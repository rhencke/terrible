import uuid
from typing import Optional

from tf.schema import Schema, Attribute
from tf.types import Bool, NormalizedJson, String, Number
from tf.iface import Resource, CreateContext, ReadContext, UpdateContext, DeleteContext, ImportContext


class TerribleHost(Resource):
    """A target host for Ansible tasks (replaces Ansible inventory)."""

    @classmethod
    def get_name(cls) -> str:
        return "host"

    @classmethod
    def get_schema(cls) -> Schema:
        return Schema(
            attributes=[
                Attribute("id", String(), description="Unique host ID", computed=True),
                Attribute("host", String(), description="Hostname or IP address", required=True, requires_replace=True),
                Attribute("port", Number(), description="SSH port", optional=True, computed=True, default=22),
                Attribute("user", String(), description="SSH user", optional=True),
                Attribute(
                    "private_key_path",
                    String(),
                    description="Path to SSH private key",
                    optional=True,
                    sensitive=True,
                ),
                Attribute(
                    "connection",
                    String(),
                    description="Ansible connection type (e.g. local, ssh, docker). Defaults to ssh.",
                    optional=True,
                ),
                Attribute(
                    "ssh_extra_args",
                    String(),
                    description=(
                        "Extra SSH arguments appended to ansible_ssh_extra_args. "
                        "Defaults to StrictHostKeyChecking=no when unset."
                    ),
                    optional=True,
                ),
                Attribute("become", Bool(), description="Enable privilege escalation.", optional=True),
                Attribute(
                    "become_user",
                    String(),
                    description="User to become. Defaults to root when become is true.",
                    optional=True,
                ),
                Attribute(
                    "become_method",
                    String(),
                    description="Escalation method (sudo, su, pbrun, …). Defaults to sudo.",
                    optional=True,
                ),
                Attribute(
                    "become_password",
                    String(),
                    description="Password for privilege escalation.",
                    optional=True,
                    sensitive=True,
                ),
                Attribute(
                    "vars",
                    NormalizedJson(),
                    description=(
                        "Arbitrary Ansible host variables merged into the host object "
                        "(e.g. ansible_python_interpreter, ansible_shell_type)."
                    ),
                    optional=True,
                ),
            ]
        )

    def __init__(self, provider):
        self._prov = provider

    def create(self, ctx: CreateContext, planned: dict) -> Optional[dict]:
        new_id = uuid.uuid4().hex
        state = {**planned, "id": new_id}
        if state.get("port") is None:
            state["port"] = 22
        self._prov._state[new_id] = state
        self._prov._save_state()
        return state

    def read(self, ctx: ReadContext, current: dict) -> Optional[dict]:
        rid = current.get("id")
        return self._prov._state.get(rid)

    def update(self, ctx: UpdateContext, current: dict, planned: dict) -> Optional[dict]:
        rid = current["id"]
        state = {**planned, "id": rid}
        self._prov._state[rid] = state
        self._prov._save_state()
        return state

    def delete(self, ctx: DeleteContext, current: dict):
        rid = current.get("id")
        self._prov._state.pop(rid, None)
        self._prov._save_state()

    def import_(self, ctx: ImportContext, id: str) -> Optional[dict]:
        return self._prov._state.get(id)
