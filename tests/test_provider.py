"""Unit tests for TerribleProvider."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from tf.utils import Diagnostics

from terrible_provider.host import TerribleHost
from terrible_provider.provider import TerribleProvider
from terrible_provider.vault import TerribleVault


def _diags():
    return Diagnostics()


class TestStateIO:
    def test_load_state_reads_json(self, tmp_path):
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({"abc": {"id": "abc", "host": "1.2.3.4"}}))
        prov = TerribleProvider.__new__(TerribleProvider)
        prov._state_file = sf
        prov._state = {}
        prov._load_state()
        assert prov._state["abc"]["host"] == "1.2.3.4"

    def test_load_state_missing_file_starts_empty(self, tmp_path):
        prov = TerribleProvider.__new__(TerribleProvider)
        prov._state_file = tmp_path / "nonexistent.json"
        prov._state = {}
        prov._load_state()
        assert prov._state == {}

    def test_load_state_corrupt_file_starts_empty(self, tmp_path):
        sf = tmp_path / "state.json"
        sf.write_text("not json{{")
        prov = TerribleProvider.__new__(TerribleProvider)
        prov._state_file = sf
        prov._state = {}
        prov._load_state()
        assert prov._state == {}

    def test_save_state_writes_json(self, tmp_path):
        sf = tmp_path / "state.json"
        prov = TerribleProvider.__new__(TerribleProvider)
        prov._state_file = sf
        prov._state = {"abc": {"id": "abc"}}
        prov._save_state()
        assert json.loads(sf.read_text()) == {"abc": {"id": "abc"}}


class TestConfigure:
    def test_configure_uses_default_state_file(self, tmp_path):
        prov = TerribleProvider.__new__(TerribleProvider)
        prov._state_file = tmp_path / "state.json"
        prov._state = {}
        prov.configure_provider(_diags(), {})
        assert prov._state == {}

    def test_configure_overrides_state_file(self, tmp_path):
        sf = tmp_path / "custom.json"
        prov = TerribleProvider.__new__(TerribleProvider)
        prov._state_file = tmp_path / "default.json"
        prov._state = {}
        prov.configure_provider(_diags(), {"state_file": str(sf)})
        assert prov._state_file == sf

    def test_configure_creates_parent_dir(self, tmp_path):
        sf = tmp_path / "subdir" / "state.json"
        prov = TerribleProvider.__new__(TerribleProvider)
        prov._state_file = sf
        prov._state = {}
        prov.configure_provider(_diags(), {})
        assert sf.parent.exists()

    def test_configure_mkdir_failure_logs_warning(self, tmp_path, caplog):
        import logging

        prov = TerribleProvider.__new__(TerribleProvider)
        # Use a non-existent subdir so configure_provider tries to call mkdir
        prov._state_file = tmp_path / "newdir" / "state.json"
        prov._state = {}
        with patch.object(Path, "mkdir", side_effect=OSError("denied")):
            with caplog.at_level(logging.WARNING, logger="terrible_provider.provider"):
                prov.configure_provider(_diags(), {})
        assert any("denied" in r.message for r in caplog.records)


class TestGetResourcesAndDataSources:
    def test_get_resources_includes_host(self):
        prov = TerribleProvider.__new__(TerribleProvider)
        prov._task_resources = None
        prov._task_datasources = None
        with patch("terrible_provider.provider.discover_task_resources", return_value=([], [])):
            resources = prov.get_resources()
        assert TerribleHost in resources

    def test_get_resources_includes_task_resources(self):
        prov = TerribleProvider.__new__(TerribleProvider)
        prov._task_resources = None
        prov._task_datasources = None
        fake_task = MagicMock()
        with patch("terrible_provider.provider.discover_task_resources", return_value=([fake_task], [])):
            resources = prov.get_resources()
        assert fake_task in resources

    def test_get_data_sources_returns_discovered(self):
        prov = TerribleProvider.__new__(TerribleProvider)
        prov._task_resources = None
        prov._task_datasources = None
        fake_ds = MagicMock()
        with patch("terrible_provider.provider.discover_task_resources", return_value=([], [fake_ds])):
            datasources = prov.get_data_sources()
        assert fake_ds in datasources

    def test_discovery_runs_only_once(self):
        prov = TerribleProvider.__new__(TerribleProvider)
        prov._task_resources = None
        prov._task_datasources = None
        with patch("terrible_provider.provider.discover_task_resources", return_value=([], [])) as mock_disc:
            prov.get_resources()
            prov.get_resources()
            prov.get_data_sources()
        mock_disc.assert_called_once()

    def test_full_name(self):
        prov = TerribleProvider.__new__(TerribleProvider)
        assert prov.full_name() == "local/terrible/terrible"

    def test_model_prefix(self):
        prov = TerribleProvider.__new__(TerribleProvider)
        assert prov.get_model_prefix() == "terrible_"

    def test_get_provider_schema_has_state_file_attr(self):
        prov = TerribleProvider.__new__(TerribleProvider)
        schema = prov.get_provider_schema(_diags())
        names = {a.name for a in schema.attributes}
        assert "state_file" in names

    def test_validate_config_is_noop(self):
        prov = TerribleProvider.__new__(TerribleProvider)
        diags = _diags()
        prov.validate_config(diags, {})
        assert not diags.has_errors()

    def test_provider_schema_has_vault_attrs(self):
        prov = TerribleProvider.__new__(TerribleProvider)
        schema = prov.get_provider_schema(_diags())
        names = {a.name for a in schema.attributes}
        assert "vault_password" in names
        assert "vault_password_file" in names

    def test_vault_password_attr_is_sensitive(self):
        prov = TerribleProvider.__new__(TerribleProvider)
        schema = prov.get_provider_schema(_diags())
        attrs = {a.name: a for a in schema.attributes}
        assert attrs["vault_password"].sensitive is True

    def test_get_data_sources_includes_vault(self):
        prov = TerribleProvider.__new__(TerribleProvider)
        prov._task_resources = None
        prov._task_datasources = None
        with patch("terrible_provider.provider.discover_task_resources", return_value=([], [])):
            datasources = prov.get_data_sources()
        assert TerribleVault in datasources


class TestVaultConfiguration:
    def test_validate_mutual_exclusivity(self):
        prov = TerribleProvider.__new__(TerribleProvider)
        diags = _diags()
        prov.validate_config(diags, {"vault_password": "pw", "vault_password_file": "/f"})
        assert diags.has_errors()

    def test_validate_vault_password_only_ok(self):
        prov = TerribleProvider.__new__(TerribleProvider)
        diags = _diags()
        prov.validate_config(diags, {"vault_password": "pw"})
        assert not diags.has_errors()

    def test_validate_vault_password_file_only_ok(self):
        prov = TerribleProvider.__new__(TerribleProvider)
        diags = _diags()
        prov.validate_config(diags, {"vault_password_file": "/some/file"})
        assert not diags.has_errors()

    def test_configure_vault_password(self, tmp_path):
        prov = TerribleProvider.__new__(TerribleProvider)
        prov._state_file = tmp_path / "state.json"
        prov._state = {}
        prov.configure_provider(_diags(), {"vault_password": "mysecret"})
        assert prov._vault_secrets is not None
        assert len(prov._vault_secrets) == 1
        assert prov._vault_secrets[0][0] == "default"

    def test_configure_vault_password_file(self, tmp_path):
        vpf = tmp_path / "vaultpass.txt"
        vpf.write_text("file_secret\n")
        prov = TerribleProvider.__new__(TerribleProvider)
        prov._state_file = tmp_path / "state.json"
        prov._state = {}
        prov.configure_provider(_diags(), {"vault_password_file": str(vpf)})
        assert prov._vault_secrets is not None

    def test_configure_vault_password_file_missing(self, tmp_path):
        prov = TerribleProvider.__new__(TerribleProvider)
        prov._state_file = tmp_path / "state.json"
        prov._state = {}
        diags = _diags()
        prov.configure_provider(diags, {"vault_password_file": "/nonexistent/vault.txt"})
        assert diags.has_errors()

    def test_configure_no_vault(self, tmp_path):
        prov = TerribleProvider.__new__(TerribleProvider)
        prov._state_file = tmp_path / "state.json"
        prov._state = {}
        prov.configure_provider(_diags(), {})
        assert prov._vault_secrets is None


class TestInit:
    def test_default_state_file(self):
        with patch("terrible_provider.provider.discover_task_resources", return_value=([], [])):
            prov = TerribleProvider()
        assert prov._state_file.name == "terrible_state.json"
        assert prov._state == {}
        assert prov._task_resources is None
        assert prov._task_datasources is None


class TestSaveStateError:
    def test_save_state_logs_error_on_failure(self, tmp_path, caplog):
        import logging

        prov = TerribleProvider.__new__(TerribleProvider)
        prov._state_file = tmp_path / "state.json"
        prov._state = {"x": {}}
        with patch.object(Path, "write_text", side_effect=OSError("no space")):
            with caplog.at_level(logging.ERROR, logger="terrible_provider.provider"):
                prov._save_state()
        assert any("no space" in r.message for r in caplog.records)
