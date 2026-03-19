"""TerriblePlaybook and TerribleRole resources — run playbooks/roles in-process."""

from __future__ import annotations

import signal as _signal
import threading
import uuid

import yaml
from tf.iface import (
    CreateContext,
    DeleteContext,
    ImportContext,
    PlanContext,
    ReadContext,
    Resource,
    UpdateContext,
)
from tf.schema import Attribute, Schema
from tf.types import Bool, NormalizedJson, Number, String, Unknown

from .task_base import (
    _MODULE_TIMEOUT,
    _ensure_ansible_initialized,
    _reap_workers,
    _run_module_lock,
    _setup_host_inventory,
)

# ---------------------------------------------------------------------------
# Multi-task callback — accumulates results across all tasks in a run
# ---------------------------------------------------------------------------


def _make_multi_callback():
    """Return a fresh callback that accumulates results across multiple tasks."""
    from ansible.plugins.callback import CallbackBase

    class _MultiCB(CallbackBase):
        _implemented_callback_methods = frozenset(
            {
                "v2_runner_on_ok",
                "v2_runner_on_failed",
                "v2_runner_on_unreachable",
                "v2_runner_on_skipped",
            }
        )

        def __init__(self):
            super().__init__()
            self.results: list = []
            self.any_changed: bool = False
            self.any_failed: bool = False

        def v2_runner_on_ok(self, result):
            res = dict(result.result)
            self.results.append(res)
            if res.get("changed"):
                self.any_changed = True

        def v2_runner_on_failed(self, result, ignore_errors=False):
            res = dict(result.result)
            self.results.append(res)
            self.any_failed = True

        def v2_runner_on_unreachable(self, result):
            res = {"unreachable": True, **dict(result.result)}
            self.results.append(res)
            self.any_failed = True

        def v2_runner_on_skipped(self, result):
            pass  # skipped tasks don't affect changed/failed

    return _MultiCB()


# ---------------------------------------------------------------------------
# Shared TQM execution — runs a list of pre-built play dicts
# ---------------------------------------------------------------------------


def _execute_plays(
    host_state: dict,
    play_dicts: list,
    extra_vars: dict | None = None,
    *,
    timeout: int | None = None,
    tags: list | None = None,
    skip_tags: list | None = None,
    vault_secrets: list | None = None,
) -> dict:
    """Run a list of play dicts in-process via TaskQueueManager."""
    from ansible import context as _ansible_context
    from ansible.executor.task_queue_manager import TaskQueueManager
    from ansible.inventory.manager import InventoryManager
    from ansible.parsing.dataloader import DataLoader
    from ansible.playbook.play import Play
    from ansible.utils.context_objects import CLIArgs
    from ansible.vars.manager import VariableManager

    effective_timeout = int(timeout) if timeout else _MODULE_TIMEOUT

    with _run_module_lock:
        _in_main = threading.current_thread() is threading.main_thread()
        if not _in_main:
            _real_signal = _signal.signal
            _signal.signal = lambda *a, **kw: None  # type: ignore[method-assign]

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

        vm = VariableManager(loader=loader, inventory=inv)
        if extra_vars:
            vm._extra_vars = extra_vars

        cb = _make_multi_callback()
        tqm = None
        try:
            _reap_workers()

            tqm = TaskQueueManager(
                inventory=inv,
                variable_manager=vm,
                loader=loader,
                passwords={},
                stdout_callback_name="minimal",
                run_additional_callbacks=False,
                forks=1,
            )
            tqm.has_dead_workers = lambda: False  # type: ignore[invalid-assignment]
            tqm.load_callbacks()
            tqm._callback_plugins.append(cb)
            for play_dict in play_dicts:
                play = Play().load(play_dict, variable_manager=vm, loader=loader)
                tqm.run(play)
        except Exception as exc:
            return {"failed": True, "msg": f"Ansible error: {exc}"}
        finally:
            if tqm:
                tqm.cleanup()
                _reap_workers()
            loader.cleanup_all_tmp_files()
            if not _in_main:
                _signal.signal = _real_signal
            _ansible_context.CLIARGS = orig_cliargs

    last = cb.results[-1] if cb.results else {}
    return {"changed": cb.any_changed, **last}


# ---------------------------------------------------------------------------
# Public runners
# ---------------------------------------------------------------------------


def _run_playbook(
    host_state: dict,
    playbook_path: str,
    extra_vars: dict | None = None,
    *,
    timeout: int | None = None,
    tags: list | None = None,
    skip_tags: list | None = None,
    vault_secrets: list | None = None,
) -> dict:
    """Load a playbook YAML and run all its plays against *host_state*."""
    _ensure_ansible_initialized()
    try:
        with open(playbook_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except Exception as exc:
        return {"failed": True, "msg": f"Failed to load playbook '{playbook_path}': {exc}"}

    if not isinstance(raw, list):
        return {"failed": True, "msg": f"Playbook '{playbook_path}' must be a YAML list of plays"}

    play_dicts = [{**p, "hosts": "target"} for p in raw if isinstance(p, dict)]
    return _execute_plays(
        host_state,
        play_dicts,
        extra_vars,
        timeout=timeout,
        tags=tags,
        skip_tags=skip_tags,
        vault_secrets=vault_secrets,
    )


def _run_role(
    host_state: dict,
    role_name: str,
    extra_vars: dict | None = None,
    *,
    timeout: int | None = None,
    tags: list | None = None,
    skip_tags: list | None = None,
    vault_secrets: list | None = None,
) -> dict:
    """Synthesize a single-role play and run it against *host_state*."""
    _ensure_ansible_initialized()
    play_dict: dict = {
        "name": "terrible_role",
        "hosts": "target",
        "gather_facts": "no",
        "roles": [{"role": role_name}],
    }
    return _execute_plays(
        host_state,
        [play_dict],
        extra_vars,
        timeout=timeout,
        tags=tags,
        skip_tags=skip_tags,
        vault_secrets=vault_secrets,
    )


# ---------------------------------------------------------------------------
# Shared schema attributes
# ---------------------------------------------------------------------------

_COMMON_ATTRS = [
    Attribute("id", String(), description="Unique resource ID.", computed=True),
    Attribute(
        "host_id",
        String(),
        description="ID of the `terrible_host` to run against.",
        required=True,
        requires_replace=True,
    ),
    Attribute("result", NormalizedJson(), description="Full raw JSON result.", computed=True),
    Attribute("changed", Bool(), description="Whether any task reported a change.", computed=True),
    Attribute(
        "extra_vars",
        NormalizedJson(),
        description="Extra variables passed to the playbook/role.",
        optional=True,
    ),
    Attribute(
        "tags",
        NormalizedJson(),
        description="Run only tasks with these Ansible tags.",
        optional=True,
    ),
    Attribute(
        "skip_tags",
        NormalizedJson(),
        description="Skip tasks with these Ansible tags.",
        optional=True,
    ),
    Attribute(
        "timeout",
        Number(),
        description=f"Override execution timeout (seconds). Defaults to {_MODULE_TIMEOUT}.",
        optional=True,
    ),
    Attribute(
        "ignore_errors",
        Bool(),
        description="When true, failures do not raise a Terraform error.",
        optional=True,
    ),
]


# ---------------------------------------------------------------------------
# Base class — shared CRUD logic
# ---------------------------------------------------------------------------


class _PlayResourceBase(Resource):
    _schema: Schema

    @classmethod
    def get_schema(cls):
        return cls._schema

    def __init__(self, provider):
        self._prov = provider

    def plan(self, ctx: PlanContext, current: dict | None, planned: dict) -> dict | None:
        if current is None:
            return {**planned, "result": Unknown, "changed": Unknown}
        inputs_changed = any(
            v is not Unknown and current.get(k) != v for k, v in planned.items() if k not in {"id", "result", "changed"}
        )
        if inputs_changed:
            return {**planned, "result": Unknown, "changed": Unknown}
        return dict(current)

    def _resolve_host(self, host_id: str, diags) -> dict | None:
        h = self._prov._state.get(host_id)
        if h is None:
            diags.add_error(
                f"Host '{host_id}' not found",
                "Ensure the terrible_host resource exists and has been applied.",
            )
        return h

    def _run(self, host_state: dict, planned: dict, vault_secrets=None) -> dict:
        raise NotImplementedError

    def _execute(self, diags, planned: dict) -> tuple[dict, bool]:
        host = self._resolve_host(planned["host_id"], diags)
        if host is None:
            return {}, False
        result = self._run(host_state=host, planned=planned, vault_secrets=self._prov._vault_secrets)
        changed = bool(result.get("changed", False))
        if (result.get("failed") or result.get("unreachable")) and not planned.get("ignore_errors"):
            diags.add_error("Ansible run failed", result.get("msg", "unknown error"))
        return result, changed

    def create(self, ctx: CreateContext, planned: dict) -> dict | None:
        result, changed = self._execute(ctx.diagnostics, planned)
        new_id = uuid.uuid4().hex
        state = {**planned, "id": new_id, "result": result, "changed": changed}
        self._prov._state[new_id] = state
        self._prov._save_state()
        return state

    def read(self, ctx: ReadContext, current: dict) -> dict | None:
        return self._prov._state.get(current["id"])

    def update(self, ctx: UpdateContext, current: dict, planned: dict) -> dict | None:
        result, changed = self._execute(ctx.diagnostics, planned)
        rid = current["id"]
        state = {**planned, "id": rid, "result": result, "changed": changed}
        self._prov._state[rid] = state
        self._prov._save_state()
        return state

    def delete(self, ctx: DeleteContext, current: dict):
        self._prov._state.pop(current.get("id"), None)
        self._prov._save_state()

    def import_(self, ctx: ImportContext, id: str) -> dict | None:
        return self._prov._state.get(id)


# ---------------------------------------------------------------------------
# Concrete resources
# ---------------------------------------------------------------------------


class TerriblePlaybook(_PlayResourceBase):
    _schema = Schema(
        attributes=_COMMON_ATTRS
        + [
            Attribute("playbook", String(), description="Path to the playbook YAML file.", required=True),
        ]
    )

    @classmethod
    def get_name(cls):
        return "playbook"

    def _run(self, host_state: dict, planned: dict, vault_secrets=None) -> dict:
        return _run_playbook(
            host_state,
            planned["playbook"],
            extra_vars=planned.get("extra_vars"),
            timeout=planned.get("timeout"),
            tags=planned.get("tags"),
            skip_tags=planned.get("skip_tags"),
            vault_secrets=vault_secrets,
        )


class TerribleRole(_PlayResourceBase):
    _schema = Schema(
        attributes=_COMMON_ATTRS
        + [
            Attribute("role", String(), description="Role name or path.", required=True),
        ]
    )

    @classmethod
    def get_name(cls):
        return "role"

    def _run(self, host_state: dict, planned: dict, vault_secrets=None) -> dict:
        return _run_role(
            host_state,
            planned["role"],
            extra_vars=planned.get("extra_vars"),
            timeout=planned.get("timeout"),
            tags=planned.get("tags"),
            skip_tags=planned.get("skip_tags"),
            vault_secrets=vault_secrets,
        )
