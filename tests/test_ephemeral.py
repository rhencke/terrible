"""Unit tests for EphemeralResource and TerribleEphemeralPing."""

from unittest.mock import MagicMock, patch

from tf.schema import Schema
from tf.utils import Diagnostics

from terrible_provider.ephemeral import EphemeralResource
from terrible_provider.ephemeral_ping import TerribleEphemeralPing

# ---------------------------------------------------------------------------
# EphemeralResource base class
# ---------------------------------------------------------------------------


class TestEphemeralResource:
    def test_get_name_returns_empty_string_by_default(self):
        assert EphemeralResource.get_name() == ""

    def test_get_schema_returns_none_by_default(self):
        assert EphemeralResource.get_schema() is None

    def test_validate_is_noop(self):
        prov = MagicMock()
        inst = EphemeralResource(prov)
        diags = Diagnostics()
        inst.validate(diags, {})
        assert not diags.has_errors()

    def test_close_is_noop(self):
        prov = MagicMock()
        inst = EphemeralResource(prov)
        diags = Diagnostics()
        inst.close(diags, b"")
        assert not diags.has_errors()

    def test_open_requires_host_id(self):
        prov = MagicMock()
        inst = EphemeralResource(prov)
        diags = Diagnostics()
        result = inst.open(diags, {})
        assert diags.has_errors()
        assert result == {}

    def test_open_errors_on_unknown_host(self):
        prov = MagicMock()
        prov._state = {}
        inst = EphemeralResource(prov)
        diags = Diagnostics()
        result = inst.open(diags, {"host_id": "nonexistent"})
        assert diags.has_errors()
        assert result == {}

    def test_open_runs_module_and_returns_result(self):
        prov = MagicMock()
        host = {"host": "127.0.0.1", "connection": "local"}
        prov._state = {"h1": host}
        inst = EphemeralResource(prov)
        inst.__class__ = type(
            "Eph_test",
            (EphemeralResource,),
            {"_module_name": "ansible.builtin.ping", "_return_attr_names": {"ping"}},
        )
        diags = Diagnostics()
        with (
            patch("terrible_provider.task_base._run_module", return_value={"ping": "pong"}),
            patch("terrible_provider.task_base._build_args_str", return_value=""),
        ):
            result = inst.open(diags, {"host_id": "h1"})
        assert not diags.has_errors()
        assert result == {"ping": "pong"}

    def test_open_filters_result_by_return_attr_names(self):
        """open() should only include attrs listed in _return_attr_names, not internal Ansible keys."""
        prov = MagicMock()
        host = {"host": "127.0.0.1", "connection": "local"}
        prov._state = {"h1": host}
        inst = EphemeralResource(prov)
        inst.__class__ = type(
            "Eph_test",
            (EphemeralResource,),
            {"_module_name": "ansible.builtin.ping", "_return_attr_names": {"ping"}},
        )
        diags = Diagnostics()
        with (
            patch(
                "terrible_provider.task_base._run_module",
                return_value={"ping": "pong", "changed": False, "_ansible_verbose_always": True},
            ),
            patch("terrible_provider.task_base._build_args_str", return_value=""),
        ):
            result = inst.open(diags, {"host_id": "h1"})
        assert result == {"ping": "pong"}

    def test_open_passes_execution_params_to_run_module(self):
        """open() should forward failed_when, environment, delegate_to_id."""
        prov = MagicMock()
        host = {"host": "127.0.0.1", "connection": "local"}
        delegate_host = {"host": "10.0.0.2", "connection": "local"}
        prov._state = {"h1": host, "h2": delegate_host}
        inst = EphemeralResource(prov)
        inst.__class__ = type(
            "Eph_test",
            (EphemeralResource,),
            {"_module_name": "ansible.builtin.ping", "_return_attr_names": set()},
        )
        diags = Diagnostics()
        config = {
            "host_id": "h1",
            "failed_when": "rc != 0",
            "environment": '{"PATH": "/usr/bin"}',
            "delegate_to_id": "h2",
        }
        with (
            patch("terrible_provider.task_base._run_module", return_value={}) as mock_run,
            patch("terrible_provider.task_base._build_args_str", return_value=""),
        ):
            inst.open(diags, config)
        kwargs = mock_run.call_args[1]
        assert kwargs.get("failed_when") == "rc != 0"
        assert kwargs.get("environment") == '{"PATH": "/usr/bin"}'
        assert "tags" not in kwargs
        assert "skip_tags" not in kwargs

    def test_open_adds_error_on_failure(self):
        prov = MagicMock()
        host = {"host": "127.0.0.1", "connection": "local"}
        prov._state = {"h1": host}
        inst = EphemeralResource(prov)
        inst.__class__ = type(
            "Eph_test", (EphemeralResource,), {"_module_name": "ansible.builtin.ping", "_return_attr_names": set()}
        )
        diags = Diagnostics()
        with (
            patch("terrible_provider.task_base._run_module", return_value={"failed": True, "msg": "boom"}),
            patch("terrible_provider.task_base._build_args_str", return_value=""),
        ):
            inst.open(diags, {"host_id": "h1"})
        assert diags.has_errors()

    def test_open_ignore_errors_suppresses_error(self):
        prov = MagicMock()
        host = {"host": "127.0.0.1", "connection": "local"}
        prov._state = {"h1": host}
        inst = EphemeralResource(prov)
        inst.__class__ = type(
            "Eph_test", (EphemeralResource,), {"_module_name": "ansible.builtin.ping", "_return_attr_names": set()}
        )
        diags = Diagnostics()
        with (
            patch("terrible_provider.task_base._run_module", return_value={"failed": True, "msg": "boom"}),
            patch("terrible_provider.task_base._build_args_str", return_value=""),
        ):
            inst.open(diags, {"host_id": "h1", "ignore_errors": True})
        assert not diags.has_errors()


# ---------------------------------------------------------------------------
# TerribleEphemeralPing
# ---------------------------------------------------------------------------


class TestTerribleEphemeralPing:
    def test_get_name(self):
        assert TerribleEphemeralPing.get_name() == "ephemeral_ping"

    def test_get_schema_returns_schema(self):
        schema = TerribleEphemeralPing.get_schema()
        assert isinstance(schema, Schema)

    def test_open_returns_pong(self):
        prov = MagicMock()
        inst = TerribleEphemeralPing(prov)
        diags = Diagnostics()
        result = inst.open(diags, {})
        assert result == {"greeting": "pong"}
        assert not diags.has_errors()

    def test_close_is_noop(self):
        prov = MagicMock()
        inst = TerribleEphemeralPing(prov)
        diags = Diagnostics()
        inst.close(diags, b"")
        assert not diags.has_errors()
