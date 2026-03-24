import logging

from tf.iface import Provider
from tf.schema import Schema
from tf.utils import Diagnostics

from .discovery import discover_task_resources
from .ephemeral_ping import TerribleEphemeralPing
from .host import TerribleHost

log = logging.getLogger(__name__)


class TerribleProvider(Provider):
    def __init__(self):
        self._state: dict[str, dict] = {}
        self._task_resources: list | None = None
        self._task_datasources: list | None = None
        self._task_ephemerals: list | None = None

    def _ensure_discovered(self):
        if self._task_resources is None:
            self._task_resources, self._task_datasources, self._task_ephemerals = discover_task_resources()

    def get_model_prefix(self) -> str:
        return "terrible_"

    def get_provider_schema(self, diags: Diagnostics) -> Schema:
        return Schema(attributes=[])

    def full_name(self) -> str:
        return "registry.terraform.io/rhencke/terrible"

    def validate_config(self, diags: Diagnostics, config: dict):
        pass

    def configure_provider(self, diags: Diagnostics, config: dict):
        pass

    def get_data_sources(self) -> list:
        self._ensure_discovered()
        return [*self._task_datasources]  # type: ignore[misc]

    def get_resources(self) -> list:
        self._ensure_discovered()
        return [TerribleHost, *self._task_resources]  # type: ignore[misc]

    def get_ephemeral_resources(self) -> list:
        self._ensure_discovered()
        return [TerribleEphemeralPing, *self._task_ephemerals]  # type: ignore[misc]
