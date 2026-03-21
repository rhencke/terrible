import json
import logging
from pathlib import Path

from tf.iface import Provider
from tf.schema import Attribute, Schema
from tf.types import String
from tf.utils import Diagnostics

from .discovery import discover_task_resources
from .host import TerribleHost
from .play import TerriblePlaybook, TerribleRole
from .vault import TerribleVault

log = logging.getLogger(__name__)


class TerribleProvider(Provider):
    def __init__(self):
        self._state_file = Path("terrible_state.json")
        self._state: dict[str, dict] = {}
        self._task_resources: list | None = None
        self._task_datasources: list | None = None
        self._vault_secrets: list | None = None

    def _ensure_discovered(self):
        if self._task_resources is None:
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
        return Schema(
            attributes=[
                Attribute("state_file", String(), optional=True),
                Attribute(
                    "vault_password",
                    String(),
                    optional=True,
                    sensitive=True,
                    description="Vault password for decrypting Ansible Vault data.",
                ),
                Attribute(
                    "vault_password_file",
                    String(),
                    optional=True,
                    description="Path to a file containing the vault password.",
                ),
            ]
        )

    def full_name(self) -> str:
        return "local/terrible/terrible"

    def validate_config(self, diags: Diagnostics, config: dict):
        if config and config.get("vault_password") and config.get("vault_password_file"):
            diags.add_error(
                "vault_password and vault_password_file are mutually exclusive",
                "Set only one of vault_password or vault_password_file, not both.",
            )

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

        # Vault setup
        self._vault_secrets = None
        if config:
            password = config.get("vault_password")
            vpf = config.get("vault_password_file")
            if not password and vpf:
                try:
                    password = Path(vpf).expanduser().read_text().strip()
                except Exception as exc:
                    diags.add_error("Cannot read vault password file", str(exc))
                    return
            if password:
                from ansible.parsing.vault import VaultSecret

                self._vault_secrets = [("default", VaultSecret(password.encode("utf-8")))]

    def get_data_sources(self) -> list:
        self._ensure_discovered()
        return [TerribleVault, *self._task_datasources]  # type: ignore[misc]

    def get_resources(self) -> list:
        self._ensure_discovered()
        return [TerribleHost, TerriblePlaybook, TerribleRole, *self._task_resources]  # type: ignore[misc]
