"""Unit tests for TerribleTaskDataSource.

These tests mock _run_module so no real Ansible or host is required.
They focus on the encode/decode contract that differs from resources:
  - NormalizedJson inputs arrive as JSON strings and must be decoded before
    passing to Ansible.
  - NormalizedJson outputs must be re-encoded as JSON strings before returning,
    because ReadDataSource bypasses _encode_state.
"""

import json
from unittest.mock import MagicMock, patch

from tf.iface import ReadDataContext
from tf.schema import Attribute, Schema
from tf.types import NormalizedJson, String
from tf.utils import Diagnostics

from terrible_provider.task_datasource import TerribleTaskDataSource

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ds_class(attrs, return_attr_names=None, module_name="test.module"):
    """Build a minimal TerribleTaskDataSource subclass with the given schema."""
    schema = Schema(attributes=attrs)
    return_names = set(return_attr_names or [])
    return type(
        "TestDS",
        (TerribleTaskDataSource,),
        {
            "_module_name": module_name,
            "_schema": schema,
            "_return_attr_names": return_names,
            "_return_attr_coercers": {},
            "get_name": classmethod(lambda cls: "test"),
        },
    )


def _make_provider(host_state=None):
    prov = MagicMock()
    prov._state = {"host-1": host_state or {"host": "127.0.0.1", "connection": "local"}}
    return prov


def _make_ctx():
    diags = Diagnostics()
    return ReadDataContext(diags, "terrible_test"), diags


# ---------------------------------------------------------------------------
# NormalizedJson decode/encode round-trip
# ---------------------------------------------------------------------------


class TestNormalizedJsonRoundTrip:
    """NormalizedJson inputs must be decoded before Ansible; outputs re-encoded."""

    def test_dict_input_is_decoded_for_ansible(self):
        """A NormalizedJson input arrives as a JSON string; Ansible must receive a dict."""
        DSClass = _make_ds_class(
            [
                Attribute("host_id", String(), required=True),
                Attribute("extra_vars", NormalizedJson(), optional=True),
            ]
        )
        inst = DSClass(_make_provider())
        ctx, diags = _make_ctx()

        captured_args = {}

        def fake_run(host, module, args_str, *, check_only=False):
            if args_str:
                captured_args.update(json.loads(args_str))
            return {"changed": False}

        config = {
            "host_id": "host-1",
            "extra_vars": json.dumps({"key": "value"}),  # NormalizedJson wire form
        }

        with patch("terrible_provider.task_datasource._run_module", side_effect=fake_run):
            inst.read(ctx, config)

        # Ansible should have received a decoded dict, not a JSON string
        assert captured_args["extra_vars"] == {"key": "value"}
        assert not diags.has_errors()

    def test_dict_output_is_encoded_in_return(self):
        """NormalizedJson computed attributes must be JSON strings in the returned state."""
        DSClass = _make_ds_class(
            attrs=[
                Attribute("host_id", String(), required=True),
                Attribute("stat", NormalizedJson(), computed=True),
            ],
            return_attr_names=["stat"],
        )
        inst = DSClass(_make_provider())
        ctx, diags = _make_ctx()

        ansible_result = {"changed": False, "stat": {"exists": True, "path": "/tmp/x"}}

        with patch("terrible_provider.task_datasource._run_module", return_value=ansible_result):
            state = inst.read(ctx, {"host_id": "host-1"})

        assert not diags.has_errors()
        assert isinstance(state["stat"], str)
        assert json.loads(state["stat"]) == {"exists": True, "path": "/tmp/x"}

    def test_string_input_passes_through_unchanged(self):
        """Plain String inputs are not JSON-encoded and pass through as-is."""
        DSClass = _make_ds_class(
            [
                Attribute("host_id", String(), required=True),
                Attribute("path", String(), required=True),
            ]
        )
        inst = DSClass(_make_provider())
        ctx, diags = _make_ctx()

        captured_args = {}

        def fake_run(host, module, args_str, *, check_only=False):
            if args_str:
                captured_args.update(json.loads(args_str))
            return {"changed": False}

        with patch("terrible_provider.task_datasource._run_module", side_effect=fake_run):
            inst.read(ctx, {"host_id": "host-1", "path": "/tmp/foo"})

        assert captured_args["path"] == "/tmp/foo"
        assert not diags.has_errors()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_host_not_found_returns_none(self):
        DSClass = _make_ds_class(
            [
                Attribute("host_id", String(), required=True),
            ]
        )
        prov = MagicMock()
        prov._state = {}  # no hosts
        inst = DSClass(prov)
        ctx, diags = _make_ctx()

        result = inst.read(ctx, {"host_id": "missing"})

        assert result is None
        assert diags.has_errors()

    def test_ansible_failure_returns_none(self):
        DSClass = _make_ds_class(
            [
                Attribute("host_id", String(), required=True),
            ]
        )
        inst = DSClass(_make_provider())
        ctx, diags = _make_ctx()

        with patch(
            "terrible_provider.task_datasource._run_module",
            return_value={"failed": True, "msg": "module exploded"},
        ):
            result = inst.read(ctx, {"host_id": "host-1"})

        assert result is None
        assert diags.has_errors()

    def test_unreachable_returns_none(self):
        DSClass = _make_ds_class(
            [
                Attribute("host_id", String(), required=True),
            ]
        )
        inst = DSClass(_make_provider())
        ctx, diags = _make_ctx()

        with patch(
            "terrible_provider.task_datasource._run_module",
            return_value={"unreachable": True, "msg": "no route"},
        ):
            result = inst.read(ctx, {"host_id": "host-1"})

        assert result is None
        assert diags.has_errors()


# ---------------------------------------------------------------------------
# Successful read
# ---------------------------------------------------------------------------


class TestSuccessfulRead:
    def test_config_echoed_in_state(self):
        """Input config attributes are present in the returned state."""
        DSClass = _make_ds_class(
            [
                Attribute("host_id", String(), required=True),
                Attribute("ping", String(), computed=True),
            ],
            return_attr_names=["ping"],
        )
        inst = DSClass(_make_provider())
        ctx, diags = _make_ctx()

        with patch(
            "terrible_provider.task_datasource._run_module",
            return_value={"changed": False, "ping": "pong"},
        ):
            state = inst.read(ctx, {"host_id": "host-1"})

        assert not diags.has_errors()
        assert state["host_id"] == "host-1"
        assert state["ping"] == "pong"

    def test_warns_on_undocumented_keys(self):
        """Ansible result keys not in RETURN schema trigger a warning."""
        DSClass = _make_ds_class(
            [
                Attribute("host_id", String(), required=True),
                Attribute("ping", String(), computed=True),
            ],
            return_attr_names=["ping"],
        )
        inst = DSClass(_make_provider())
        ctx, diags = _make_ctx()

        with (
            patch(
                "terrible_provider.task_datasource._run_module",
                return_value={"changed": False, "ping": "pong", "undocumented": "val"},
            ),
            patch("terrible_provider.task_datasource.log") as mock_log,
        ):
            inst.read(ctx, {"host_id": "host-1"})

        mock_log.warning.assert_called_once()
        assert "undocumented" in str(mock_log.warning.call_args)

    def test_runs_in_check_mode(self):
        """_run_module must always be called with check_only=True."""
        DSClass = _make_ds_class(
            [
                Attribute("host_id", String(), required=True),
            ]
        )
        inst = DSClass(_make_provider())
        ctx, _ = _make_ctx()

        with patch("terrible_provider.task_datasource._run_module", return_value={"changed": False}) as mock_run:
            inst.read(ctx, {"host_id": "host-1"})

        _, kwargs = mock_run.call_args
        assert kwargs.get("check_only") is True

    def test_get_schema_returns_schema(self):
        DSClass = _make_ds_class([Attribute("host_id", String(), required=True)])
        assert DSClass.get_schema() is DSClass._schema
