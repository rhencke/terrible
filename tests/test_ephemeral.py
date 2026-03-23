"""Unit tests for EphemeralResource, EphemeralMixin, and TerribleEphemeralPing."""

from unittest.mock import MagicMock, patch

import grpc
from tf.schema import Schema
from tf.utils import Diagnostics

from terrible_provider.ephemeral import EphemeralMixin, EphemeralResource
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
        inst.__class__ = type("Eph_test", (EphemeralResource,), {"_module_name": "ansible.builtin.ping"})
        diags = Diagnostics()
        with (
            patch("terrible_provider.task_base._run_module", return_value={"ping": "pong"}),
            patch("terrible_provider.task_base._build_args_str", return_value=""),
        ):
            result = inst.open(diags, {"host_id": "h1"})
        assert not diags.has_errors()
        assert result == {"ping": "pong"}

    def test_open_adds_error_on_failure(self):
        prov = MagicMock()
        host = {"host": "127.0.0.1", "connection": "local"}
        prov._state = {"h1": host}
        inst = EphemeralResource(prov)
        inst.__class__ = type("Eph_test", (EphemeralResource,), {"_module_name": "ansible.builtin.ping"})
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
        inst.__class__ = type("Eph_test", (EphemeralResource,), {"_module_name": "ansible.builtin.ping"})
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
        assert TerribleEphemeralPing.get_name() == "terrible_ephemeral_ping"

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


# ---------------------------------------------------------------------------
# EphemeralMixin
# ---------------------------------------------------------------------------


def _make_servicer(ephemeral_classes=None):
    """Build a minimal servicer that mixes in EphemeralMixin."""
    import tf.gen.tfplugin_pb2 as tf_pb

    app = MagicMock()
    app.get_ephemeral_resources.return_value = (
        [TerribleEphemeralPing] if ephemeral_classes is None else ephemeral_classes
    )

    def new_ephemeral_resource(klass):
        return klass(app)

    app.new_ephemeral_resource = new_ephemeral_resource

    # Real protobuf response so _xproto can serialize it.
    base_resp = tf_pb.GetProviderSchema.Response()

    class _Base:
        def GetProviderSchema(self, request, context):
            return base_resp

    class _Servicer(EphemeralMixin, _Base):
        pass

    svc = _Servicer()
    svc.app = app
    return svc


class TestEphemeralMixin:
    def test_load_ephemeral_cls_map_uses_app(self):
        svc = _make_servicer([TerribleEphemeralPing])
        cls_map = svc._load_ephemeral_cls_map()
        assert "terrible_ephemeral_ping" in cls_map
        assert cls_map["terrible_ephemeral_ping"] is TerribleEphemeralPing

    def test_load_ephemeral_cls_map_cached(self):
        svc = _make_servicer([TerribleEphemeralPing])
        map1 = svc._load_ephemeral_cls_map()
        map2 = svc._load_ephemeral_cls_map()
        assert map1 is map2

    def test_load_ephemeral_cls_map_no_app_method(self):
        app = MagicMock(spec=[])  # no get_ephemeral_resources attribute
        svc = EphemeralMixin()
        svc.app = app
        cls_map = svc._load_ephemeral_cls_map()
        assert cls_map == {}

    def test_get_ephemeral_cls_returns_none_and_sets_grpc_error_for_unknown(self):
        svc = _make_servicer([TerribleEphemeralPing])
        context = MagicMock()
        result = svc._get_ephemeral_cls("terrible_ephemeral_missing", context)
        assert result is None
        context.set_code.assert_called_once_with(grpc.StatusCode.NOT_FOUND)

    def test_get_ephemeral_cls_returns_class_for_known(self):
        svc = _make_servicer([TerribleEphemeralPing])
        context = MagicMock()
        result = svc._get_ephemeral_cls("terrible_ephemeral_ping", context)
        assert result is TerribleEphemeralPing

    def test_get_provider_schema_includes_ephemeral_schemas(self):
        svc = _make_servicer([TerribleEphemeralPing])

        request = MagicMock()
        context = MagicMock()
        resp = svc.GetProviderSchema(request, context)
        assert "terrible_ephemeral_ping" in resp.ephemeral_resource_schemas

    def test_get_provider_schema_passthrough_when_no_ephemeral(self):
        svc = _make_servicer([])
        request = MagicMock()
        context = MagicMock()
        import tf.gen.tfplugin_pb2 as tf_pb

        resp = svc.GetProviderSchema(request, context)
        # No ephemeral classes → returns the base tf proto response unchanged
        assert isinstance(resp, tf_pb.GetProviderSchema.Response)

    def test_validate_ephemeral_resource_config_unknown_type(self):
        svc = _make_servicer()
        request = MagicMock()
        request.type_name = "terrible_ephemeral_missing"
        context = MagicMock()
        resp = svc.ValidateEphemeralResourceConfig(request, context)
        assert resp is not None

    def test_validate_ephemeral_resource_config_known_type(self):
        svc = _make_servicer()
        from tf.utils import to_dynamic_value

        request = MagicMock()
        request.type_name = "terrible_ephemeral_ping"
        request.config = to_dynamic_value({})
        context = MagicMock()
        resp = svc.ValidateEphemeralResourceConfig(request, context)
        assert resp is not None

    def test_open_ephemeral_resource_returns_pong(self):
        svc = _make_servicer()
        from tf.utils import to_dynamic_value

        request = MagicMock()
        request.type_name = "terrible_ephemeral_ping"
        request.config = to_dynamic_value({})
        context = MagicMock()
        resp = svc.OpenEphemeralResource(request, context)
        assert resp is not None

    def test_open_ephemeral_resource_unknown_type(self):
        svc = _make_servicer()
        request = MagicMock()
        request.type_name = "terrible_ephemeral_missing"
        context = MagicMock()
        resp = svc.OpenEphemeralResource(request, context)
        assert resp is not None

    def test_get_resource_identity_schemas_returns_empty(self):
        svc = _make_servicer()
        request = MagicMock()
        context = MagicMock()
        resp = svc.GetResourceIdentitySchemas(request, context)
        assert resp is not None

    def test_upgrade_resource_identity_returns_empty(self):
        svc = _make_servicer()
        request = MagicMock()
        context = MagicMock()
        resp = svc.UpgradeResourceIdentity(request, context)
        assert resp is not None

    def test_renew_ephemeral_resource_is_noop(self):
        svc = _make_servicer()
        request = MagicMock()
        context = MagicMock()
        resp = svc.RenewEphemeralResource(request, context)
        assert resp is not None

    def test_close_ephemeral_resource_unknown_type(self):
        svc = _make_servicer()
        request = MagicMock()
        request.type_name = "terrible_ephemeral_missing"
        context = MagicMock()
        resp = svc.CloseEphemeralResource(request, context)
        assert resp is not None

    def test_close_ephemeral_resource_known_type(self):
        svc = _make_servicer()
        request = MagicMock()
        request.type_name = "terrible_ephemeral_ping"
        request.private = b""
        context = MagicMock()
        resp = svc.CloseEphemeralResource(request, context)
        assert resp is not None
