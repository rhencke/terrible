import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

from tf.schema import Schema, Attribute
from tf.types import String
from tf.utils import Diagnostics
from tf.iface import Provider

from .host import TerribleHost
from .discovery import discover_task_resources


class TerribleProvider(Provider):
    def __init__(self):
        self._state_file = Path("terrible_state.json")
        self._state: dict[str, dict] = {}
        self._task_resources, self._task_datasources = discover_task_resources()

    def _load_state(self):
        if self._state_file.exists():
            try:
                self._state = json.loads(self._state_file.read_text())
            except Exception as exc:
                log.warning("Could not load state from %s: %s — starting empty", self._state_file, exc)
                self._state = {}

    def _save_state(self):
        try:
            self._state_file.write_text(json.dumps(self._state, indent=2, sort_keys=True))
        except Exception as exc:
            log.error("Failed to persist state to %s: %s", self._state_file, exc)

    def get_model_prefix(self) -> str:
        return "terrible_"

    def get_provider_schema(self, diags: Diagnostics) -> Schema:
        return Schema(attributes=[Attribute("state_file", String(), optional=True)])

    def full_name(self) -> str:
        return "local/terrible/terrible"

    def validate_config(self, diags: Diagnostics, config: dict):
        # No validation needed: state_file is an optional free-form path with no
        # constraints that can be checked before the filesystem is accessed.
        pass

    def configure_provider(self, diags: Diagnostics, config: dict):
        sf = config.get("state_file") if config else None
        if sf:
            self._state_file = Path(sf)
        if not self._state_file.parent.exists():
            try:
                self._state_file.parent.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                log.warning("Could not create state file directory %s: %s", self._state_file.parent, exc)
        self._load_state()

    def get_data_sources(self) -> list:
        return self._task_datasources

    def get_resources(self) -> list:
        return [TerribleHost, *self._task_resources]
