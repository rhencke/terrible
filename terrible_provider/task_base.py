"""Base class for dynamically-generated Ansible module resources."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from typing import Optional

from tf.iface import Resource, CreateContext, ReadContext, UpdateContext, DeleteContext, ImportContext, PlanContext
from tf.types import Unknown


def _ansible_bin() -> str:
    candidate = os.path.join(os.path.dirname(sys.executable), "ansible")
    return candidate if os.path.exists(candidate) else "ansible"


_MODULE_TIMEOUT = 300  # seconds before an Ansible run is considered hung


def _run_module(host_state: dict, module: str, args: Optional[str], *, check_only: bool = False) -> dict:
    """Run an Ansible module ad-hoc against a host via CLI, returning the result dict."""
    tmpdir = tempfile.mkdtemp()
    try:
        host = host_state["host"]
        port = int(host_state.get("port") or 22)
        user = host_state.get("user")
        key = host_state.get("private_key_path")
        connection = host_state.get("connection")

        inv_line = f"{host} ansible_port={port}"
        if connection:
            inv_line += f" ansible_connection={connection}"
        if user:
            inv_line += f" ansible_user={user}"
        if key:
            inv_line += f" ansible_ssh_private_key_file={key}"
        if connection != "local":
            inv_line += " ansible_ssh_extra_args='-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'"

        inv_path = os.path.join(tmpdir, "inventory")
        with open(inv_path, "w") as f:
            f.write(f"[target]\n{inv_line}\n")

        results_dir = os.path.join(tmpdir, "results")
        os.makedirs(results_dir)

        cmd = [_ansible_bin(), "target", "-i", inv_path, "-m", module, "--tree", results_dir]
        if check_only:
            cmd += ["--check", "--diff"]
        if args:
            cmd += ["-a", args]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_MODULE_TIMEOUT)
        except subprocess.TimeoutExpired:
            return {"failed": True, "msg": f"Ansible module timed out after {_MODULE_TIMEOUT}s"}

        files = os.listdir(results_dir)
        if files:
            try:
                with open(os.path.join(results_dir, files[0])) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                return {"failed": True, "msg": f"Could not parse Ansible result: {exc}"}

        return {"failed": True, "msg": proc.stderr or proc.stdout}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


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

    def plan(self, ctx: PlanContext, current: Optional[dict], planned: dict) -> Optional[dict]:
        unknown_outputs = {name: Unknown for name in self.__class__._return_attr_names}
        if current is None:
            # New resource — outputs unknown until creation
            return {**planned, **unknown_outputs, "result": Unknown, "changed": Unknown}

        # Existing resource — check whether any input attribute changed
        computed = self.__class__._return_attr_names | {"id", "result", "changed"}
        inputs_changed = any(
            v is not Unknown and current.get(k) != v
            for k, v in planned.items()
            if k not in computed
        )
        if inputs_changed:
            return {**planned, **unknown_outputs, "result": Unknown, "changed": Unknown}

        # Nothing changed — stable no-op plan
        return dict(current)

    def _resolve_host(self, host_id: str, diags) -> Optional[dict]:
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

        # Collect module args from planned state as JSON (avoids k=v parsing ambiguity
        # and correctly handles free-form modules like command/shell)
        skip = {"id", "host_id", "result", "changed", "triggers"}
        args_dict = {
            k: v
            for k, v in planned.items()
            if k not in skip and v not in (None, Unknown)
        }
        args_str = json.dumps(args_dict) if args_dict else None

        result = _run_module(host, self.__class__._module_name, args_str)
        changed = bool(result.get("changed", False))
        if result.get("failed") or result.get("unreachable"):
            diags.add_error("Ansible task failed", result.get("msg", "unknown error"))

        # Unpack individual return attributes; default absent ones to None so
        # no Unknown values leak through from the plan phase.
        # Apply per-attribute coercers to handle mis-documented module RETURN types.
        coercers = self.__class__._return_attr_coercers
        return_attrs = {
            name: coercers[name](result.get(name)) if name in coercers else result.get(name)
            for name in self.__class__._return_attr_names
        }
        return result, changed, return_attrs

    def create(self, ctx: CreateContext, planned: dict) -> Optional[dict]:
        result, changed, return_attrs = self._execute(ctx.diagnostics, planned)
        new_id = uuid.uuid4().hex
        # Merge: planned inputs first, then computed outputs (overrides any Unknown from plan)
        state = {**planned, **return_attrs, "id": new_id, "result": result, "changed": changed}
        self._prov._state[new_id] = state
        self._prov._save_state()
        return state

    def _execute_check(self, diags, current: dict) -> Optional[dict]:
        """Run module in check+diff mode against stored state. Returns raw result or None on host error."""
        host = self._resolve_host(current["host_id"], diags)
        if host is None:
            return None
        skip = {"id", "host_id", "result", "changed", "triggers"}
        args_dict = {k: v for k, v in current.items() if k not in skip and v not in (None, Unknown)}
        args_str = json.dumps(args_dict) if args_dict else None
        return _run_module(host, self.__class__._module_name, args_str, check_only=True)

    def read(self, ctx: ReadContext, current: dict) -> Optional[dict]:
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

    def update(self, ctx: UpdateContext, current: dict, planned: dict) -> Optional[dict]:
        result, changed, return_attrs = self._execute(ctx.diagnostics, planned)
        rid = current["id"]
        state = {**planned, **return_attrs, "id": rid, "result": result, "changed": changed}
        self._prov._state[rid] = state
        self._prov._save_state()
        return state

    def delete(self, ctx: DeleteContext, current: dict):
        self._prov._state.pop(current.get("id"), None)
        self._prov._save_state()

    def import_(self, ctx: ImportContext, id: str) -> Optional[dict]:
        return self._prov._state.get(id)
