"""Base class for dynamically-generated Ansible module resources."""

import json
import signal as _signal
import threading
import uuid

from tf.iface import CreateContext, DeleteContext, ImportContext, PlanContext, ReadContext, Resource, UpdateContext
from tf.types import Unknown

_MODULE_TIMEOUT = 300  # seconds before an Ansible run is considered hung

# ---------------------------------------------------------------------------
# One-time Ansible in-process initialisation
# ---------------------------------------------------------------------------


def _ensure_collection_finder():
    """Install AnsibleCollectionFinder so FQCN modules resolve in-process."""
    try:
        from ansible.utils.collection_loader._collection_finder import (
            AnsibleCollectionConfig,
            _AnsibleCollectionFinder,
        )

        if AnsibleCollectionConfig.collection_finder is None:
            _AnsibleCollectionFinder(paths=[])._install()
    except ImportError:
        pass


_ensure_collection_finder()

_ansible_init_lock = threading.Lock()
_ansible_initialized = False


def _ensure_ansible_initialized():
    global _ansible_initialized
    if _ansible_initialized:
        return
    with _ansible_init_lock:
        if _ansible_initialized:
            return
        from ansible import context
        from ansible.utils.context_objects import CLIArgs

        context.CLIARGS = CLIArgs(
            {
                "module_path": None,
                "forks": 1,
                "become_method": None,
                "become_user": None,
                "check": False,
                "diff": False,
                "timeout": _MODULE_TIMEOUT,
                "connection": "ssh",
                "verbosity": 0,
                "private_key_file": None,
                "remote_user": None,
                "start_at_task": None,
                "task_timeout": 0,
                "tags": ["all"],
                "skip_tags": [],
            }
        )
        _ansible_initialized = True


_run_module_lock = threading.Lock()  # TQM is not thread-safe; serialise all calls


# ---------------------------------------------------------------------------
# Shared inventory setup
# ---------------------------------------------------------------------------


def _setup_host_inventory(hobj, host_state: dict) -> None:
    """Populate Ansible host variables on *hobj* from a TerribleHost state dict."""
    connection = host_state.get("connection")
    hobj.vars["ansible_host"] = host_state["host"]
    hobj.vars["ansible_port"] = int(host_state.get("port") or 22)
    if connection:
        hobj.vars["ansible_connection"] = connection
    if user := host_state.get("user"):
        hobj.vars["ansible_user"] = user
    if key := host_state.get("private_key_path"):
        hobj.vars["ansible_ssh_private_key_file"] = key
    if connection == "winrm":
        hobj.vars["ansible_port"] = int(host_state.get("winrm_port") or 5986)
        hobj.vars["ansible_winrm_scheme"] = host_state.get("winrm_scheme") or "https"
        hobj.vars["ansible_winrm_transport"] = host_state.get("winrm_transport") or "ntlm"
        hobj.vars["ansible_winrm_server_cert_validation"] = host_state.get("winrm_server_cert_validation") or "validate"
    elif connection != "local":
        hobj.vars["ansible_ssh_extra_args"] = (
            host_state.get("ssh_extra_args") or "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
        )
    if host_state.get("become"):
        hobj.vars["ansible_become"] = True
        hobj.vars["ansible_become_user"] = host_state.get("become_user") or "root"
        hobj.vars["ansible_become_method"] = host_state.get("become_method") or "sudo"
        hobj.vars["ansible_become_password"] = host_state.get("become_password")
    hobj.vars.update(host_state.get("vars") or {})


# ---------------------------------------------------------------------------
# Ansible callback — defined at module level (no closures needed)
# ---------------------------------------------------------------------------


def _make_callback():
    """Return a fresh CallbackBase instance that captures the task result."""
    from ansible.plugins.callback import CallbackBase

    class _CB(CallbackBase):
        result = None
        _implemented_callback_methods = frozenset(
            {
                "v2_runner_on_ok",
                "v2_runner_on_failed",
                "v2_runner_on_unreachable",
                "v2_runner_on_skipped",
            }
        )

        def v2_runner_on_ok(self, r):  # type: ignore[override]
            self.result = dict(r.result)

        def v2_runner_on_failed(self, r, ignore_errors=False):  # type: ignore[override]
            self.result = dict(r.result)

        def v2_runner_on_unreachable(self, r):  # type: ignore[override]
            self.result = {"unreachable": True, **dict(r.result)}

        def v2_runner_on_skipped(self, r):  # type: ignore[override]
            self.result = {"changed": False, "skipped": True}

    return _CB()


# ---------------------------------------------------------------------------
# Core execution
# ---------------------------------------------------------------------------


def _run_module(
    host_state: dict,
    module: str,
    args: str | None,
    *,
    check_only: bool = False,
    timeout: int | None = None,
    changed_when: str | None = None,
    failed_when: str | None = None,
    environment: dict | None = None,
    tags: list | None = None,
    skip_tags: list | None = None,
    async_seconds: int | None = None,
    poll_interval: int | None = None,
    delegate_host_state: dict | None = None,
    vault_secrets: list | None = None,
) -> dict:
    """Run an Ansible module in-process via TaskQueueManager."""
    _ensure_ansible_initialized()

    from ansible import context as _ansible_context
    from ansible.executor.task_queue_manager import TaskQueueManager
    from ansible.inventory.manager import InventoryManager
    from ansible.parsing.dataloader import DataLoader
    from ansible.playbook.play import Play
    from ansible.utils.context_objects import CLIArgs
    from ansible.vars.manager import VariableManager

    args_dict = json.loads(args) if args else {}

    with _run_module_lock:
        # Ansible's TQM calls signal.signal() internally, which fails in non-main
        # threads (gRPC worker threads).  Patch it to a no-op for the duration;
        # _run_module_lock ensures no other caller is affected concurrently.
        _in_main = threading.current_thread() is threading.main_thread()
        if not _in_main:
            _real_signal = _signal.signal
            _signal.signal = lambda *a, **kw: None  # type: ignore[method-assign]

        # Override CLIARGS for this call (timeout + tag filters); restore in finally.
        effective_timeout = int(timeout) if timeout else _MODULE_TIMEOUT
        orig_cliargs = _ansible_context.CLIARGS
        _ansible_context.CLIARGS = CLIArgs(
            {
                **dict(orig_cliargs),
                "timeout": effective_timeout,
                "tags": tags or ["all"],
                "skip_tags": skip_tags or [],
            }
        )

        loader = DataLoader()
        if vault_secrets:
            loader.set_vault_secrets(vault_secrets)
        inv = InventoryManager(loader=loader, sources="target,")
        hobj = inv.get_host("target")
        _setup_host_inventory(hobj, host_state)

        if delegate_host_state:
            inv.add_host(host="delegate", group="all")
            _setup_host_inventory(inv.get_host("delegate"), delegate_host_state)

        vm = VariableManager(loader=loader, inventory=inv)
        cb = _make_callback()
        task_dict: dict = dict(action=module, args=args_dict)
        if changed_when is not None:
            task_dict["changed_when"] = changed_when
        if failed_when is not None:
            task_dict["failed_when"] = failed_when
        if environment:
            task_dict["environment"] = environment
        if tags:
            task_dict["tags"] = tags
        if async_seconds and int(async_seconds) > 0:
            task_dict["async"] = int(async_seconds)
            task_dict["poll"] = int(poll_interval) if poll_interval else 15
        if delegate_host_state:
            task_dict["delegate_to"] = "delegate"
        play = Play().load(
            dict(
                name="terrible_task",
                hosts="target",
                gather_facts="no",
                check_mode=check_only,
                diff=check_only,
                tasks=[task_dict],
            ),
            variable_manager=vm,
            loader=loader,
        )

        tqm = None
        try:
            tqm = TaskQueueManager(
                inventory=inv,  # type: ignore[arg-type]
                variable_manager=vm,
                loader=loader,
                passwords={},
                stdout_callback_name="minimal",
                run_additional_callbacks=False,
                forks=1,
            )
            tqm.load_callbacks()
            tqm._callback_plugins.append(cb)
            tqm.run(play)
        except Exception as exc:
            return {"failed": True, "msg": f"Ansible in-process error: {exc}"}
        finally:
            if tqm:
                tqm.cleanup()
            loader.cleanup_all_tmp_files()
            if not _in_main:
                _signal.signal = _real_signal  # type: ignore[method-assign,unused-ignore]
            _ansible_context.CLIARGS = orig_cliargs

    return cb.result or {"failed": True, "msg": "No result captured from Ansible"}


_SKIP_ATTRS = frozenset(
    {
        "id",
        "host_id",
        "result",
        "changed",
        "triggers",
        "timeout",
        "ignore_errors",
        "changed_when",
        "failed_when",
        "environment",
        "tags",
        "skip_tags",
        "async_seconds",
        "poll_interval",
        "delegate_to_id",
    }
)


def _build_args_str(state: dict) -> str | None:
    """Serialize non-framework, non-null state entries as a JSON args string for Ansible."""
    args = {k: v for k, v in state.items() if k not in _SKIP_ATTRS and v not in (None, Unknown)}
    return json.dumps(args) if args else None


# ---------------------------------------------------------------------------
# Resource base class
# ---------------------------------------------------------------------------


class TerribleTaskBase(Resource):
    """
    Base class for per-module task resources.

    Subclasses set `_module_name` (FQCN) and `_schema` (Schema).
    Both are injected by the discovery factory.
    """

    _module_name: str = ""
    _schema = None
    _return_attr_names: set[str] = set()
    _return_attr_coercers: dict = {}
    _check_mode_support: str = "none"

    def __init__(self, provider):
        self._prov = provider

    @classmethod
    def get_schema(cls):
        return cls._schema

    def plan(self, ctx: PlanContext, current: dict | None, planned: dict) -> dict | None:
        unknown_outputs = {name: Unknown for name in self.__class__._return_attr_names}
        if current is None:
            # New resource — outputs unknown until creation
            return {**planned, **unknown_outputs, "result": Unknown, "changed": Unknown}

        # Existing resource — check whether any input attribute changed
        computed = self.__class__._return_attr_names | {"id", "result", "changed"}
        inputs_changed = any(v is not Unknown and current.get(k) != v for k, v in planned.items() if k not in computed)
        if inputs_changed:
            return {**planned, **unknown_outputs, "result": Unknown, "changed": Unknown}

        # Nothing changed — stable no-op plan
        return dict(current)

    def _resolve_host(self, host_id: str, diags) -> dict | None:
        h = self._prov._state.get(host_id)
        if h is None:
            diags.add_error(
                f"Host '{host_id}' not found",
                "Ensure the terrible_host resource exists and has been applied.",
            )
        return h

    def _execute(self, diags, planned: dict) -> tuple[dict, bool, dict]:
        host = self._resolve_host(planned["host_id"], diags)
        if host is None:
            return {}, False, {}

        delegate_host = None
        if planned.get("delegate_to_id"):
            delegate_host = self._resolve_host(planned["delegate_to_id"], diags)
            if delegate_host is None:
                return {}, False, {}

        args_str = _build_args_str(planned)
        result = _run_module(
            host,
            self.__class__._module_name,
            args_str,
            timeout=planned.get("timeout"),
            changed_when=planned.get("changed_when"),
            failed_when=planned.get("failed_when"),
            environment=planned.get("environment"),
            tags=planned.get("tags"),
            skip_tags=planned.get("skip_tags"),
            async_seconds=planned.get("async_seconds"),
            poll_interval=planned.get("poll_interval"),
            delegate_host_state=delegate_host,
            vault_secrets=self._prov._vault_secrets,
        )
        changed = bool(result.get("changed", False))
        if (result.get("failed") or result.get("unreachable")) and not planned.get("ignore_errors"):
            diags.add_error("Ansible task failed", result.get("msg", "unknown error"))

        coercers = self.__class__._return_attr_coercers
        return_attrs = {
            name: coercers[name](result.get(name)) if name in coercers else result.get(name)
            for name in self.__class__._return_attr_names
        }
        return result, changed, return_attrs

    def create(self, ctx: CreateContext, planned: dict) -> dict | None:
        result, changed, return_attrs = self._execute(ctx.diagnostics, planned)
        new_id = uuid.uuid4().hex
        state = {**planned, **return_attrs, "id": new_id, "result": result, "changed": changed}
        self._prov._state[new_id] = state
        self._prov._save_state()
        return state

    def _execute_check(self, diags, current: dict) -> dict | None:
        """Run module in check+diff mode against stored state. Returns raw result or None on host error."""
        host = self._resolve_host(current["host_id"], diags)
        if host is None:
            return None
        return _run_module(
            host,
            self.__class__._module_name,
            _build_args_str(current),
            check_only=True,
            timeout=current.get("timeout"),
            vault_secrets=self._prov._vault_secrets,
        )

    def read(self, ctx: ReadContext, current: dict) -> dict | None:
        stored = self._prov._state.get(current["id"])
        if stored is None:
            return None

        if self.__class__._check_mode_support != "full":
            return stored  # input-hash idempotency only

        result = self._execute_check(ctx.diagnostics, stored)
        if result is None:
            return stored  # host error — don't signal deletion

        if result.get("failed") or result.get("unreachable"):
            ctx.diagnostics.add_warning(
                "Ansible check mode failed during refresh",
                result.get("msg", "unknown error"),
            )
            return stored

        if not result.get("changed", False):
            return stored  # up to date, no drift

        # Drift detected — clear computed outputs so Terraform plans an update()
        drift_state = dict(stored)
        drift_state["result"] = None
        drift_state["changed"] = None
        for name in self.__class__._return_attr_names:
            drift_state[name] = None
        return drift_state

    def update(self, ctx: UpdateContext, current: dict, planned: dict) -> dict | None:
        result, changed, return_attrs = self._execute(ctx.diagnostics, planned)
        rid = current["id"]
        state = {**planned, **return_attrs, "id": rid, "result": result, "changed": changed}
        self._prov._state[rid] = state
        self._prov._save_state()
        return state

    def delete(self, ctx: DeleteContext, current: dict):
        self._prov._state.pop(current.get("id"), None)
        self._prov._save_state()

    def import_(self, ctx: ImportContext, id: str) -> dict | None:
        return self._prov._state.get(id)
