"""Unit tests for terrible_provider.vault — TerribleVault data source."""

from unittest.mock import MagicMock, patch

import pytest

from tf.iface import ReadContext
from tf.utils import Diagnostics

from terrible_provider.vault import TerribleVault


def _ctx():
    return ReadContext(Diagnostics(), "terrible_vault")


def _provider(vault_secrets=None):
    prov = MagicMock()
    prov._vault_secrets = vault_secrets
    return prov


class TestTerribleVaultSchema:
    def test_name(self):
        assert TerribleVault.get_name() == "vault"

    def test_schema_has_expected_attrs(self):
        names = {a.name for a in TerribleVault.get_schema().attributes}
        assert {"id", "ciphertext", "plaintext"} == names

    def test_plaintext_is_sensitive(self):
        attrs = {a.name: a for a in TerribleVault.get_schema().attributes}
        assert attrs["plaintext"].sensitive is True

    def test_plaintext_is_computed(self):
        attrs = {a.name: a for a in TerribleVault.get_schema().attributes}
        assert attrs["plaintext"].computed is True

    def test_ciphertext_is_required(self):
        attrs = {a.name: a for a in TerribleVault.get_schema().attributes}
        assert attrs["ciphertext"].required is True


class TestTerribleVaultRead:
    def _encrypt(self, plaintext, password):
        """Encrypt plaintext using Ansible Vault."""
        from ansible.parsing.vault import VaultLib, VaultSecret
        secrets = [("default", VaultSecret(password.encode("utf-8")))]
        vault = VaultLib(secrets=secrets)
        return vault.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def test_decrypt_success(self):
        password = "testpassword123"
        plaintext = "super secret value"
        ciphertext = self._encrypt(plaintext, password)

        from ansible.parsing.vault import VaultSecret
        secrets = [("default", VaultSecret(password.encode("utf-8")))]

        prov = _provider(vault_secrets=secrets)
        inst = TerribleVault(prov)
        ctx = _ctx()
        result = inst.read(ctx, {"ciphertext": ciphertext})

        assert result is not None
        assert result["plaintext"] == plaintext
        assert result["ciphertext"] == ciphertext
        assert result["id"] == "vault"
        assert not ctx.diagnostics.has_errors()

    def test_no_vault_password_configured(self):
        prov = _provider(vault_secrets=None)
        inst = TerribleVault(prov)
        ctx = _ctx()
        result = inst.read(ctx, {"ciphertext": "$ANSIBLE_VAULT;1.1;AES256\nabcdef"})

        assert result is None
        assert ctx.diagnostics.has_errors()

    def test_bad_ciphertext(self):
        from ansible.parsing.vault import VaultSecret
        secrets = [("default", VaultSecret(b"password"))]

        prov = _provider(vault_secrets=secrets)
        inst = TerribleVault(prov)
        ctx = _ctx()
        result = inst.read(ctx, {"ciphertext": "not valid vault data"})

        assert result is None
        assert ctx.diagnostics.has_errors()

    def test_wrong_password(self):
        ciphertext = self._encrypt("secret", "correct_password")

        from ansible.parsing.vault import VaultSecret
        secrets = [("default", VaultSecret(b"wrong_password"))]

        prov = _provider(vault_secrets=secrets)
        inst = TerribleVault(prov)
        ctx = _ctx()
        result = inst.read(ctx, {"ciphertext": ciphertext})

        assert result is None
        assert ctx.diagnostics.has_errors()

    def test_empty_ciphertext_with_no_secrets(self):
        prov = _provider(vault_secrets=None)
        inst = TerribleVault(prov)
        ctx = _ctx()
        result = inst.read(ctx, {"ciphertext": ""})

        assert result is None
        assert ctx.diagnostics.has_errors()
