"""Ephemeral resource support for terrible via tfplugin6.9 protocol.

Ephemeral resources exist only during plan/apply — never persisted to state.
Right semantic for one-shot modules (command, shell, uri reads, script) where
drift detection is meaningless.

This module provides:
  - EphemeralResource   base class for per-module ephemeral task types
  - EphemeralMixin      servicer mixin that adds the four ephemeral RPCs to
                        tf's ProviderServicer and extends GetProviderSchema
"""

import logging
from typing import Any

import grpc

from .gen import tfplugin_pb2 as _epb

# Cast to Any: protobuf descriptors are dynamic; ty cannot resolve their attributes.
epb: Any = _epb

log = logging.getLogger(__name__)


def _xproto(src, dst_cls):
    """Re-parse a protobuf message into a different class with the same wire format.

    Both tfplugin6 (from tf) and tfplugin6_9 (our gen) share identical wire
    formats — only the package name differs.  Serialising to bytes and parsing
    with the target class is the safe way to cross the proto class boundary.
    """
    obj = dst_cls()
    obj.ParseFromString(src.SerializeToString())
    return obj


def _xdiags(diags):
    """Convert tf Diagnostics to a list of epb.Diagnostic."""
    return [_xproto(d, epb.Diagnostic) for d in diags.to_pb()]


# ---------------------------------------------------------------------------
# EphemeralResource base class
# ---------------------------------------------------------------------------


class EphemeralResource:
    """Base class for dynamically-generated ephemeral task resource types.

    Subclasses set _module_name (FQCN) and _schema (Schema).
    Injected by the discovery factory (same pattern as TerribleTaskBase).
    """

    _module_name: str = ""
    _schema = None

    def __init__(self, provider):
        self._prov = provider

    @classmethod
    def get_name(cls) -> str:
        return getattr(cls, "_name", "")

    @classmethod
    def get_schema(cls):
        return cls._schema

    def validate(self, diags, config: dict):
        pass

    def open(self, diags, config: dict) -> dict:
        """Run the module and return results. Called on OpenEphemeralResource."""
        from .task_base import _build_args_str, _run_module

        host_id = config.get("host_id")
        if host_id is None:
            diags.add_error("host_id is required", "")
            return {}

        host = self._prov._state.get(host_id)
        if host is None:
            diags.add_error(
                f"Host '{host_id}' not found",
                "Ensure the terrible_host resource exists and has been applied.",
            )
            return {}

        args_str = _build_args_str(config)
        result = _run_module(host, self.__class__._module_name, args_str)

        if (result.get("failed") or result.get("unreachable")) and not config.get("ignore_errors"):
            diags.add_error("Ansible task failed", result.get("msg", "unknown error"))

        return result

    def close(self, diags, private: bytes):
        pass


# ---------------------------------------------------------------------------
# EphemeralMixin — adds ephemeral RPCs to tf's ProviderServicer
# ---------------------------------------------------------------------------


class EphemeralMixin:
    """Mixin for tf's ProviderServicer that adds ephemeral resource support.

    Overrides GetProviderSchema to include ephemeral_resource_schemas,
    and implements the four ephemeral lifecycle RPCs.

    Usage:
        class TerribleServicer(EphemeralMixin, ProviderServicer):
            pass
    """

    # set by ProviderServicer.__init__; declared here so ty can resolve it
    app: Any

    def _load_ephemeral_cls_map(self) -> dict:
        if not hasattr(self, "_ephemeral_cls_map"):
            klasses = self.app.get_ephemeral_resources() if hasattr(self.app, "get_ephemeral_resources") else []
            self._ephemeral_cls_map = {k.get_name(): k for k in klasses}
        return self._ephemeral_cls_map

    def _get_ephemeral_cls(self, type_name: str, context):
        klass = self._load_ephemeral_cls_map().get(type_name)
        if klass is None:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Unknown ephemeral resource type: {type_name}")
        return klass

    def GetProviderSchema(self, request, context):
        """Extend base GetProviderSchema to include ephemeral_resource_schemas."""
        resp = super().GetProviderSchema(request, context)  # type: ignore[misc]

        ephemeral_schemas = {
            type_name: _xproto(klass.get_schema().to_pb(), epb.Schema)
            for type_name, klass in self._load_ephemeral_cls_map().items()
            if klass.get_schema() is not None
        }

        if not ephemeral_schemas:
            return resp

        # Re-encode via 6.9 proto to include the ephemeral field.
        resp_69 = _xproto(resp, epb.GetProviderSchema.Response)
        for type_name, our_schema in ephemeral_schemas.items():
            resp_69.ephemeral_resource_schemas[type_name].CopyFrom(our_schema)
        return resp_69

    def ValidateEphemeralResourceConfig(self, request, context):
        from tf.utils import Diagnostics

        klass = self._get_ephemeral_cls(request.type_name, context)
        if klass is None:
            return epb.ValidateEphemeralResourceConfig.Response()

        from tf.utils import read_dynamic_value

        config = read_dynamic_value(request.config)
        diags = Diagnostics()
        inst = (
            self.app.new_ephemeral_resource(klass) if hasattr(self.app, "new_ephemeral_resource") else klass(self.app)
        )
        inst.validate(diags, config or {})
        return epb.ValidateEphemeralResourceConfig.Response(diagnostics=_xdiags(diags))

    def OpenEphemeralResource(self, request, context):
        from tf.utils import Diagnostics, read_dynamic_value, to_dynamic_value

        klass = self._get_ephemeral_cls(request.type_name, context)
        if klass is None:
            return epb.OpenEphemeralResource.Response()

        config = read_dynamic_value(request.config)
        diags = Diagnostics()
        inst = (
            self.app.new_ephemeral_resource(klass) if hasattr(self.app, "new_ephemeral_resource") else klass(self.app)
        )
        result = inst.open(diags, config or {})
        tf_dv = to_dynamic_value(result)
        return epb.OpenEphemeralResource.Response(
            diagnostics=_xdiags(diags),
            result=epb.DynamicValue(msgpack=tf_dv.msgpack),
        )

    def GetResourceIdentitySchemas(self, request, context):
        # terrible does not use resource identity schemas.
        return epb.GetResourceIdentitySchemas.Response()

    def UpgradeResourceIdentity(self, request, context):
        # terrible does not use resource identity.
        return epb.UpgradeResourceIdentity.Response()

    def RenewEphemeralResource(self, request, context):
        # terrible ephemeral resources don't renew — one-shot execution only.
        return epb.RenewEphemeralResource.Response()

    def CloseEphemeralResource(self, request, context):
        from tf.utils import Diagnostics

        klass = self._get_ephemeral_cls(request.type_name, context)
        if klass is None:
            return epb.CloseEphemeralResource.Response()

        diags = Diagnostics()
        inst = (
            self.app.new_ephemeral_resource(klass) if hasattr(self.app, "new_ephemeral_resource") else klass(self.app)
        )
        inst.close(diags, request.private)
        return epb.CloseEphemeralResource.Response(diagnostics=_xdiags(diags))
