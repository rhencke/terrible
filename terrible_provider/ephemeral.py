"""Base class for dynamically-generated ephemeral task resource types.

Ephemeral resources exist only during plan/apply — never persisted to state.
Right semantic for one-shot modules (command, shell, uri reads, script) where
drift detection is meaningless.
"""

import logging

log = logging.getLogger(__name__)


class EphemeralResource:
    """Base class for dynamically-generated ephemeral task resource types.

    Subclasses set _module_name (FQCN) and _schema (Schema).
    Injected by the discovery factory (same pattern as TerribleTaskBase).
    """

    _module_name: str = ""
    _schema = None
    _return_attr_names: set = set()
    _return_attr_coercers: dict = {}

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

        delegate_host = None
        if config.get("delegate_to_id"):
            delegate_host = self._prov._state.get(config["delegate_to_id"])

        args_str = _build_args_str(config)
        result = _run_module(
            host,
            self.__class__._module_name,
            args_str,
            timeout=config.get("timeout"),
            failed_when=config.get("failed_when"),
            environment=config.get("environment"),
            delegate_host_state=delegate_host,
        )

        if (result.get("failed") or result.get("unreachable")) and not config.get("ignore_errors"):
            diags.add_error("Ansible task failed", result.get("msg", "unknown error"))

        coercers = self.__class__._return_attr_coercers
        return {
            name: coercers[name](result.get(name)) if name in coercers else result.get(name)
            for name in self.__class__._return_attr_names
        }

    def close(self, diags, private: bytes):
        pass
