"""Base class for dynamically-generated Ansible module data sources.

Only modules with check_mode support == "full" get a data source.  The data
source runs the module in check+diff mode and returns the result without making
any changes on the target host.
"""

import json

from tf.iface import ReadDataContext
from tf.provider import DataSource
from tf.types import Unknown

from .task_base import _run_module

# Attributes that belong to the data source framework, not the Ansible module args
_DS_SKIP_ATTRS = frozenset({"host_id", "result"})


class TerribleTaskDataSource(DataSource):
    """
    Base class for per-module data sources.

    Subclasses set `_module_name` (FQCN) and `_schema` (Schema).
    Both are injected by the discovery factory.
    """

    _module_name: str = ""
    _schema = None
    _return_attr_names: set[str] = set()
    _return_attr_coercers: dict = {}

    def __init__(self, provider):
        self._prov = provider

    @classmethod
    def get_schema(cls):
        return cls._schema

    def read(self, ctx: ReadDataContext, config: dict) -> dict | None:
        host_id = config.get("host_id")
        host = self._prov._state.get(host_id)
        if host is None:
            ctx.diagnostics.add_error(
                f"Host '{host_id}' not found",
                "Ensure the terrible_host resource exists and has been applied.",
            )
            return None

        # config arrives with NormalizedJson values as JSON strings (Terraform's wire
        # format). Decode them back to Python objects before passing to Ansible, which
        # expects dicts/lists, not JSON strings.
        attr_map = {a.name: a for a in self.__class__._schema.attributes}  # type: ignore[union-attr]
        decoded_config = {
            k: attr_map[k].type.decode(v) if k in attr_map and v not in (None, Unknown) else v
            for k, v in config.items()
        }

        args = {k: v for k, v in decoded_config.items() if k not in _DS_SKIP_ATTRS and v not in (None, Unknown)}
        args_str = json.dumps(args) if args else None

        result = _run_module(host, self.__class__._module_name, args_str, check_only=True)

        if result.get("failed") or result.get("unreachable"):
            ctx.diagnostics.add_error("Ansible module failed in check mode", result.get("msg", "unknown error"))
            return None

        coercers = self.__class__._return_attr_coercers
        return_attrs = {
            name: coercers[name](result.get(name)) if name in coercers else result.get(name)
            for name in self.__class__._return_attr_names
        }

        # Unlike resources, ReadDataSource bypasses _encode_state, so NormalizedJson
        # attributes must be pre-encoded as JSON strings before returning.
        state = {**config, **return_attrs, "result": result}
        return {
            k: attr_map[k].type.encode(v) if k in attr_map and v not in (None, Unknown) else v for k, v in state.items()
        }
