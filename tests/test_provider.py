"""Unit tests for TerribleProvider."""

from unittest.mock import MagicMock, patch

from tf.utils import Diagnostics

from terrible_provider.host import TerribleHost
from terrible_provider.provider import TerribleProvider


def _diags():
    return Diagnostics()


class TestConfigure:
    def test_configure_is_noop(self):
        prov = TerribleProvider.__new__(TerribleProvider)
        prov._state = {}
        prov.configure_provider(_diags(), {})
        assert prov._state == {}


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

    def test_get_ephemeral_resources_returns_ping(self):
        from terrible_provider.ephemeral_ping import TerribleEphemeralPing

        prov = TerribleProvider.__new__(TerribleProvider)
        assert TerribleEphemeralPing in prov.get_ephemeral_resources()

    def test_get_data_sources_excludes_vault(self):
        prov = TerribleProvider.__new__(TerribleProvider)
        prov._task_resources = None
        prov._task_datasources = None
        with patch("terrible_provider.provider.discover_task_resources", return_value=([], [])):
            datasources = prov.get_data_sources()
        assert not any(getattr(ds, "__name__", "") == "TerribleVault" for ds in datasources)

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

    def test_get_provider_schema_has_no_attributes(self):
        prov = TerribleProvider.__new__(TerribleProvider)
        schema = prov.get_provider_schema(_diags())
        assert schema.attributes == []

    def test_validate_config_is_noop(self):
        prov = TerribleProvider.__new__(TerribleProvider)
        diags = _diags()
        prov.validate_config(diags, {})
        assert not diags.has_errors()


class TestInit:
    def test_init_starts_with_empty_state(self):
        with patch("terrible_provider.provider.discover_task_resources", return_value=([], [])):
            prov = TerribleProvider()
        assert prov._state == {}
        assert prov._task_resources is None
        assert prov._task_datasources is None
        assert not hasattr(prov, "_vault_secrets")  # vault removed
