"""Unit tests for TerribleTaskBase resource methods."""

import json
import sys
import threading
from unittest.mock import MagicMock, patch

from tf.iface import (
    CreateContext,
    DeleteContext,
    ImportContext,
    PlanContext,
    ReadContext,
    UpdateContext,
)
from tf.types import Unknown
from tf.utils import Diagnostics

from terrible_provider.discovery import _coerce_number, make_task_class
from terrible_provider.task_base import (
    _build_args_str,
    _ensure_ansible_initialized,
    _ensure_collection_finder,
    _make_callback,
    _run_module,
    _setup_host_inventory,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(klass, changed_fields=None):
    diags = Diagnostics()
    if klass is PlanContext:
        return klass(diags, "terrible_test", changed_fields or set())
    return klass(diags, "terrible_test")


def _make_class(options=None, returns=None, check_mode="none"):
    return make_task_class(
        "ansible.builtin.test_mod",
        options or {},
        returns or {},
        check_mode_support=check_mode,
    )


def _provider(state=None):
    prov = MagicMock()
    prov._state = state or {}
    prov._save_state = MagicMock()
    return prov


def _host():
    return {"host": "127.0.0.1", "connection": "local"}


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class TestBuildArgsStr:
    def test_basic(self):
        s = _build_args_str({"id": "x", "host_id": "y", "path": "/tmp/f"})
        assert json.loads(s) == {"path": "/tmp/f"}

    def test_skips_framework_attrs(self):
        s = _build_args_str({"id": "1", "host_id": "2", "result": {}, "changed": True, "triggers": None})
        assert s is None

    def test_skips_new_framework_attrs(self):
        s = _build_args_str(
            {
                "timeout": 300,
                "ignore_errors": True,
                "changed_when": "false",
                "failed_when": "rc != 0",
                "environment": {"FOO": "bar"},
                "tags": ["deploy"],
                "skip_tags": ["slow"],
                "async_seconds": 600,
                "poll_interval": 10,
                "delegate_to_id": "h2",
            }
        )
        assert s is None

    def test_skips_none_values(self):
        s = _build_args_str({"path": None, "mode": "0644"})
        assert json.loads(s) == {"mode": "0644"}

    def test_skips_unknown(self):
        s = _build_args_str({"path": Unknown, "mode": "0644"})
        assert json.loads(s) == {"mode": "0644"}

    def test_empty_returns_none(self):
        assert _build_args_str({"id": "1"}) is None


class TestCoerceNumber:
    def test_int_string(self):
        assert _coerce_number("42") == 42

    def test_float_string(self):
        assert _coerce_number("3.14") == 3.14

    def test_int(self):
        assert _coerce_number(7) == 7

    def test_none(self):
        assert _coerce_number(None) is None

    def test_invalid(self):
        assert _coerce_number("not-a-number") is None


# ---------------------------------------------------------------------------
# TerribleTaskBase.plan
# ---------------------------------------------------------------------------


class TestGetSchema:
    def test_returns_schema(self):
        klass = _make_class(options={"path": {"type": "str"}})
        assert klass.get_schema() is klass._schema


class TestPlan:
    def _make_instance(self, **kw):
        klass = _make_class(**kw)
        return klass(_provider())

    def test_new_resource_marks_outputs_unknown(self):
        klass = _make_class(returns={"rc": {"type": "int"}})
        inst = klass(_provider())
        result = inst.plan(_ctx(PlanContext), None, {"host_id": "h1"})
        assert result["rc"] is Unknown
        assert result["result"] is Unknown
        assert result["changed"] is Unknown

    def test_existing_no_change_returns_current(self):
        inst = _make_class()(_provider())
        current = {"id": "x", "host_id": "h1", "result": {}, "changed": False}
        planned = {"host_id": "h1", "result": {}, "changed": False}
        result = inst.plan(_ctx(PlanContext), current, planned)
        assert result["id"] == "x"

    def test_existing_input_changed_marks_unknown(self):
        klass = _make_class(
            options={"path": {"type": "str"}},
            returns={"rc": {"type": "int"}},
        )
        inst = klass(_provider())
        current = {"id": "x", "host_id": "h1", "path": "/old", "rc": 0, "result": {}, "changed": False}
        planned = {"host_id": "h1", "path": "/new", "result": {}, "changed": False}
        result = inst.plan(_ctx(PlanContext), current, planned)
        assert result["rc"] is Unknown


# ---------------------------------------------------------------------------
# TerribleTaskBase._resolve_host
# ---------------------------------------------------------------------------


class TestResolveHost:
    def test_found(self):
        prov = _provider(state={"h1": _host()})
        inst = _make_class()(prov)
        diags = Diagnostics()
        host = inst._resolve_host("h1", diags)
        assert host == _host()
        assert not diags.has_errors()

    def test_not_found_adds_error(self):
        prov = _provider()
        inst = _make_class()(prov)
        diags = Diagnostics()
        host = inst._resolve_host("missing", diags)
        assert host is None
        assert diags.has_errors()


# ---------------------------------------------------------------------------
# TerribleTaskBase create / update / delete / read / import_
# ---------------------------------------------------------------------------


class TestCRUD:
    _RESULT = {"changed": False, "rc": 0}

    def test_create_stores_state(self):
        klass = _make_class(returns={"rc": {"type": "int"}})
        prov = _provider(state={"h1": _host()})
        inst = klass(prov)
        with patch("terrible_provider.task_base._run_module", return_value=self._RESULT):
            state = inst.create(_ctx(CreateContext), {"host_id": "h1"})
        assert "id" in state
        assert state["id"] in prov._state
        prov._save_state.assert_called_once()

    def test_create_host_not_found_adds_error(self):
        klass = _make_class()
        prov = _provider()
        inst = klass(prov)
        ctx = _ctx(CreateContext)
        with patch("terrible_provider.task_base._run_module", return_value=self._RESULT):
            inst.create(ctx, {"host_id": "missing"})
        assert ctx.diagnostics.has_errors()

    def test_update_replaces_state(self):
        klass = _make_class(returns={"rc": {"type": "int"}})
        prov = _provider(state={"h1": _host(), "rid": {"id": "rid", "host_id": "h1"}})
        inst = klass(prov)
        with patch("terrible_provider.task_base._run_module", return_value=self._RESULT):
            state = inst.update(_ctx(UpdateContext), {"id": "rid", "host_id": "h1"}, {"host_id": "h1"})
        assert state["id"] == "rid"
        prov._save_state.assert_called_once()

    def test_delete_removes_state(self):
        klass = _make_class()
        prov = _provider(state={"rid": {"id": "rid"}})
        inst = klass(prov)
        inst.delete(_ctx(DeleteContext), {"id": "rid"})
        assert "rid" not in prov._state
        prov._save_state.assert_called_once()

    def test_import_returns_by_id(self):
        klass = _make_class()
        prov = _provider(state={"rid": {"id": "rid", "host_id": "h1"}})
        inst = klass(prov)
        result = inst.import_(_ctx(ImportContext), "rid")
        assert result == {"id": "rid", "host_id": "h1"}

    def test_import_returns_none_when_missing(self):
        klass = _make_class()
        prov = _provider()
        inst = klass(prov)
        assert inst.import_(_ctx(ImportContext), "gone") is None


# ---------------------------------------------------------------------------
# TerribleTaskBase.read — drift detection
# ---------------------------------------------------------------------------


class TestRead:
    def test_read_returns_stored_state(self):
        klass = _make_class()
        stored = {"id": "rid", "host_id": "h1"}
        prov = _provider(state={"rid": stored})
        inst = klass(prov)
        result = inst.read(_ctx(ReadContext), {"id": "rid"})
        assert result == stored

    def test_read_returns_none_when_not_in_state(self):
        klass = _make_class()
        prov = _provider()
        inst = klass(prov)
        assert inst.read(_ctx(ReadContext), {"id": "gone"}) is None

    def test_read_no_check_mode_returns_stored(self):
        klass = _make_class(check_mode="none")
        stored = {"id": "rid", "host_id": "h1", "result": {}, "changed": False}
        prov = _provider(state={"h1": _host(), "rid": stored})
        inst = klass(prov)
        result = inst.read(_ctx(ReadContext), {"id": "rid"})
        assert result == stored

    def test_read_check_mode_no_drift_returns_stored(self):
        klass = _make_class(check_mode="full")
        stored = {"id": "rid", "host_id": "h1", "result": {}, "changed": False}
        prov = _provider(state={"h1": _host(), "rid": stored})
        inst = klass(prov)
        with patch("terrible_provider.task_base._run_module", return_value={"changed": False}):
            result = inst.read(_ctx(ReadContext), {"id": "rid"})
        assert result == stored

    def test_read_check_mode_drift_clears_outputs(self):
        klass = _make_class(returns={"rc": {"type": "int"}}, check_mode="full")
        stored = {"id": "rid", "host_id": "h1", "rc": 0, "result": {}, "changed": False}
        prov = _provider(state={"h1": _host(), "rid": stored})
        inst = klass(prov)
        with patch("terrible_provider.task_base._run_module", return_value={"changed": True}):
            result = inst.read(_ctx(ReadContext), {"id": "rid"})
        assert result["rc"] is None
        assert result["result"] is None
        assert result["changed"] is None

    def test_read_check_mode_failed_returns_stored_with_warning(self):
        klass = _make_class(check_mode="full")
        stored = {"id": "rid", "host_id": "h1", "result": {}, "changed": False}
        prov = _provider(state={"h1": _host(), "rid": stored})
        inst = klass(prov)
        with patch("terrible_provider.task_base._run_module", return_value={"failed": True, "msg": "oops"}):
            result = inst.read(_ctx(ReadContext), {"id": "rid"})
        assert result == stored

    def test_read_check_mode_host_error_returns_stored(self):
        klass = _make_class(check_mode="full")
        stored = {"id": "rid", "host_id": "missing", "result": {}, "changed": False}
        prov = _provider(state={"rid": stored})  # host NOT in state
        inst = klass(prov)
        result = inst.read(_ctx(ReadContext), {"id": "rid"})
        assert result == stored


# ---------------------------------------------------------------------------
# _execute error paths
# ---------------------------------------------------------------------------


class TestExecuteErrors:
    def test_ansible_failure_adds_diagnostic(self):
        klass = _make_class()
        prov = _provider(state={"h1": _host()})
        inst = klass(prov)
        diags = Diagnostics()
        with patch("terrible_provider.task_base._run_module", return_value={"failed": True, "msg": "boom"}):
            inst._execute(diags, {"host_id": "h1"})
        assert diags.has_errors()

    def test_unreachable_adds_diagnostic(self):
        klass = _make_class()
        prov = _provider(state={"h1": _host()})
        inst = klass(prov)
        diags = Diagnostics()
        with patch("terrible_provider.task_base._run_module", return_value={"unreachable": True, "msg": "no route"}):
            inst._execute(diags, {"host_id": "h1"})
        assert diags.has_errors()


# ---------------------------------------------------------------------------
# Helper — fake TaskQueueManager that injects a result into the callback
# ---------------------------------------------------------------------------


def _make_mock_tqm(result):
    class _MockTQM:
        def __init__(self, **kwargs):
            self._callback_plugins = []

        def load_callbacks(self):
            pass

        def run(self, play):
            for cb in self._callback_plugins:
                if hasattr(cb, "result") and cb.result is None:
                    cb.result = result

        def cleanup(self):
            pass

    return _MockTQM


# ---------------------------------------------------------------------------
# _ensure_collection_finder
# ---------------------------------------------------------------------------


class TestEnsureCollectionFinder:
    def test_already_installed_no_op(self):
        mock_cfg = MagicMock()
        mock_cfg.collection_finder = MagicMock()  # Not None — already installed
        mock_module = MagicMock(
            AnsibleCollectionConfig=mock_cfg,
            _AnsibleCollectionFinder=MagicMock(),
        )
        with patch.dict(sys.modules, {"ansible.utils.collection_loader._collection_finder": mock_module}):
            _ensure_collection_finder()
        mock_module._AnsibleCollectionFinder.assert_not_called()

    def test_not_installed_calls_install(self):
        mock_finder = MagicMock()
        mock_cfg = MagicMock()
        mock_cfg.collection_finder = None
        mock_module = MagicMock(
            _AnsibleCollectionFinder=MagicMock(return_value=mock_finder),
            AnsibleCollectionConfig=mock_cfg,
        )
        with patch.dict(sys.modules, {"ansible.utils.collection_loader._collection_finder": mock_module}):
            _ensure_collection_finder()
        mock_module._AnsibleCollectionFinder.assert_called_once_with(paths=[])
        mock_finder._install.assert_called_once()

    def test_import_error_is_ignored(self):
        with patch.dict(sys.modules, {"ansible.utils.collection_loader._collection_finder": None}):
            _ensure_collection_finder()  # Must not raise


# ---------------------------------------------------------------------------
# _ensure_ansible_initialized
# ---------------------------------------------------------------------------


class TestEnsureAnsibleInitialized:
    def test_sets_cliargs_on_first_call(self):
        import terrible_provider.task_base as tb

        original = tb._ansible_initialized
        tb._ansible_initialized = False
        try:
            _ensure_ansible_initialized()
            assert tb._ansible_initialized is True
        finally:
            tb._ansible_initialized = original

    def test_noop_when_already_initialized(self):
        import terrible_provider.task_base as tb

        tb._ansible_initialized = True
        _ensure_ansible_initialized()
        assert tb._ansible_initialized is True

    def test_double_checked_locking_inner_guard(self):
        """Cover the inner 'if _ansible_initialized: return' inside the lock.

        Strategy: pre-acquire the lock so a background thread blocks on it after
        passing the outer check (which sees False).  Then set the flag to True
        and release — the thread acquires the lock, finds True on the inner
        check, and returns early, hitting line 42.
        """
        import terrible_provider.task_base as tb

        class _ControlledLock:
            """Drop-in lock whose inner mutex we can hold from the test thread."""

            def __init__(self):
                self._inner = threading.Lock()
                self.waiting = threading.Event()

            def acquire(self, *a, **kw):
                self.waiting.set()  # signal: thread is about to block
                return self._inner.acquire(*a, **kw)

            def release(self):
                self._inner.release()

            def __enter__(self):
                self.acquire()
                return self

            def __exit__(self, *a):
                self.release()

        orig_init = tb._ansible_initialized
        orig_lock = tb._ansible_init_lock
        controlled = _ControlledLock()
        controlled._inner.acquire()  # hold the lock before the thread starts
        tb._ansible_init_lock = controlled
        tb._ansible_initialized = False

        done = threading.Event()

        def _run():
            _ensure_ansible_initialized()  # outer check False → tries to acquire → blocks
            done.set()

        t = threading.Thread(target=_run)
        try:
            t.start()
            controlled.waiting.wait(timeout=2)  # thread is now blocked on acquire()
            tb._ansible_initialized = True  # set flag while thread can't see yet
            controlled._inner.release()  # unblock thread → inner check sees True
            assert done.wait(timeout=2), "thread did not finish"
        finally:
            tb._ansible_init_lock = orig_lock
            tb._ansible_initialized = orig_init
            t.join()


# ---------------------------------------------------------------------------
# _make_callback
# ---------------------------------------------------------------------------


class TestMakeCallback:
    def test_returns_callback_with_none_result(self):
        cb = _make_callback()
        assert cb.result is None

    def test_v2_runner_on_ok_sets_result(self):
        cb = _make_callback()
        r = MagicMock()
        r.result = {"changed": False, "ping": "pong"}
        cb.v2_runner_on_ok(r)
        assert cb.result == {"changed": False, "ping": "pong"}

    def test_v2_runner_on_failed_sets_result(self):
        cb = _make_callback()
        r = MagicMock()
        r.result = {"failed": True, "msg": "boom"}
        cb.v2_runner_on_failed(r)
        assert cb.result == {"failed": True, "msg": "boom"}

    def test_v2_runner_on_unreachable_sets_unreachable(self):
        cb = _make_callback()
        r = MagicMock()
        r.result = {"msg": "no route"}
        cb.v2_runner_on_unreachable(r)
        assert cb.result.get("unreachable") is True

    def test_v2_runner_on_skipped_sets_skipped(self):
        cb = _make_callback()
        r = MagicMock()
        cb.v2_runner_on_skipped(r)
        assert cb.result == {"changed": False, "skipped": True}


# ---------------------------------------------------------------------------
# _run_module
# ---------------------------------------------------------------------------


class TestReapWorkers:
    def test_reap_terminates_and_joins_children(self):
        from terrible_provider.task_base import _reap_workers

        child = MagicMock()
        with patch("multiprocessing.active_children", return_value=[child]):
            _reap_workers()
        child.terminate.assert_called_once()
        child.join.assert_called_once_with(timeout=5)
        child.close.assert_called_once()

    def test_reap_no_children_is_noop(self):
        from terrible_provider.task_base import _reap_workers

        with patch("multiprocessing.active_children", return_value=[]):
            _reap_workers()


class TestRunModule:
    _HOST = {"host": "127.0.0.1", "connection": "local"}

    def test_success_returns_callback_result(self):
        MockTQM = _make_mock_tqm({"changed": False, "ping": "pong"})
        with patch("ansible.executor.task_queue_manager.TaskQueueManager", MockTQM):
            result = _run_module(self._HOST, "ansible.builtin.ping", None)
        assert result == {"changed": False, "ping": "pong"}

    def test_with_args_string(self):
        MockTQM = _make_mock_tqm({"changed": True, "rc": 0})
        with patch("ansible.executor.task_queue_manager.TaskQueueManager", MockTQM):
            result = _run_module(self._HOST, "ansible.builtin.command", '{"_raw_params": "true"}')
        assert result["rc"] == 0

    def test_check_only_true(self):
        MockTQM = _make_mock_tqm({"changed": False})
        with patch("ansible.executor.task_queue_manager.TaskQueueManager", MockTQM):
            result = _run_module(self._HOST, "ansible.builtin.ping", None, check_only=True)
        assert result == {"changed": False}

    def test_ssh_host_with_extra_options(self):
        host = {
            "host": "10.0.0.1",
            "connection": "ssh",
            "user": "testuser",
            "private_key_path": "/tmp/id_rsa",
            "port": 2222,
        }
        MockTQM = _make_mock_tqm({"changed": False})
        with patch("ansible.executor.task_queue_manager.TaskQueueManager", MockTQM):
            result = _run_module(host, "ansible.builtin.ping", None)
        assert "failed" not in result

    def test_tqm_run_raises_returns_failed(self):
        class _ErrTQM:
            def __init__(self, **kwargs):
                self._callback_plugins = []

            def load_callbacks(self):
                pass

            def run(self, play):
                raise RuntimeError("task exploded")

            def cleanup(self):
                pass

        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _ErrTQM):
            result = _run_module(self._HOST, "ansible.builtin.ping", None)
        assert result["failed"] is True
        assert "task exploded" in result["msg"]

    def test_no_result_returns_failed(self):
        class _SilentTQM:
            def __init__(self, **kwargs):
                self._callback_plugins = []

            def load_callbacks(self):
                pass

            def run(self, play):
                pass

            def cleanup(self):
                pass

        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _SilentTQM):
            result = _run_module(self._HOST, "ansible.builtin.ping", None)
        assert result["failed"] is True

    def test_from_non_main_thread(self):
        MockTQM = _make_mock_tqm({"changed": False})
        results = []

        def _run():
            with patch("ansible.executor.task_queue_manager.TaskQueueManager", MockTQM):
                results.append(_run_module(self._HOST, "ansible.builtin.ping", None))

        t = threading.Thread(target=_run)
        t.start()
        t.join()
        assert results == [{"changed": False}]

    def test_become_vars_set(self):
        host = {
            "host": "127.0.0.1",
            "connection": "local",
            "become": True,
            "become_user": "root",
            "become_method": "sudo",
            "become_password": "s3cr3t",
        }
        captured = {}

        class _CaptureTQM:
            def __init__(self, inventory, **kw):
                hobj = inventory.get_host("target")
                captured.update(hobj.vars)
                self._callback_plugins = []

            def load_callbacks(self):
                pass

            def run(self, play):
                for cb in self._callback_plugins:
                    if hasattr(cb, "result") and cb.result is None:
                        cb.result = {"changed": False}

            def cleanup(self):
                pass

        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _CaptureTQM):
            _run_module(host, "ansible.builtin.ping", None)
        assert captured.get("ansible_become") is True
        assert captured.get("ansible_become_user") == "root"
        assert captured.get("ansible_become_method") == "sudo"
        assert captured.get("ansible_become_password") == "s3cr3t"

    def test_become_defaults(self):
        host = {"host": "127.0.0.1", "connection": "local", "become": True}
        captured = {}

        class _CaptureTQM:
            def __init__(self, inventory, **kw):
                captured.update(inventory.get_host("target").vars)
                self._callback_plugins = []

            def load_callbacks(self):
                pass

            def run(self, play):
                for cb in self._callback_plugins:
                    if hasattr(cb, "result") and cb.result is None:
                        cb.result = {"changed": False}

            def cleanup(self):
                pass

        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _CaptureTQM):
            _run_module(host, "ansible.builtin.ping", None)
        assert captured.get("ansible_become_user") == "root"
        assert captured.get("ansible_become_method") == "sudo"

    def test_become_false_skipped(self):
        host = {"host": "127.0.0.1", "connection": "local", "become": False}
        captured = {}

        class _CaptureTQM:
            def __init__(self, inventory, **kw):
                captured.update(inventory.get_host("target").vars)
                self._callback_plugins = []

            def load_callbacks(self):
                pass

            def run(self, play):
                for cb in self._callback_plugins:
                    if hasattr(cb, "result") and cb.result is None:
                        cb.result = {"changed": False}

            def cleanup(self):
                pass

        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _CaptureTQM):
            _run_module(host, "ansible.builtin.ping", None)
        assert "ansible_become" not in captured

    def test_vars_merged(self):
        host = {
            "host": "127.0.0.1",
            "connection": "local",
            "vars": {"ansible_python_interpreter": "/usr/bin/python3.11"},
        }
        captured = {}

        class _CaptureTQM:
            def __init__(self, inventory, **kw):
                captured.update(inventory.get_host("target").vars)
                self._callback_plugins = []

            def load_callbacks(self):
                pass

            def run(self, play):
                for cb in self._callback_plugins:
                    if hasattr(cb, "result") and cb.result is None:
                        cb.result = {"changed": False}

            def cleanup(self):
                pass

        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _CaptureTQM):
            _run_module(host, "ansible.builtin.ping", None)
        assert captured.get("ansible_python_interpreter") == "/usr/bin/python3.11"

    def test_ssh_extra_args_custom(self):
        host = {"host": "10.0.0.1", "connection": "ssh", "ssh_extra_args": "-o ProxyJump=bastion"}
        captured = {}

        class _CaptureTQM:
            def __init__(self, inventory, **kw):
                captured.update(inventory.get_host("target").vars)
                self._callback_plugins = []

            def load_callbacks(self):
                pass

            def run(self, play):
                for cb in self._callback_plugins:
                    if hasattr(cb, "result") and cb.result is None:
                        cb.result = {"changed": False}

            def cleanup(self):
                pass

        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _CaptureTQM):
            _run_module(host, "ansible.builtin.ping", None)
        assert captured.get("ansible_ssh_extra_args") == "-o ProxyJump=bastion"

    def test_ssh_extra_args_default_when_unset(self):
        host = {"host": "10.0.0.1", "connection": "ssh"}
        captured = {}

        class _CaptureTQM:
            def __init__(self, inventory, **kw):
                captured.update(inventory.get_host("target").vars)
                self._callback_plugins = []

            def load_callbacks(self):
                pass

            def run(self, play):
                for cb in self._callback_plugins:
                    if hasattr(cb, "result") and cb.result is None:
                        cb.result = {"changed": False}

            def cleanup(self):
                pass

        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _CaptureTQM):
            _run_module(host, "ansible.builtin.ping", None)
        assert "StrictHostKeyChecking" in captured.get("ansible_ssh_extra_args", "")

    def test_timeout_overrides_cliargs(self):
        from ansible import context as _ctx

        original_timeout = dict(_ctx.CLIARGS).get("timeout")
        MockTQM = _make_mock_tqm({"changed": False})
        with patch("ansible.executor.task_queue_manager.TaskQueueManager", MockTQM):
            _run_module(self._HOST, "ansible.builtin.ping", None, timeout=42)
        # After the call, CLIARGS must be restored
        assert dict(_ctx.CLIARGS).get("timeout") == original_timeout

    def test_changed_when_passed_to_task(self):
        captured_play = {}

        class _CaptureTQM:
            def __init__(self, **kw):
                self._callback_plugins = []

            def load_callbacks(self):
                pass

            def run(self, play):
                captured_play["tasks"] = play.compile()
                for cb in self._callback_plugins:
                    if hasattr(cb, "result") and cb.result is None:
                        cb.result = {"changed": False}

            def cleanup(self):
                pass

        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _CaptureTQM):
            _run_module(self._HOST, "ansible.builtin.ping", None, changed_when="false")
        # The play was loaded with changed_when — just verify no error
        assert "tasks" in captured_play

    def test_failed_when_passed_to_task(self):
        captured_play = {}

        class _CaptureTQM:
            def __init__(self, **kw):
                self._callback_plugins = []

            def load_callbacks(self):
                pass

            def run(self, play):
                captured_play["tasks"] = play.compile()
                for cb in self._callback_plugins:
                    if hasattr(cb, "result") and cb.result is None:
                        cb.result = {"changed": False}

            def cleanup(self):
                pass

        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _CaptureTQM):
            _run_module(self._HOST, "ansible.builtin.ping", None, failed_when="rc != 0")
        assert "tasks" in captured_play

    def test_environment_in_task(self):
        captured_play = {}

        class _CaptureTQM:
            def __init__(self, **kw):
                self._callback_plugins = []

            def load_callbacks(self):
                pass

            def run(self, play):
                captured_play["tasks"] = play.compile()
                for cb in self._callback_plugins:
                    if hasattr(cb, "result") and cb.result is None:
                        cb.result = {"changed": False}

            def cleanup(self):
                pass

        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _CaptureTQM):
            _run_module(self._HOST, "ansible.builtin.ping", None, environment={"ANSIBLE_TIMEOUT": "10"})
        assert "tasks" in captured_play

    def test_tags_in_task(self):
        captured_play = {}

        class _CaptureTQM:
            def __init__(self, **kw):
                self._callback_plugins = []

            def load_callbacks(self):
                pass

            def run(self, play):
                captured_play["tasks"] = play.compile()
                for cb in self._callback_plugins:
                    if hasattr(cb, "result") and cb.result is None:
                        cb.result = {"changed": False}

            def cleanup(self):
                pass

        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _CaptureTQM):
            _run_module(self._HOST, "ansible.builtin.ping", None, tags=["deploy"])
        assert "tasks" in captured_play


# ---------------------------------------------------------------------------
# _execute — ignore_errors and new kwarg passthrough
# ---------------------------------------------------------------------------


class TestExecuteIgnoreErrors:
    _RESULT = {"failed": True, "msg": "intentional"}

    def test_ignore_errors_suppresses_diagnostic(self):
        klass = _make_class()
        prov = _provider(state={"h1": _host()})
        inst = klass(prov)
        diags = Diagnostics()
        with patch("terrible_provider.task_base._run_module", return_value=self._RESULT):
            inst._execute(diags, {"host_id": "h1", "ignore_errors": True})
        assert not diags.has_errors()

    def test_ignore_errors_false_still_adds_error(self):
        klass = _make_class()
        prov = _provider(state={"h1": _host()})
        inst = klass(prov)
        diags = Diagnostics()
        with patch("terrible_provider.task_base._run_module", return_value=self._RESULT):
            inst._execute(diags, {"host_id": "h1", "ignore_errors": False})
        assert diags.has_errors()

    def test_timeout_and_expressions_forwarded(self):
        klass = _make_class()
        prov = _provider(state={"h1": _host()})
        inst = klass(prov)
        calls = []

        def _mock_run(host, module, args, **kwargs):
            calls.append(kwargs)
            return {"changed": False}

        with patch("terrible_provider.task_base._run_module", side_effect=_mock_run):
            inst._execute(
                Diagnostics(),
                {"host_id": "h1", "timeout": 60, "changed_when": "false", "failed_when": "rc != 0"},
            )
        assert calls[0]["timeout"] == 60
        assert calls[0]["changed_when"] == "false"
        assert calls[0]["failed_when"] == "rc != 0"

    def test_environment_tags_skip_tags_forwarded(self):
        klass = _make_class()
        prov = _provider(state={"h1": _host()})
        inst = klass(prov)
        calls = []

        def _mock_run(host, module, args, **kwargs):
            calls.append(kwargs)
            return {"changed": False}

        with patch("terrible_provider.task_base._run_module", side_effect=_mock_run):
            inst._execute(
                Diagnostics(),
                {
                    "host_id": "h1",
                    "environment": {"FOO": "bar"},
                    "tags": ["deploy"],
                    "skip_tags": ["slow"],
                },
            )
        assert calls[0]["environment"] == {"FOO": "bar"}
        assert calls[0]["tags"] == ["deploy"]
        assert calls[0]["skip_tags"] == ["slow"]

    def test_async_seconds_forwarded(self):
        klass = _make_class()
        prov = _provider(state={"h1": _host()})
        inst = klass(prov)
        calls = []

        def _mock_run(host, module, args, **kwargs):
            calls.append(kwargs)
            return {"changed": False}

        with patch("terrible_provider.task_base._run_module", side_effect=_mock_run):
            inst._execute(
                Diagnostics(),
                {"host_id": "h1", "async_seconds": 600, "poll_interval": 10},
            )
        assert calls[0]["async_seconds"] == 600
        assert calls[0]["poll_interval"] == 10


# ---------------------------------------------------------------------------
# Async task execution (async / poll)
# ---------------------------------------------------------------------------


class TestAsyncTaskExecution:
    _HOST = {"host": "127.0.0.1", "port": 22, "connection": "local"}

    @staticmethod
    def _find_task(compiled_blocks):
        """Find the actual (non-meta) task in a compiled play."""
        for block in compiled_blocks:
            for t in block.block:
                if t.action != "meta":
                    return t
        return None

    def test_async_poll_in_task_dict(self):
        captured_play = {}

        class _CaptureTQM:
            def __init__(self, **kw):
                self._callback_plugins = []

            def load_callbacks(self):
                pass

            def run(self, play):
                captured_play["tasks"] = play.compile()
                for cb in self._callback_plugins:
                    if hasattr(cb, "result") and cb.result is None:
                        cb.result = {"changed": False}

            def cleanup(self):
                pass

        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _CaptureTQM):
            _run_module(
                self._HOST, "ansible.builtin.command", '{"cmd": "sleep 1"}', async_seconds=600, poll_interval=10
            )
        task = self._find_task(captured_play["tasks"])
        assert task is not None
        assert task.async_val == 600
        assert task.poll == 10

    def test_no_async_when_zero(self):
        captured_play = {}

        class _CaptureTQM:
            def __init__(self, **kw):
                self._callback_plugins = []

            def load_callbacks(self):
                pass

            def run(self, play):
                captured_play["tasks"] = play.compile()
                for cb in self._callback_plugins:
                    if hasattr(cb, "result") and cb.result is None:
                        cb.result = {"changed": False}

            def cleanup(self):
                pass

        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _CaptureTQM):
            _run_module(self._HOST, "ansible.builtin.ping", None, async_seconds=0)
        task = self._find_task(captured_play["tasks"])
        assert task is not None
        assert task.async_val == 0

    def test_no_async_when_none(self):
        captured_play = {}

        class _CaptureTQM:
            def __init__(self, **kw):
                self._callback_plugins = []

            def load_callbacks(self):
                pass

            def run(self, play):
                captured_play["tasks"] = play.compile()
                for cb in self._callback_plugins:
                    if hasattr(cb, "result") and cb.result is None:
                        cb.result = {"changed": False}

            def cleanup(self):
                pass

        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _CaptureTQM):
            _run_module(self._HOST, "ansible.builtin.ping", None)
        task = self._find_task(captured_play["tasks"])
        assert task is not None
        assert task.async_val == 0


# ---------------------------------------------------------------------------
# delegate_to on task resources
# ---------------------------------------------------------------------------


class TestDelegateTo:
    _HOST = {"host": "127.0.0.1", "port": 22, "connection": "local"}
    _DELEGATE = {"host": "10.0.0.99", "port": 22, "user": "deploy", "connection": "ssh"}

    @staticmethod
    def _find_task(compiled_blocks):
        for block in compiled_blocks:
            for t in block.block:
                if t.action != "meta":
                    return t
        return None

    def test_delegate_host_added_to_inventory(self):
        captured = {}

        class _CaptureTQM:
            def __init__(self, inventory, **kw):
                captured["inventory"] = inventory
                self._callback_plugins = []

            def load_callbacks(self):
                pass

            def run(self, play):
                captured["tasks"] = play.compile()
                for cb in self._callback_plugins:
                    if hasattr(cb, "result") and cb.result is None:
                        cb.result = {"changed": False}

            def cleanup(self):
                pass

        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _CaptureTQM):
            _run_module(self._HOST, "ansible.builtin.ping", None, delegate_host_state=self._DELEGATE)
        inv = captured["inventory"]
        delegate = inv.get_host("delegate")
        assert delegate is not None
        assert delegate.vars["ansible_host"] == "10.0.0.99"
        assert delegate.vars["ansible_user"] == "deploy"

    def test_delegate_to_in_task_dict(self):
        captured = {}

        class _CaptureTQM:
            def __init__(self, **kw):
                self._callback_plugins = []

            def load_callbacks(self):
                pass

            def run(self, play):
                captured["tasks"] = play.compile()
                for cb in self._callback_plugins:
                    if hasattr(cb, "result") and cb.result is None:
                        cb.result = {"changed": False}

            def cleanup(self):
                pass

        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _CaptureTQM):
            _run_module(self._HOST, "ansible.builtin.ping", None, delegate_host_state=self._DELEGATE)
        task = self._find_task(captured["tasks"])
        assert task is not None
        assert task.delegate_to == "delegate"

    def test_no_delegate_when_none(self):
        captured = {}

        class _CaptureTQM:
            def __init__(self, inventory, **kw):
                captured["inventory"] = inventory
                self._callback_plugins = []

            def load_callbacks(self):
                pass

            def run(self, play):
                captured["tasks"] = play.compile()
                for cb in self._callback_plugins:
                    if hasattr(cb, "result") and cb.result is None:
                        cb.result = {"changed": False}

            def cleanup(self):
                pass

        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _CaptureTQM):
            _run_module(self._HOST, "ansible.builtin.ping", None)
        inv = captured["inventory"]
        assert inv.get_host("delegate") is None
        task = self._find_task(captured["tasks"])
        assert task.delegate_to is None

    def test_execute_resolves_delegate_host(self):
        klass = _make_class()
        h2 = {"host": "10.0.0.99", "connection": "ssh"}
        prov = _provider(state={"h1": _host(), "h2": h2})
        inst = klass(prov)
        calls = []

        def _mock_run(host, module, args, **kwargs):
            calls.append(kwargs)
            return {"changed": False}

        with patch("terrible_provider.task_base._run_module", side_effect=_mock_run):
            inst._execute(Diagnostics(), {"host_id": "h1", "delegate_to_id": "h2"})
        assert calls[0]["delegate_host_state"]["host"] == "10.0.0.99"

    def test_execute_errors_on_missing_delegate(self):
        klass = _make_class()
        prov = _provider(state={"h1": _host()})
        inst = klass(prov)
        diags = Diagnostics()
        with patch("terrible_provider.task_base._run_module", return_value={"changed": False}):
            inst._execute(diags, {"host_id": "h1", "delegate_to_id": "missing"})
        assert diags.has_errors()


# ---------------------------------------------------------------------------
# Vault secrets forwarding (_run_module)
# ---------------------------------------------------------------------------


class TestVaultSecretsInRunModule:
    _HOST = {"host": "127.0.0.1", "connection": "local"}

    def test_vault_secrets_set_on_loader(self):
        captured = {}

        class _CaptureLoaderTQM:
            def __init__(self, loader, **kw):
                captured["vault_secrets"] = getattr(loader, "_vault_secrets", "NOT_SET")
                self._callback_plugins = []

            def load_callbacks(self):
                pass

            def run(self, play):
                for cb in self._callback_plugins:
                    if hasattr(cb, "result") and cb.result is None:
                        cb.result = {"changed": False}

            def cleanup(self):
                pass

        secrets = [("default", MagicMock())]
        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _CaptureLoaderTQM):
            _run_module(self._HOST, "ansible.builtin.ping", None, vault_secrets=secrets)
        assert captured["vault_secrets"] is not None

    def test_no_vault_secrets_leaves_loader_unchanged(self):
        captured = {}

        class _CaptureLoaderTQM:
            def __init__(self, loader, **kw):
                captured["vault_secrets"] = getattr(loader, "_vault_secrets", "NOT_SET")
                self._callback_plugins = []

            def load_callbacks(self):
                pass

            def run(self, play):
                for cb in self._callback_plugins:
                    if hasattr(cb, "result") and cb.result is None:
                        cb.result = {"changed": False}

            def cleanup(self):
                pass

        with patch("ansible.executor.task_queue_manager.TaskQueueManager", _CaptureLoaderTQM):
            _run_module(self._HOST, "ansible.builtin.ping", None)
        # Without vault_secrets, _vault_secrets should be empty or not set
        assert captured["vault_secrets"] in (None, "NOT_SET", [])

    def test_execute_forwards_vault_secrets_from_provider(self):
        klass = _make_class()
        prov = _provider(state={"h1": _host()})
        prov._vault_secrets = [("default", MagicMock())]
        inst = klass(prov)
        calls = []

        def _mock_run(host, module, args, **kwargs):
            calls.append(kwargs)
            return {"changed": False}

        with patch("terrible_provider.task_base._run_module", side_effect=_mock_run):
            inst._execute(Diagnostics(), {"host_id": "h1"})
        assert calls[0].get("vault_secrets") == prov._vault_secrets

    def test_execute_check_forwards_vault_secrets(self):
        klass = _make_class(check_mode="full")
        prov = _provider(state={"h1": _host(), "rid": {"id": "rid", "host_id": "h1"}})
        prov._vault_secrets = [("default", MagicMock())]
        inst = klass(prov)

        with patch("terrible_provider.task_base._run_module", return_value={"changed": False}) as mock_run:
            inst._execute_check(Diagnostics(), {"host_id": "h1"})
        assert mock_run.call_args.kwargs.get("vault_secrets") == prov._vault_secrets


# ---------------------------------------------------------------------------
# WinRM host inventory setup
# ---------------------------------------------------------------------------


class TestSetupHostInventoryWinRM:
    def _make_host(self):
        from ansible.inventory.manager import InventoryManager
        from ansible.parsing.dataloader import DataLoader

        loader = DataLoader()
        inv = InventoryManager(loader=loader, sources="target,")
        return inv.get_host("target")

    def test_winrm_sets_default_port(self):
        hobj = self._make_host()
        _setup_host_inventory(hobj, {"host": "win.example.com", "connection": "winrm"})
        assert hobj.vars["ansible_port"] == 5986
        assert hobj.vars["ansible_connection"] == "winrm"

    def test_winrm_custom_port(self):
        hobj = self._make_host()
        _setup_host_inventory(hobj, {"host": "win.example.com", "connection": "winrm", "winrm_port": 5985})
        assert hobj.vars["ansible_port"] == 5985

    def test_winrm_scheme(self):
        hobj = self._make_host()
        _setup_host_inventory(hobj, {"host": "win.example.com", "connection": "winrm", "winrm_scheme": "http"})
        assert hobj.vars["ansible_winrm_scheme"] == "http"

    def test_winrm_transport(self):
        hobj = self._make_host()
        _setup_host_inventory(hobj, {"host": "win.example.com", "connection": "winrm", "winrm_transport": "kerberos"})
        assert hobj.vars["ansible_winrm_transport"] == "kerberos"

    def test_winrm_cert_validation(self):
        hobj = self._make_host()
        _setup_host_inventory(
            hobj,
            {
                "host": "win.example.com",
                "connection": "winrm",
                "winrm_server_cert_validation": "ignore",
            },
        )
        assert hobj.vars["ansible_winrm_server_cert_validation"] == "ignore"

    def test_winrm_defaults_scheme_to_https(self):
        hobj = self._make_host()
        _setup_host_inventory(hobj, {"host": "win.example.com", "connection": "winrm"})
        assert hobj.vars["ansible_winrm_scheme"] == "https"

    def test_winrm_defaults_transport_to_ntlm(self):
        hobj = self._make_host()
        _setup_host_inventory(hobj, {"host": "win.example.com", "connection": "winrm"})
        assert hobj.vars["ansible_winrm_transport"] == "ntlm"

    def test_winrm_defaults_cert_validation_to_validate(self):
        hobj = self._make_host()
        _setup_host_inventory(hobj, {"host": "win.example.com", "connection": "winrm"})
        assert hobj.vars["ansible_winrm_server_cert_validation"] == "validate"

    def test_winrm_does_not_set_ssh_extra_args(self):
        hobj = self._make_host()
        _setup_host_inventory(hobj, {"host": "win.example.com", "connection": "winrm"})
        assert "ansible_ssh_extra_args" not in hobj.vars

    def test_ssh_connection_still_sets_ssh_extra_args(self):
        hobj = self._make_host()
        _setup_host_inventory(hobj, {"host": "linux.example.com", "connection": "ssh"})
        assert "ansible_ssh_extra_args" in hobj.vars
