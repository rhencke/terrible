"""TerribleVault data source — decrypt Ansible Vault ciphertext."""

from __future__ import annotations

from tf.iface import DataSource, ReadContext
from tf.schema import Attribute, Schema
from tf.types import String


class TerribleVault(DataSource):
    _schema = Schema(
        attributes=[
            Attribute("id", String(), description="Data source ID (set to 'vault').", computed=True),
            Attribute(
                "ciphertext",
                String(),
                description="Ansible Vault encrypted string (the $ANSIBLE_VAULT;... blob).",
                required=True,
            ),
            Attribute(
                "plaintext",
                String(),
                description="Decrypted plaintext value.",
                computed=True,
                sensitive=True,
            ),
        ]
    )

    def __init__(self, provider):
        self._prov = provider

    @classmethod
    def get_name(cls):
        return "vault"

    @classmethod
    def get_schema(cls):
        return cls._schema

    def read(self, ctx: ReadContext, config: dict) -> dict | None:  # type: ignore[override]
        ciphertext = config.get("ciphertext", "")
        if not self._prov._vault_secrets:
            ctx.diagnostics.add_error(
                "No vault password configured",
                "Set vault_password or vault_password_file on the terrible provider to decrypt vault data.",
            )
            return None

        from ansible.parsing.vault import VaultLib

        vault = VaultLib(secrets=self._prov._vault_secrets)
        try:
            plaintext = vault.decrypt(ciphertext).decode("utf-8")
        except Exception as exc:
            ctx.diagnostics.add_error("Vault decryption failed", str(exc))
            return None

        return {
            "id": "vault",
            "ciphertext": ciphertext,
            "plaintext": plaintext,
        }
