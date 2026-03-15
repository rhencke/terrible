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


def _run_module(host_state: dict, module: str, args: Optional[str]) -> dict:
    """Run an Ansible module ad-hoc against a host via CLI, returning the result dict."""
    tmpdir = tempfile.mkdtemp()
    try:
        host = host_state["host"]
        port = int(host_state.get("port") or 22)
        user = host_state.get("user")
        key = host_state.get("private_key_path")

        inv_line = f"{host} ansible_port={port}"
        if user:
            inv_line += f" ansible_user={user}"
        if key:
            inv_line += f" ansible_ssh_private_key_file={key}"

        inv_path = os.path.join(tmpdir, "inventory")
        with open(inv_path, "w") as f:
            f.write(f"[target]\n{inv_line}\n")

        results_dir = os.path.join(tmpdir, "results")
        os.makedirs(results_dir)

        cmd = [_ansible_bin(), "target", "-i", inv_path, "-m", module, "--tree", results_dir]
        if args:
            cmd += ["-a", args]

        proc = subprocess.run(cmd, capture_output=True, text=True)

        files = os.listdir(results_dir)
        if files:
            with open(os.path.join(results_dir, files[0])) as f:
                return json.load(f)

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

    def __init__(self, provider):
        self._prov = provider

    @classmethod
    def get_schema(cls):
        return cls._schema

    def plan(self, ctx: PlanContext, current: Optional[dict], planned: dict) -> Optional[dict]:
        unknown_outputs = {name: Unknown for name in self.__class__._return_attr_names}
        return {**planned, **unknown_outputs, "result": Unknown, "changed": Unknown}

    def _resolve_host(self, host_id: str, diags) -> Optional[dict]:
        h = self._prov._state.get(host_id)
        if h is None:
            diags.add_error(
                f"Host '{host_id}' not found",
                "Ensure the terrible_host resource exists and has been applied.",
            )
        return h

    def _execute(self, diags, planned: dict) -> tuple[dict, bool]:
        host = self._resolve_host(planned["host_id"], diags)
        if host is None:
            return {}, False

        # Collect module args from planned state (exclude framework fields)
        skip = {"id", "host_id", "result", "changed"}
        args_parts = [
            f"{k}={v}"
            for k, v in planned.items()
            if k not in skip and v is not None
        ]
        args_str = " ".join(args_parts) or None

        result = _run_module(host, self.__class__._module_name, args_str)
        changed = bool(result.get("changed", False))
        if result.get("failed") or result.get("unreachable"):
            diags.add_error("Ansible task failed", result.get("msg", "unknown error"))

        # Unpack individual return attributes from the result
        return_attrs = {
            name: result[name]
            for name in self.__class__._return_attr_names
            if name in result
        }
        return result, changed, return_attrs

    def create(self, ctx: CreateContext, planned: dict) -> Optional[dict]:
        result, changed, return_attrs = self._execute(ctx.diagnostics, planned)
        new_id = uuid.uuid4().hex
        state = {**planned, **return_attrs, "id": new_id, "result": result, "changed": changed}
        self._prov._state[new_id] = state
        self._prov._save_state()
        return state

    def read(self, ctx: ReadContext, current: dict) -> Optional[dict]:
        return self._prov._state.get(current["id"])

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
