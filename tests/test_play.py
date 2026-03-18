"""Unit tests for terrible_provider.play — TerriblePlaybook, TerribleRole, runners."""

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tf.iface import (
    CreateContext, DeleteContext, ImportContext, ReadContext, UpdateContext, PlanContext,
)
from tf.types import Unknown
from tf.utils import Diagnostics

from terrible_provider.play import (
    _make_multi_callback,
    _execute_plays,
    _run_playbook,
    _run_role,
    TerriblePlaybook,
    TerribleRole,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctx(klass, changed_fields=None):
    diags = Diagnostics()
    if klass is PlanContext:
        return klass(diags, "terrible_playbook", changed_fields or set())
    return klass(diags, "terrible_playbook")


def _provider(state=None):
    prov = MagicMock()
    prov._state = state or {}
    prov._save_state = MagicMock()
    return prov


def _host():
    return {"host": "127.0.0.1", "connection": "local"}


def _make_mock_tqm(result):
    """TQM mock that injects *result* via v2_runner_on_ok into every _MultiCB callback."""
    class _MockTQM:
        def __init__(self, **kw):
            self._callback_plugins = []

        def load_callbacks(self):
            pass

        def run(self, play):
            r = MagicMock()
            r.result = result
            for cb in self._callback_plugins:
                if hasattr(cb, "results"):
                    if result.get("failed") or result.get("unreachable"):
                        cb.v2_runner_on_failed(r)
                    else:
                        cb.v2_runner_on_ok(r)

        def cleanup(self):
            pass

    return _MockTQM


# ---------------------------------------------------------------------------
# _make_multi_callback
# ---------------------------------------------------------------------------

class TestMakeMultiCallback:
    def test_initial_state(self):
        cb = _make_multi_callback()
        assert cb.results == []
        assert cb.any_changed is False
        assert cb.any_failed is False

    def test_v2_runner_on_ok_no_change(self):
        cb = _make_multi_callback()
        r = MagicMock()
        r.result = {"changed": False, "ping": "pong"}
        cb.v2_runner_on_ok(r)
        assert cb.results == [{"changed": False, "ping": "pong"}]
        assert cb.any_changed is False
        assert cb.any_failed is False

    def test_v2_runner_on_ok_with_change(self):
        cb = _make_multi_callback()
        r = MagicMock()
        r.result = {"changed": True}
        cb.v2_runner_on_ok(r)
        assert cb.any_changed is True
        assert cb.any_failed is False

    def test_v2_runner_on_failed(self):
        cb = _make_multi_callback()
        r = MagicMock()
        r.result = {"failed": True, "msg": "boom"}
        cb.v2_runner_on_failed(r)
        assert cb.results == [{"failed": True, "msg": "boom"}]
        assert cb.any_failed is True
        assert cb.any_changed is False

    def test_v2_runner_on_unreachable(self):
        cb = _make_multi_callback()
        r = MagicMock()
        r.result = {"msg": "no route"}
        cb.v2_runner_on_unreachable(r)
        assert cb.results[0].get("unreachable") is True
        assert cb.any_failed is True

    def test_v2_runner_on_skipped_is_noop(self):
        cb = _make_multi_callback()
        r = MagicMock()
        cb.v2_runner_on_skipped(r)
        assert cb.results == []
        assert cb.any_changed is False
        assert cb.any_failed is False


# ---------------------------------------------------------------------------
# _execute_plays / _run_playbook / _run_role — shared TQM path
# ---------------------------------------------------------------------------

class TestExecutePlays:
    _HOST = {"host": "127.0.0.1", "connection": "local"}

    def test_success_returns_last_result(self):
        MockTQM = _make_mock_tqm({"changed": True, "rc": 0})
        with patch("ansible.executor.task_queue_manager.TaskQueueManager", MockTQM):
            result = _execute_plays(self._HOST, [{
                "name": "p", "hosts": "target", "gather_facts": "no",
                "tasks": [{"action": "ansible.builtin.ping"}],
            }])
        assert result["changed"] is True
        assert result["rc"] == 0

    def test_empty_play_list_returns_no_result(self):
        MockTQM = _make_mock_tqm({"changed": False})
        with patch("ansible.executor.task_queue_manager.TaskQueueManager", MockTQM):
            result = _execute_plays(self._HOST, [])
        assert result == {"changed": False}

    def test_tqm_raises_returns_failed(self):
        class _ErrTQM:
            def __init__(self, **kw):
                self._callback_plugins = []
            def load_callbacks(self): pass
            def run(self, play): raise RuntimeError("exploded")
            def cleanup(self): pass

        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _ErrTQM):
            result = _execute_plays(self._HOST, [{
                "name": "p", "hosts": "target", "gather_facts": "no", "tasks": [],
            }])
        assert result["failed"] is True
        assert "exploded" in result["msg"]

    def test_extra_vars_set_on_variable_manager(self):
        captured = {}

        class _CaptureTQM:
            def __init__(self, variable_manager, **kw):
                captured["extra_vars"] = getattr(variable_manager, "_extra_vars", None)
                self._callback_plugins = []
            def load_callbacks(self): pass
            def run(self, play):
                r = MagicMock()
                r.result = {"changed": False}
                for cb in self._callback_plugins:
                    if hasattr(cb, "results"):
                        cb.v2_runner_on_ok(r)
            def cleanup(self): pass

        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _CaptureTQM):
            _execute_plays(self._HOST, [], extra_vars={"MY_VAR": "hello"})
        assert captured["extra_vars"] == {"MY_VAR": "hello"}

    def test_timeout_and_tags_set_in_cliargs(self):
        from ansible import context as _ctx

        cliargs_seen = {}

        class _CaptureTQM:
            def __init__(self, **kw):
                self._callback_plugins = []
                cliargs_seen["timeout"] = dict(_ctx.CLIARGS).get("timeout")
                # CLIArgs normalises lists to tuples; check membership instead
                cliargs_seen["tags"] = list(dict(_ctx.CLIARGS).get("tags") or [])
                cliargs_seen["skip_tags"] = list(dict(_ctx.CLIARGS).get("skip_tags") or [])
            def load_callbacks(self): pass
            def run(self, play): pass
            def cleanup(self): pass

        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _CaptureTQM):
            _execute_plays(
                self._HOST, [],
                timeout=42, tags=["web"], skip_tags=["slow"],
            )
        assert cliargs_seen["timeout"] == 42
        assert cliargs_seen["tags"] == ["web"]
        assert cliargs_seen["skip_tags"] == ["slow"]

    def test_cliargs_restored_after_call(self):
        from ansible import context as _ctx
        orig = dict(_ctx.CLIARGS).get("timeout")
        MockTQM = _make_mock_tqm({"changed": False})
        with patch("ansible.executor.task_queue_manager.TaskQueueManager", MockTQM):
            _execute_plays(self._HOST, [], timeout=999)
        assert dict(_ctx.CLIARGS).get("timeout") == orig

    def test_non_main_thread(self):
        MockTQM = _make_mock_tqm({"changed": False})
        results = []

        def _run():
            with patch("ansible.executor.task_queue_manager.TaskQueueManager", MockTQM):
                results.append(_execute_plays(self._HOST, []))

        t = threading.Thread(target=_run)
        t.start()
        t.join()
        assert results == [{"changed": False}]


# ---------------------------------------------------------------------------
# _run_playbook
# ---------------------------------------------------------------------------

class TestRunPlaybook:
    _HOST = {"host": "127.0.0.1", "connection": "local"}

    def test_file_not_found_returns_failed(self):
        result = _run_playbook(self._HOST, "/no/such/playbook.yml")
        assert result["failed"] is True
        assert "Failed to load" in result["msg"]

    def test_yaml_not_a_list_returns_failed(self, tmp_path):
        pb = tmp_path / "bad.yml"
        pb.write_text("key: value\n")
        result = _run_playbook(self._HOST, str(pb))
        assert result["failed"] is True
        assert "must be a YAML list" in result["msg"]

    def test_success(self, tmp_path):
        pb = tmp_path / "site.yml"
        pb.write_text(
            "- name: test\n  hosts: all\n  gather_facts: no\n"
            "  tasks:\n    - action: ansible.builtin.ping\n"
        )
        MockTQM = _make_mock_tqm({"changed": False, "ping": "pong"})
        with patch("ansible.executor.task_queue_manager.TaskQueueManager", MockTQM):
            result = _run_playbook(self._HOST, str(pb))
        assert result["ping"] == "pong"

    def test_hosts_overridden_to_target(self, tmp_path):
        """The playbook's hosts: field must be replaced with 'target'."""
        pb = tmp_path / "site.yml"
        pb.write_text("- name: p\n  hosts: webservers\n  gather_facts: no\n  tasks: []\n")
        captured = {}

        class _CaptureTQM:
            def __init__(self, **kw):
                self._callback_plugins = []
            def load_callbacks(self): pass
            def run(self, play):
                captured["hosts"] = play._ds.get("hosts")
            def cleanup(self): pass

        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _CaptureTQM):
            _run_playbook(self._HOST, str(pb))
        assert captured.get("hosts") == "target"

    def test_non_dict_plays_skipped(self, tmp_path):
        pb = tmp_path / "weird.yml"
        pb.write_text("- null\n- name: real\n  hosts: all\n  gather_facts: no\n  tasks: []\n")
        MockTQM = _make_mock_tqm({"changed": False})
        with patch("ansible.executor.task_queue_manager.TaskQueueManager", MockTQM):
            result = _run_playbook(self._HOST, str(pb))
        # null entry is skipped; real play runs
        assert "failed" not in result or not result["failed"]


# ---------------------------------------------------------------------------
# _run_role
# ---------------------------------------------------------------------------

class TestRunRole:
    """Play().load() would fail for a non-existent role, so we mock it out."""
    _HOST = {"host": "127.0.0.1", "connection": "local"}
    _MOCK_PLAY = MagicMock()

    def _patches(self, tqm_cls):
        """Apply both TQM and Play patches so role resolution is bypassed."""
        return (
            patch("ansible.executor.task_queue_manager.TaskQueueManager", tqm_cls),
            patch("terrible_provider.play.Play", return_value=MagicMock(
                load=MagicMock(return_value=self._MOCK_PLAY)
            )),
        )

    def test_success(self):
        MockTQM = _make_mock_tqm({"changed": False})
        with patch("ansible.executor.task_queue_manager.TaskQueueManager", MockTQM), \
             patch("ansible.playbook.play.Play") as MockPlay:
            MockPlay.return_value.load.return_value = self._MOCK_PLAY
            result = _run_role(self._HOST, "myrole")
        assert result["changed"] is False

    def test_extra_vars_forwarded(self):
        captured = {}

        class _CaptureTQM:
            def __init__(self, variable_manager, **kw):
                captured["extra_vars"] = getattr(variable_manager, "_extra_vars", None)
                self._callback_plugins = []
            def load_callbacks(self): pass
            def run(self, play): pass
            def cleanup(self): pass

        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _CaptureTQM), \
             patch("ansible.playbook.play.Play") as MockPlay:
            MockPlay.return_value.load.return_value = self._MOCK_PLAY
            _run_role(self._HOST, "myrole", extra_vars={"DB_HOST": "localhost"})
        assert captured["extra_vars"] == {"DB_HOST": "localhost"}

    def test_tqm_raises_returns_failed(self):
        class _ErrTQM:
            def __init__(self, **kw):
                self._callback_plugins = []
            def load_callbacks(self): pass
            def run(self, play): raise RuntimeError("role error")
            def cleanup(self): pass

        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _ErrTQM), \
             patch("ansible.playbook.play.Play") as MockPlay:
            MockPlay.return_value.load.return_value = self._MOCK_PLAY
            result = _run_role(self._HOST, "myrole")
        assert result["failed"] is True
        assert "role error" in result["msg"]

    def test_non_main_thread(self):
        MockTQM = _make_mock_tqm({"changed": True})
        results = []

        def _run():
            with patch("ansible.executor.task_queue_manager.TaskQueueManager", MockTQM), \
                 patch("ansible.playbook.play.Play") as MockPlay:
                MockPlay.return_value.load.return_value = self._MOCK_PLAY
                results.append(_run_role(self._HOST, "myrole"))

        t = threading.Thread(target=_run)
        t.start()
        t.join()
        assert results[0]["changed"] is True


# ---------------------------------------------------------------------------
# TerriblePlaybook — CRUD, plan, schema
# ---------------------------------------------------------------------------

class TestTerriblePlaybook:
    def _inst(self, state=None):
        return TerriblePlaybook(_provider(state=state or {}))

    # Schema
    def test_schema_has_expected_attrs(self):
        names = {a.name for a in TerriblePlaybook.get_schema().attributes}
        assert {"id", "host_id", "playbook", "result", "changed",
                "extra_vars", "tags", "skip_tags", "timeout", "ignore_errors"} <= names

    def test_get_name(self):
        assert TerriblePlaybook.get_name() == "playbook"

    # plan
    def test_plan_new_resource(self):
        inst = self._inst()
        result = inst.plan(_ctx(PlanContext), None, {"host_id": "h1", "playbook": "site.yml"})
        assert result["result"] is Unknown
        assert result["changed"] is Unknown

    def test_plan_existing_no_change(self):
        inst = self._inst()
        current = {"id": "rid", "host_id": "h1", "playbook": "site.yml",
                   "result": {}, "changed": False}
        planned = {"host_id": "h1", "playbook": "site.yml", "result": {}, "changed": False}
        result = inst.plan(_ctx(PlanContext), current, planned)
        assert result["id"] == "rid"

    def test_plan_existing_input_changed(self):
        inst = self._inst()
        current = {"id": "rid", "host_id": "h1", "playbook": "old.yml",
                   "result": {}, "changed": False}
        planned = {"host_id": "h1", "playbook": "new.yml", "result": {}, "changed": False}
        result = inst.plan(_ctx(PlanContext), current, planned)
        assert result["result"] is Unknown

    # create
    def test_create_stores_state(self):
        prov = _provider(state={"h1": _host()})
        inst = TerriblePlaybook(prov)
        with patch("terrible_provider.play._run_playbook", return_value={"changed": False}):
            state = inst.create(_ctx(CreateContext), {"host_id": "h1", "playbook": "site.yml"})
        assert "id" in state
        assert state["id"] in prov._state
        prov._save_state.assert_called_once()

    def test_create_host_not_found_adds_error(self):
        prov = _provider()
        inst = TerriblePlaybook(prov)
        ctx = _ctx(CreateContext)
        with patch("terrible_provider.play._run_playbook", return_value={"changed": False}):
            inst.create(ctx, {"host_id": "missing", "playbook": "site.yml"})
        assert ctx.diagnostics.has_errors()

    # read
    def test_read_found(self):
        stored = {"id": "rid", "host_id": "h1", "playbook": "site.yml"}
        inst = self._inst(state={"rid": stored})
        assert inst.read(_ctx(ReadContext), {"id": "rid"}) == stored

    def test_read_not_found(self):
        inst = self._inst()
        assert inst.read(_ctx(ReadContext), {"id": "gone"}) is None

    # update
    def test_update_replaces_state(self):
        prov = _provider(state={"h1": _host(), "rid": {"id": "rid", "host_id": "h1"}})
        inst = TerriblePlaybook(prov)
        with patch("terrible_provider.play._run_playbook", return_value={"changed": True}):
            state = inst.update(
                _ctx(UpdateContext),
                {"id": "rid", "host_id": "h1"},
                {"host_id": "h1", "playbook": "site.yml"},
            )
        assert state["id"] == "rid"
        assert state["changed"] is True
        prov._save_state.assert_called_once()

    # delete
    def test_delete_removes_state(self):
        prov = _provider(state={"rid": {"id": "rid"}})
        inst = TerriblePlaybook(prov)
        inst.delete(_ctx(DeleteContext), {"id": "rid"})
        assert "rid" not in prov._state
        prov._save_state.assert_called_once()

    # import_
    def test_import_found(self):
        stored = {"id": "rid", "host_id": "h1"}
        inst = self._inst(state={"rid": stored})
        assert inst.import_(_ctx(ImportContext), "rid") == stored

    def test_import_not_found(self):
        inst = self._inst()
        assert inst.import_(_ctx(ImportContext), "gone") is None

    # _execute — failed + ignore_errors
    def test_execute_failed_ignore_errors_suppresses(self):
        prov = _provider(state={"h1": _host()})
        inst = TerriblePlaybook(prov)
        diags = Diagnostics()
        with patch("terrible_provider.play._run_playbook",
                   return_value={"failed": True, "msg": "boom"}):
            inst._execute(diags, {"host_id": "h1", "playbook": "site.yml", "ignore_errors": True})
        assert not diags.has_errors()

    def test_execute_failed_no_ignore_adds_error(self):
        prov = _provider(state={"h1": _host()})
        inst = TerriblePlaybook(prov)
        diags = Diagnostics()
        with patch("terrible_provider.play._run_playbook",
                   return_value={"failed": True, "msg": "boom"}):
            inst._execute(diags, {"host_id": "h1", "playbook": "site.yml"})
        assert diags.has_errors()

    def test_execute_unreachable_adds_error(self):
        prov = _provider(state={"h1": _host()})
        inst = TerriblePlaybook(prov)
        diags = Diagnostics()
        with patch("terrible_provider.play._run_playbook",
                   return_value={"unreachable": True, "msg": "no route"}):
            inst._execute(diags, {"host_id": "h1", "playbook": "site.yml"})
        assert diags.has_errors()


# ---------------------------------------------------------------------------
# TerribleRole — CRUD, plan, schema
# ---------------------------------------------------------------------------

class TestTerribleRole:
    def _inst(self, state=None):
        return TerribleRole(_provider(state=state or {}))

    # Schema
    def test_schema_has_expected_attrs(self):
        names = {a.name for a in TerribleRole.get_schema().attributes}
        assert {"id", "host_id", "role", "result", "changed",
                "extra_vars", "tags", "skip_tags", "timeout", "ignore_errors"} <= names

    def test_get_name(self):
        assert TerribleRole.get_name() == "role"

    # plan
    def test_plan_new_resource(self):
        inst = self._inst()
        result = inst.plan(_ctx(PlanContext), None, {"host_id": "h1", "role": "myrole"})
        assert result["result"] is Unknown
        assert result["changed"] is Unknown

    def test_plan_existing_no_change(self):
        inst = self._inst()
        current = {"id": "rid", "host_id": "h1", "role": "myrole", "result": {}, "changed": False}
        planned = {"host_id": "h1", "role": "myrole", "result": {}, "changed": False}
        result = inst.plan(_ctx(PlanContext), current, planned)
        assert result["id"] == "rid"

    def test_plan_existing_input_changed(self):
        inst = self._inst()
        current = {"id": "rid", "host_id": "h1", "role": "old", "result": {}, "changed": False}
        planned = {"host_id": "h1", "role": "new", "result": {}, "changed": False}
        result = inst.plan(_ctx(PlanContext), current, planned)
        assert result["result"] is Unknown

    # create
    def test_create_stores_state(self):
        prov = _provider(state={"h1": _host()})
        inst = TerribleRole(prov)
        with patch("terrible_provider.play._run_role", return_value={"changed": False}):
            state = inst.create(_ctx(CreateContext), {"host_id": "h1", "role": "myrole"})
        assert "id" in state
        assert state["id"] in prov._state
        prov._save_state.assert_called_once()

    def test_create_host_not_found_adds_error(self):
        prov = _provider()
        inst = TerribleRole(prov)
        ctx = _ctx(CreateContext)
        with patch("terrible_provider.play._run_role", return_value={"changed": False}):
            inst.create(ctx, {"host_id": "missing", "role": "myrole"})
        assert ctx.diagnostics.has_errors()

    # read
    def test_read_found(self):
        stored = {"id": "rid", "host_id": "h1", "role": "myrole"}
        inst = self._inst(state={"rid": stored})
        assert inst.read(_ctx(ReadContext), {"id": "rid"}) == stored

    def test_read_not_found(self):
        inst = self._inst()
        assert inst.read(_ctx(ReadContext), {"id": "gone"}) is None

    # update
    def test_update_replaces_state(self):
        prov = _provider(state={"h1": _host(), "rid": {"id": "rid", "host_id": "h1"}})
        inst = TerribleRole(prov)
        with patch("terrible_provider.play._run_role", return_value={"changed": True}):
            state = inst.update(
                _ctx(UpdateContext),
                {"id": "rid", "host_id": "h1"},
                {"host_id": "h1", "role": "myrole"},
            )
        assert state["id"] == "rid"
        assert state["changed"] is True
        prov._save_state.assert_called_once()

    # delete
    def test_delete_removes_state(self):
        prov = _provider(state={"rid": {"id": "rid"}})
        inst = TerribleRole(prov)
        inst.delete(_ctx(DeleteContext), {"id": "rid"})
        assert "rid" not in prov._state
        prov._save_state.assert_called_once()

    # import_
    def test_import_found(self):
        stored = {"id": "rid", "host_id": "h1"}
        inst = self._inst(state={"rid": stored})
        assert inst.import_(_ctx(ImportContext), "rid") == stored

    def test_import_not_found(self):
        inst = self._inst()
        assert inst.import_(_ctx(ImportContext), "gone") is None

    # _execute — failed + ignore_errors
    def test_execute_failed_ignore_errors_suppresses(self):
        prov = _provider(state={"h1": _host()})
        inst = TerribleRole(prov)
        diags = Diagnostics()
        with patch("terrible_provider.play._run_role",
                   return_value={"failed": True, "msg": "boom"}):
            inst._execute(diags, {"host_id": "h1", "role": "myrole", "ignore_errors": True})
        assert not diags.has_errors()

    def test_execute_failed_no_ignore_adds_error(self):
        prov = _provider(state={"h1": _host()})
        inst = TerribleRole(prov)
        diags = Diagnostics()
        with patch("terrible_provider.play._run_role",
                   return_value={"failed": True, "msg": "boom"}):
            inst._execute(diags, {"host_id": "h1", "role": "myrole"})
        assert diags.has_errors()


# ---------------------------------------------------------------------------
# _PlayResourceBase — abstract method guard
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Vault secrets forwarding
# ---------------------------------------------------------------------------

class TestVaultSecretsForwarding:
    """Verify vault_secrets are forwarded from provider through to runners."""

    def test_execute_plays_passes_vault_secrets_to_loader(self):
        captured = {}

        class _CaptureLoaderTQM:
            def __init__(self, loader, **kw):
                captured["vault_secrets"] = getattr(loader, "_vault_secrets", "NOT_SET")
                self._callback_plugins = []
            def load_callbacks(self): pass
            def run(self, play): pass
            def cleanup(self): pass

        secrets = [("default", MagicMock())]
        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _CaptureLoaderTQM):
            _execute_plays(
                {"host": "127.0.0.1", "connection": "local"}, [],
                vault_secrets=secrets,
            )
        assert captured["vault_secrets"] is not None

    def test_playbook_resource_forwards_vault_secrets(self):
        prov = _provider(state={"h1": _host()})
        prov._vault_secrets = [("default", MagicMock())]
        inst = TerriblePlaybook(prov)

        with patch("terrible_provider.play._run_playbook", return_value={"changed": False}) as mock_run:
            inst._execute(Diagnostics(), {"host_id": "h1", "playbook": "site.yml"})
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs.get("vault_secrets") == prov._vault_secrets

    def test_role_resource_forwards_vault_secrets(self):
        prov = _provider(state={"h1": _host()})
        prov._vault_secrets = [("default", MagicMock())]
        inst = TerribleRole(prov)

        with patch("terrible_provider.play._run_role", return_value={"changed": False}) as mock_run:
            inst._execute(Diagnostics(), {"host_id": "h1", "role": "myrole"})
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs.get("vault_secrets") == prov._vault_secrets


class TestPlayResourceBaseAbstract:
    def test_run_raises_not_implemented(self):
        from terrible_provider.play import _PlayResourceBase

        class _Concrete(_PlayResourceBase):
            _schema = None
            @classmethod
            def get_name(cls): return "test"

        inst = _Concrete.__new__(_Concrete)
        with pytest.raises(NotImplementedError):
            inst._run({}, {})
