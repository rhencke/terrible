"""Unit tests for TerribleHost resource."""

from unittest.mock import MagicMock

from tf.iface import CreateContext, DeleteContext, ImportContext, ReadContext, UpdateContext
from tf.utils import Diagnostics

from terrible_provider.host import TerribleHost


def _ctx(klass):
    return klass(Diagnostics(), "terrible_host")


def _provider():
    prov = MagicMock()
    prov._state = {}
    prov._save_state = MagicMock()
    return prov


class TestTerribleHost:
    def test_get_name(self):
        assert TerribleHost.get_name() == "host"

    def test_get_schema_has_expected_attrs(self):
        names = {a.name for a in TerribleHost.get_schema().attributes}
        assert names == {
            "id",
            "host",
            "port",
            "user",
            "private_key_path",
            "connection",
            "ssh_extra_args",
            "become",
            "become_user",
            "become_method",
            "become_password",
            "winrm_port",
            "winrm_scheme",
            "winrm_transport",
            "winrm_server_cert_validation",
            "vars",
        }

    def test_become_password_is_sensitive(self):
        attrs = {a.name: a for a in TerribleHost.get_schema().attributes}
        assert attrs["become_password"].sensitive

    def test_private_key_path_is_sensitive(self):
        attrs = {a.name: a for a in TerribleHost.get_schema().attributes}
        assert attrs["private_key_path"].sensitive

    def test_create_stores_become_fields(self):
        prov = _provider()
        inst = TerribleHost(prov)
        state = inst.create(
            _ctx(CreateContext),
            {
                "host": "10.0.0.1",
                "become": True,
                "become_user": "root",
                "become_method": "sudo",
                "become_password": "s3cr3t",
            },
        )
        assert state["become"] is True
        assert state["become_user"] == "root"

    def test_create_stores_vars(self):
        prov = _provider()
        inst = TerribleHost(prov)
        state = inst.create(
            _ctx(CreateContext),
            {
                "host": "10.0.0.1",
                "vars": {"ansible_python_interpreter": "/usr/bin/python3.11"},
            },
        )
        assert state["vars"] == {"ansible_python_interpreter": "/usr/bin/python3.11"}

    def test_create_stores_ssh_extra_args(self):
        prov = _provider()
        inst = TerribleHost(prov)
        state = inst.create(
            _ctx(CreateContext),
            {
                "host": "10.0.0.1",
                "ssh_extra_args": "-o ProxyJump=bastion",
            },
        )
        assert state["ssh_extra_args"] == "-o ProxyJump=bastion"

    def test_create_assigns_id(self):
        prov = _provider()
        inst = TerribleHost(prov)
        state = inst.create(_ctx(CreateContext), {"host": "10.0.0.1"})
        assert "id" in state
        assert len(state["id"]) == 32  # uuid4 hex

    def test_create_defaults_port(self):
        prov = _provider()
        inst = TerribleHost(prov)
        state = inst.create(_ctx(CreateContext), {"host": "10.0.0.1", "port": None})
        assert state["port"] == 22

    def test_create_preserves_explicit_port(self):
        prov = _provider()
        inst = TerribleHost(prov)
        state = inst.create(_ctx(CreateContext), {"host": "10.0.0.1", "port": 2222})
        assert state["port"] == 2222

    def test_create_saves_to_provider_state(self):
        prov = _provider()
        inst = TerribleHost(prov)
        state = inst.create(_ctx(CreateContext), {"host": "10.0.0.1"})
        assert state["id"] in prov._state
        prov._save_state.assert_called_once()

    def test_read_returns_stored_state(self):
        prov = _provider()
        prov._state["abc"] = {"id": "abc", "host": "10.0.0.1"}
        inst = TerribleHost(prov)
        result = inst.read(_ctx(ReadContext), {"id": "abc"})
        assert result == {"id": "abc", "host": "10.0.0.1"}

    def test_read_returns_none_when_missing(self):
        prov = _provider()
        inst = TerribleHost(prov)
        assert inst.read(_ctx(ReadContext), {"id": "nonexistent"}) is None

    def test_update_replaces_state(self):
        prov = _provider()
        prov._state["abc"] = {"id": "abc", "host": "old"}
        inst = TerribleHost(prov)
        result = inst.update(_ctx(UpdateContext), {"id": "abc"}, {"host": "new", "port": 22})
        assert result["host"] == "new"
        assert result["id"] == "abc"
        assert prov._state["abc"]["host"] == "new"
        prov._save_state.assert_called_once()

    def test_delete_removes_from_state(self):
        prov = _provider()
        prov._state["abc"] = {"id": "abc"}
        inst = TerribleHost(prov)
        inst.delete(_ctx(DeleteContext), {"id": "abc"})
        assert "abc" not in prov._state
        prov._save_state.assert_called_once()

    def test_delete_missing_id_is_safe(self):
        prov = _provider()
        inst = TerribleHost(prov)
        inst.delete(_ctx(DeleteContext), {"id": None})  # no crash

    def test_import_returns_state_by_id(self):
        prov = _provider()
        prov._state["abc"] = {"id": "abc", "host": "10.0.0.1"}
        inst = TerribleHost(prov)
        result = inst.import_(_ctx(ImportContext), "abc")
        assert result == {"id": "abc", "host": "10.0.0.1"}

    def test_import_returns_none_when_missing(self):
        prov = _provider()
        inst = TerribleHost(prov)
        assert inst.import_(_ctx(ImportContext), "gone") is None

    def test_create_stores_winrm_fields(self):
        prov = _provider()
        inst = TerribleHost(prov)
        state = inst.create(
            _ctx(CreateContext),
            {
                "host": "10.0.0.1",
                "connection": "winrm",
                "winrm_port": 5985,
                "winrm_scheme": "http",
                "winrm_transport": "ntlm",
                "winrm_server_cert_validation": "ignore",
            },
        )
        assert state["winrm_port"] == 5985
        assert state["winrm_scheme"] == "http"
        assert state["winrm_transport"] == "ntlm"
        assert state["winrm_server_cert_validation"] == "ignore"
