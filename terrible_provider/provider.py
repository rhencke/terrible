import json
from pathlib import Path

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
        self._task_resources = discover_task_resources()

    def _load_state(self):
        if self._state_file.exists():
            try:
                self._state = json.loads(self._state_file.read_text())
            except Exception:
                self._state = {}

    def _save_state(self):
        try:
            self._state_file.write_text(json.dumps(self._state, indent=2, sort_keys=True))
        except Exception:
            pass

    def get_model_prefix(self) -> str:
        return "terrible_"

    def get_provider_schema(self, diags: Diagnostics) -> Schema:
        return Schema(attributes=[Attribute("state_file", String(), optional=True)])

    def full_name(self) -> str:
        return "local/terrible/terrible"

    def validate_config(self, diags: Diagnostics, config: dict):
        pass

    def configure_provider(self, diags: Diagnostics, config: dict):
        sf = config.get("state_file") if config else None
        if sf:
            self._state_file = Path(sf)
        if not self._state_file.parent.exists():
            try:
                self._state_file.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
        self._load_state()

    def get_data_sources(self) -> list:
        return []

    def get_resources(self) -> list:
        return [TerribleHost, *self._task_resources]
