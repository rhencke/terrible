"""
Dynamic discovery of Ansible task types and creation of per-task Resource subclasses.

Each Ansible module file embeds DOCUMENTATION (input options) and RETURN (output
attributes) YAML blocks. We parse both to build a full tf Schema — options become
required/optional attributes, return values become computed attributes — then
dynamically subclass TerribleTaskBase for each discovered task type.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sqlite3
from pathlib import Path

import yaml
from tf.schema import Attribute, Schema
from tf.types import Bool, Map, NormalizedJson, Number, String

from .task_base import _MODULE_TIMEOUT, TerribleTaskBase
from .task_datasource import TerribleTaskDataSource

log = logging.getLogger(__name__)

_ANSIBLE_BUILTIN_MODULES = re.compile(r".+/ansible/modules$")
_COLLECTION_MODULES = re.compile(r".+/ansible_collections/([^/]+)/([^/]+)/plugins/modules$")

# ---------------------------------------------------------------------------
# Per-module classification for ansible.builtin.*
#
# Each entry maps the short name (after "ansible.builtin.") to a frozenset of
# Terraform type(s) to expose: "resource", "datasource", "ephemeral".
# An empty frozenset means the module is internal and not exported at all.
# ---------------------------------------------------------------------------

_NONE = frozenset()
_R = frozenset({"resource"})
_D = frozenset({"datasource"})
_E = frozenset({"ephemeral"})
_RE = frozenset({"resource", "ephemeral"})
_RDE = frozenset({"resource", "datasource", "ephemeral"})

_BUILTIN_CLASSIFICATION: dict[str, frozenset[str]] = {
    # --- Internal: no Terraform analog ---
    "debug": _NONE,  # action plugin, connection: none
    "assert": _NONE,  # action plugin, connection: none
    "fail": _NONE,  # action plugin, connection: none
    "set_fact": _NONE,  # sets in-memory Ansible vars only
    "pause": _NONE,  # requires TTY, connection: none
    "meta": _NONE,  # Ansible execution control (flush_handlers etc.)
    "include_vars": _NONE,  # loads YAML into in-memory Ansible store
    "add_host": _NONE,  # adds to in-memory Ansible inventory only
    "group_by": _NONE,  # mutates in-memory Ansible groups only
    "set_stats": _NONE,  # sets Ansible stats for callback plugins
    "validate_argument_spec": _NONE,  # validates role arg specs, no host state
    "import_playbook": _NONE,  # Ansible play-level directive
    "import_role": _NONE,  # Ansible play-level directive
    "import_tasks": _NONE,  # Ansible play-level directive
    "include_role": _NONE,  # Ansible play-level directive
    "include_tasks": _NONE,  # Ansible play-level directive
    "async_wrapper": _NONE,  # internal async executor, not user-facing
    "gather_facts": _NONE,  # action plugin wrapper around setup, no main()
    # --- Ephemeral: one-shot execution, no persistent host state ---
    "ping": _RDE,  # connectivity test: resource + datasource + ephemeral
    "command": _RE,  # used in legacy resource style and ephemeral modern APIs
    "shell": _RE,  # delegates to command with _uses_shell=True
    "raw": _RE,  # virtual module, entirely server-side
    "script": _RE,  # virtual module, runs local script on remote
    "expect": _RE,  # pexpect-based; always changed=True
    "reboot": _RE,  # one-shot reboot event; no state
    "wait_for": _RE,  # polling operation; no host state modified
    "wait_for_connection": _RE,  # polls connection availability; no state
    "tempfile": _RE,  # mkstemp/mkdtemp; always changed=True; no lifecycle
    "uri": _RE,  # HTTP client; fire-and-forget; not idempotent by default
    "fetch": _RE,  # virtual; copies remote→controller fs; no remote state
    "async_status": _E,  # reads async job state; tied to job lifetime not host
    # --- Datasource: purely read-only, no side effects ---
    "stat": _D,  # pure os.stat; always changed=False
    "slurp": _D,  # reads file content; always changed=False
    "find": _D,  # pure os.walk/glob; always changed=False
    "getent": _D,  # reads system databases (passwd, group, etc.); read-only
    "package_facts": _D,  # returns installed package info; read-only
    "service_facts": _D,  # returns service state; read-only
    "setup": _D,  # gathers host facts (OS, hardware, network); read-only
    "mount_facts": _D,  # reads mount info from /proc/mounts etc.; read-only
    # --- Resource: manages durable host state; idempotent; has state=absent ---
    # File/content management
    "file": _R,  # manages files, dirs, symlinks; state=absent deletes
    "copy": _R,  # copies content to remote; idempotent via checksum
    "template": _R,  # renders Jinja2 to remote; same semantics as copy
    "get_url": _R,  # downloads URL to remote file; idempotent via checksum
    "unarchive": _R,  # extracts archives; idempotent
    "assemble": _R,  # assembles fragments into a file; idempotent
    "blockinfile": _R,  # manages marked block in file; state=absent removes
    "lineinfile": _R,  # manages line in file; state=absent removes
    "replace": _R,  # regex in-place replacement; idempotent
    # Package/repository management
    "apt": _R,  # apt packages; state=absent removes
    "apt_key": _R,  # apt signing keys; state=absent removes
    "apt_repository": _R,  # apt repo sources; state=absent removes
    "deb822_repository": _R,  # deb822 apt repo files; state=absent removes
    "debconf": _R,  # debconf database entries; idempotent
    "dnf": _R,  # dnf/RPM packages; state=absent removes
    "dnf5": _R,  # dnf5 packages (Fedora 41+); state=absent removes
    "dpkg_selections": _R,  # dpkg package selection state; idempotent
    "pip": _R,  # Python packages; state=absent removes
    "package": _R,  # generic package manager; state=absent removes
    "rpm_key": _R,  # RPM signing keys; state=absent removes
    "yum_repository": _R,  # yum/dnf .repo files; state=absent removes
    # System configuration
    "cron": _R,  # cron jobs; state=absent removes
    "user": _R,  # system users; state=absent removes
    "group": _R,  # system groups; state=absent removes
    "hostname": _R,  # system hostname; idempotent
    "known_hosts": _R,  # ~/.ssh/known_hosts entries; state=absent removes
    "iptables": _R,  # firewall rules; state=absent removes
    "service": _R,  # service state/enablement; idempotent
    "systemd": _R,  # systemd unit state/enablement; idempotent
    "systemd_service": _R,  # systemd service variant; idempotent
    "sysvinit": _R,  # SysV init service state; idempotent
    # Source control
    "git": _R,  # git repo clones/checkouts; idempotent via HEAD compare
    "subversion": _R,  # SVN working copies; idempotent
}


def _classify(fqcn: str) -> frozenset[str]:
    """Return the set of Terraform types to expose for a module FQCN.

    Returns a frozenset containing any combination of "resource", "datasource",
    "ephemeral". An empty frozenset means the module is not exported at all.
    Community modules (non-ansible.builtin.*) are not yet classified and return
    empty, so they are skipped during discovery.
    """
    if not fqcn.startswith("ansible.builtin."):
        return _NONE
    short = fqcn[len("ansible.builtin.") :]
    return _BUILTIN_CLASSIFICATION.get(short, _NONE)


_TYPE_MAP = {
    "str": String(),
    "string": String(),
    "path": String(),
    "raw": NormalizedJson(),
    "dict": NormalizedJson(),
    "list": NormalizedJson(),
    "bool": Bool(),
    "boolean": Bool(),
    "int": Number(),
    "integer": Number(),
    "float": Number(),
}

# Always present on every task resource
_FRAMEWORK_ATTRS = [
    Attribute("id", String(), description="Unique task resource ID", computed=True),
    Attribute(
        "host_id",
        String(),
        description="ID of the `terrible_host` to run this task against",
        required=True,
        requires_replace=True,
    ),
    Attribute("changed", Bool(), description="Whether the task reported a change", computed=True),
    Attribute(
        "triggers",
        Map(String()),
        description="Arbitrary map of string values; any change triggers task re-execution",
        optional=True,
    ),
    Attribute(
        "timeout",
        Number(),
        description=f"Override the default execution timeout (seconds). Defaults to {_MODULE_TIMEOUT}.",
        optional=True,
    ),
    Attribute(
        "ignore_errors",
        Bool(),
        description="When true, a failed task does not raise a Terraform error.",
        optional=True,
    ),
    Attribute(
        "changed_when",
        String(),
        description="Jinja2 expression that overrides when the task is considered changed (e.g. 'false').",
        optional=True,
    ),
    Attribute(
        "failed_when",
        String(),
        description="Jinja2 expression that overrides when the task is considered failed.",
        optional=True,
    ),
    Attribute(
        "environment",
        Map(String()),
        description="Environment variables set for the task (map of name→value).",
        optional=True,
    ),
    Attribute(
        "async_seconds",
        Number(),
        description="Run the task asynchronously, timing out after this many seconds. 0 = synchronous (default).",
        optional=True,
    ),
    Attribute(
        "poll_interval",
        Number(),
        description="Polling interval in seconds when async_seconds > 0. Defaults to 15.",
        optional=True,
    ),
    Attribute(
        "delegate_to_id",
        String(),
        description="ID of another terrible_host to delegate execution to.",
        optional=True,
    ),
]

_FRAMEWORK_NAMES = {a.name for a in _FRAMEWORK_ATTRS}

# Data source framework attributes — no id/triggers/changed; just host_id
_DS_FRAMEWORK_ATTRS = [
    Attribute(
        "host_id",
        String(),
        description="ID of the `terrible_host` to run this data source against",
        required=True,
    ),
]
_DS_FRAMEWORK_NAMES = {a.name for a in _DS_FRAMEWORK_ATTRS}

# Ephemeral resource framework attributes — execution context only.
# No id/changed/triggers (no state), no changed_when (no drift), no async (synchronous open).
_EPHEMERAL_FRAMEWORK_ATTRS = [
    Attribute(
        "host_id",
        String(),
        description="ID of the `terrible_host` to run this ephemeral resource against",
        required=True,
    ),
    Attribute(
        "timeout",
        Number(),
        description=f"Override the default execution timeout (seconds). Defaults to {_MODULE_TIMEOUT}.",
        optional=True,
    ),
    Attribute(
        "ignore_errors",
        Bool(),
        description="When true, a failed task does not raise a Terraform error.",
        optional=True,
    ),
    Attribute(
        "failed_when",
        String(),
        description="Jinja2 expression that overrides when the task is considered failed.",
        optional=True,
    ),
    Attribute(
        "environment",
        Map(String()),
        description="Environment variables set for the task (map of name→value).",
        optional=True,
    ),
    Attribute(
        "delegate_to_id",
        String(),
        description="ID of another terrible_host to delegate execution to.",
        optional=True,
    ),
]
_EPHEMERAL_FRAMEWORK_NAMES = {a.name for a in _EPHEMERAL_FRAMEWORK_ATTRS}

_DOC_RE = re.compile(r"^DOCUMENTATION\s*=\s*[ru]?[\'\"]{3}(.*?)[\'\"]{3}", re.DOTALL | re.MULTILINE)
_RET_RE = re.compile(r"^RETURN\s*=\s*[ru]?[\'\"]{3}(.*?)[\'\"]{3}", re.DOTALL | re.MULTILINE)


def _check_mode_support(doc: dict) -> str:
    """Return 'full', 'partial', or 'none' from DOCUMENTATION attributes block."""
    return doc.get("attributes", {}).get("check_mode", {}).get("support", "none")


def _has_absent_state(options: dict) -> bool:
    """Return True if the module's 'state' option lists 'absent' as a valid choice."""
    state_opt = options.get("state", {})
    if not isinstance(state_opt, dict):
        return False
    choices = state_opt.get("choices", [])
    if isinstance(choices, list):
        return "absent" in choices
    return False


def _fqcn_for_path(path: str) -> str | None:
    directory = os.path.dirname(path)
    shortname = os.path.splitext(os.path.basename(path))[0]
    if _ANSIBLE_BUILTIN_MODULES.match(directory):
        return f"ansible.builtin.{shortname}"
    m = _COLLECTION_MODULES.match(directory)
    if m:
        return f"{m.group(1)}.{m.group(2)}.{shortname}"
    return None


def _parse_yaml_block(source: str, regex: re.Pattern) -> dict | None:
    m = regex.search(source)
    if not m:
        return None
    try:
        return yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return None


def _tf_type_for(ansible_type: str):
    return _TYPE_MAP.get(str(ansible_type).lower(), String())


_RST_INLINE = re.compile(
    r"([OCEVMP])\(([^)]*)\)|B\(([^)]*)\)|I\(([^)]*)\)|U\(([^)]*)\)"
    r"|L\(([^,)]*),([^)]*)\)|R\(([^,)]*),([^)]*)\)"
)


def _render_rst(text: str) -> str:
    """Translate Ansible RST inline markup to Markdown."""

    def _replace(m: re.Match) -> str:
        if m.group(1):  # O(...) C(...) E(...) V(...) M(...) P(...)
            return f"`{m.group(2)}`"
        if m.group(3) is not None:  # B(...)
            return f"**{m.group(3)}**"
        if m.group(4) is not None:  # I(...)
            return f"*{m.group(4)}*"
        if m.group(5) is not None:  # U(url)
            return m.group(5)
        if m.group(6) is not None:  # L(text,url)
            return f"[{m.group(6)}]({m.group(7)})"
        # R(text,ref) — internal Ansible cross-reference; keep text only
        return m.group(8)

    return _RST_INLINE.sub(_replace, text)


def _description(spec: dict) -> str:
    d = spec.get("description", "")
    raw = " ".join(d) if isinstance(d, list) else (d or "")
    return _render_rst(raw)


def _build_schema(options: dict, returns: dict) -> tuple[Schema, set[str]]:
    """
    Merge DOCUMENTATION options and RETURN entries into a Schema.

    - options-only → required/optional input attribute
    - returns-only → computed output attribute
    - in both     → optional + computed (passthrough: user may set it; Ansible will echo it back)

    Returns the Schema and the set of return-attribute names (for use in _execute).
    """
    option_names = set(options)
    # Return-only: declared in RETURN but not in options.
    # Used by _execute to know which attributes to pull from the Ansible result.
    # Fields present in both are passthroughs: user sets them, Ansible echoes them
    # back. We do NOT include those in return_names so _execute keeps the user value.
    return_names = {k for k in returns if k not in _FRAMEWORK_NAMES and k not in option_names}

    attrs: list[Attribute] = list(_FRAMEWORK_ATTRS)

    # Options (input attributes)
    for name, spec in options.items():
        if name in _FRAMEWORK_NAMES or not isinstance(spec, dict):
            continue
        required = bool(spec.get("required", False))
        # Fields present in both options and RETURN are passthroughs: the user sets
        # them and Ansible echoes the same value back. We keep the user's value rather
        # than overwriting it with the result, so these are plain optional inputs
        # (not computed). Only return-only fields (in return_names) are computed.
        in_return = name in return_names  # always False for option names by construction
        attrs.append(
            Attribute(
                name,
                _tf_type_for(spec.get("type", "str")),
                description=_description(spec),
                required=required and not in_return,
                optional=not required or in_return,
                computed=in_return,
            )
        )

    # Return-only outputs (not already added above)
    for name, spec in returns.items():
        if name in _FRAMEWORK_NAMES or name in option_names or not isinstance(spec, dict):
            continue
        attrs.append(
            Attribute(
                name,
                _tf_type_for(spec.get("type", "str")),
                description=_description(spec),
                computed=True,
            )
        )

    return Schema(attributes=attrs), return_names


def _resource_name_for(fqcn: str) -> str:
    if fqcn.startswith("ansible.builtin."):
        fqcn = fqcn[len("ansible.builtin.") :]
    return fqcn.replace(".", "_").replace("-", "_")


def _coerce_number(v):
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        try:
            return float(v)
        except (ValueError, TypeError):
            return None


def _coercers_for(schema: Schema, return_names: set[str]) -> dict:
    """
    Build a {attr_name: callable} map that coerces ansible result values to
    the Python type expected by the tf schema, guarding against mis-documented
    module RETURN blocks (e.g. command's `msg` is typed bool but ships a str).
    """
    coercers = {}
    for attr in schema.attributes:
        if attr.name not in return_names:
            continue
        if isinstance(attr.type, Bool):
            coercers[attr.name] = lambda v: bool(v) if v is not None else None
        elif isinstance(attr.type, Number):
            coercers[attr.name] = _coerce_number
        # else: String / NormalizedJson — accept as-is; NormalizedJson encodes on the way out
    return coercers


def _make_get_name(name: str):
    @classmethod
    def get_name(cls) -> str:
        return name

    return get_name


def make_task_class(fqcn: str, options: dict, returns: dict, check_mode_support: str = "none") -> type:
    """Return a unique TerribleTaskBase subclass for an Ansible task type."""
    rname = _resource_name_for(fqcn)
    schema, return_names = _build_schema(options, returns)
    coercers = _coercers_for(schema, return_names)
    return type(
        f"Terrible_{rname}",
        (TerribleTaskBase,),
        {
            "_module_name": fqcn,
            "_schema": schema,
            "_return_attr_names": return_names,
            "_return_attr_coercers": coercers,
            "_check_mode_support": check_mode_support,
            "_has_state_absent": _has_absent_state(options),
            "get_name": _make_get_name(rname),
        },
    )


def _build_datasource_schema(options: dict, returns: dict) -> tuple[Schema, set[str]]:
    """Like _build_schema but for data sources: uses _DS_FRAMEWORK_ATTRS, no id/triggers/changed."""
    option_names = set(options)
    return_names = {k for k in returns if k not in _DS_FRAMEWORK_NAMES and k not in option_names}

    attrs: list[Attribute] = list(_DS_FRAMEWORK_ATTRS)

    for name, spec in options.items():
        if name in _DS_FRAMEWORK_NAMES or not isinstance(spec, dict):
            continue
        required = bool(spec.get("required", False))
        attrs.append(
            Attribute(
                name,
                _tf_type_for(spec.get("type", "str")),
                description=_description(spec),
                required=required,
                optional=not required,
            )
        )

    for name, spec in returns.items():
        if name in _DS_FRAMEWORK_NAMES or name in option_names or not isinstance(spec, dict):
            continue
        attrs.append(
            Attribute(
                name,
                _tf_type_for(spec.get("type", "str")),
                description=_description(spec),
                computed=True,
            )
        )

    return Schema(attributes=attrs), return_names


def make_datasource_class(fqcn: str, options: dict, returns: dict) -> type:
    """Return a unique TerribleTaskDataSource subclass for an Ansible task type."""
    rname = _resource_name_for(fqcn)
    schema, return_names = _build_datasource_schema(options, returns)
    coercers = _coercers_for(schema, return_names)
    return type(
        f"TerribleDS_{rname}",
        (TerribleTaskDataSource,),
        {
            "_module_name": fqcn,
            "_schema": schema,
            "_return_attr_names": return_names,
            "_return_attr_coercers": coercers,
            "get_name": _make_get_name(rname),
        },
    )


def _build_ephemeral_schema(options: dict, returns: dict) -> tuple[Schema, set[str]]:
    """Like _build_schema but for ephemeral resources.

    Uses _EPHEMERAL_FRAMEWORK_ATTRS — no id, changed, triggers, changed_when, or
    async attrs, because ephemeral resources have no persistent state and always
    execute fresh on open(). Return values are computed outputs on the result.
    """
    option_names = set(options)
    return_names = {k for k in returns if k not in _EPHEMERAL_FRAMEWORK_NAMES and k not in option_names}

    attrs: list[Attribute] = list(_EPHEMERAL_FRAMEWORK_ATTRS)

    for name, spec in options.items():
        if name in _EPHEMERAL_FRAMEWORK_NAMES or not isinstance(spec, dict):
            continue
        required = bool(spec.get("required", False))
        attrs.append(
            Attribute(
                name,
                _tf_type_for(spec.get("type", "str")),
                description=_description(spec),
                required=required,
                optional=not required,
            )
        )

    for name, spec in returns.items():
        if name in _EPHEMERAL_FRAMEWORK_NAMES or name in option_names or not isinstance(spec, dict):
            continue
        attrs.append(
            Attribute(
                name,
                _tf_type_for(spec.get("type", "str")),
                description=_description(spec),
                computed=True,
            )
        )

    return Schema(attributes=attrs), return_names


def make_ephemeral_class(fqcn: str, options: dict, returns: dict) -> type:
    """Return a unique EphemeralResource subclass for an Ansible task type."""
    from .ephemeral import EphemeralResource

    rname = _resource_name_for(fqcn)
    schema, return_names = _build_ephemeral_schema(options, returns)
    coercers = _coercers_for(schema, return_names)
    return type(
        f"TerribleEph_{rname}",
        (EphemeralResource,),
        {
            "_module_name": fqcn,
            "_schema": schema,
            "_return_attr_names": return_names,
            "_return_attr_coercers": coercers,
            "get_name": _make_get_name(rname),
        },
    )


def _get_installed_collections(collection_paths=None) -> set[str]:
    """Return the set of 'namespace.collection' strings found in collection_paths.

    When *collection_paths* is None, reads ``ansible.constants.COLLECTIONS_PATHS``.
    """
    if collection_paths is None:
        try:
            import ansible.constants as C

            collection_paths = C.COLLECTIONS_PATHS or []  # type: ignore[attr-defined]
        except ImportError:
            return set()

    installed: set[str] = set()
    for cp in collection_paths:
        ac_dir = Path(cp) / "ansible_collections"
        if not ac_dir.is_dir():
            continue
        try:
            for ns_dir in ac_dir.iterdir():
                if not ns_dir.is_dir() or ns_dir.name.startswith("."):
                    continue
                for coll_dir in ns_dir.iterdir():
                    if not coll_dir.is_dir() or coll_dir.name.startswith("."):
                        continue
                    installed.add(f"{ns_dir.name}.{coll_dir.name}")
        except OSError:
            pass
    return installed


def _iter_collection_module_paths(collection_paths=None):
    """Yield absolute paths to every module .py file in installed collections.

    Checks both Ansible's COLLECTIONS_PATHS and venv site-packages, since
    pip/uv installs collections into site-packages rather than ~/.ansible/collections.
    """
    if collection_paths is None:
        import site

        try:
            import ansible.constants as C

            collection_paths = list(C.COLLECTIONS_PATHS or [])  # type: ignore[attr-defined]
        except ImportError:
            collection_paths = []

        # Also search venv/system site-packages for pip-installed collections.
        for sp in site.getsitepackages():
            if sp not in collection_paths:
                collection_paths.append(sp)

    seen: set[str] = set()
    for cp in collection_paths:
        ac_dir = Path(cp) / "ansible_collections"
        if not ac_dir.is_dir():
            continue
        try:
            for modules_dir in ac_dir.glob("*/*/plugins/modules"):
                for mod_file in modules_dir.iterdir():
                    if mod_file.suffix == ".py" and not mod_file.name.startswith("_"):
                        key = str(mod_file)
                        if key not in seen:
                            seen.add(key)
                            yield key
        except OSError:
            pass


def _cache_db_path() -> Path:
    cache_dir = Path.home() / ".cache" / "tf-python-provider"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "discovery.db"


def _open_cache() -> sqlite3.Connection:
    db = sqlite3.connect(_cache_db_path())
    db.execute("""
        CREATE TABLE IF NOT EXISTS discovery_cache (
            ansible_version TEXT NOT NULL,
            fqcn            TEXT NOT NULL,
            options_json    TEXT NOT NULL,
            returns_json    TEXT NOT NULL,
            check_mode      TEXT NOT NULL,
            classification  TEXT NOT NULL DEFAULT 'resource',
            PRIMARY KEY (ansible_version, fqcn)
        )
    """)
    db.commit()
    # Migrate old schema (5-column) by adding the classification column.
    # If the column is newly added all old rows had wrong classifications,
    # so clear them to force re-discovery with correct types.
    try:
        db.execute("ALTER TABLE discovery_cache ADD COLUMN classification TEXT NOT NULL DEFAULT 'resource'")
        db.execute("DELETE FROM discovery_cache")
        db.commit()
    except sqlite3.OperationalError:
        pass  # column already exists — cache is up to date
    return db


def _load_cached(db: sqlite3.Connection, ansible_version: str) -> tuple[list[type], list[type], list[type]] | None:
    _SQL = (
        "SELECT fqcn, options_json, returns_json, check_mode, classification"
        " FROM discovery_cache WHERE ansible_version = ?"
    )
    rows = db.execute(_SQL, (ansible_version,)).fetchall()
    if not rows:
        return None
    resources = []
    datasources = []
    ephemerals = []
    for fqcn, options_json, returns_json, check_mode, classification in rows:
        try:
            options = json.loads(options_json)
            returns = json.loads(returns_json)
            cached_types = frozenset(c for c in classification.split(",") if c)
            current_types = _classify(fqcn)
            if cached_types != current_types:
                log.debug(
                    "Discovery cache classification mismatch for %s: cached=%s current=%s",
                    fqcn,
                    cached_types,
                    current_types,
                )
                return None
            if "resource" in cached_types:
                resources.append(make_task_class(fqcn, options, returns, check_mode))
            if "datasource" in cached_types:
                datasources.append(make_datasource_class(fqcn, options, returns))
            if "ephemeral" in cached_types:
                ephemerals.append(make_ephemeral_class(fqcn, options, returns))
        except Exception as exc:
            log.debug("Failed to restore cached class for %s: %s", fqcn, exc)
    return resources, datasources, ephemerals


def _save_cache(db: sqlite3.Connection, ansible_version: str, rows: list[tuple]) -> None:
    # Drop stale entries for other Ansible versions to keep the DB small.
    db.execute("DELETE FROM discovery_cache WHERE ansible_version != ?", (ansible_version,))
    db.executemany(
        "INSERT OR REPLACE INTO discovery_cache VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    db.commit()


def discover_task_resources() -> tuple[list[type], list[type], list[type]]:
    """
    Walk all Ansible builtin module paths and return (resources, datasources, ephemerals).

    resources   — Resource subclasses for modules that manage durable host state
    datasources — DataSource subclasses for purely read-only modules
    ephemerals  — EphemeralResource subclasses for one-shot execution modules

    Modules with no classification (internal Ansible modules, community modules not
    yet classified) are silently skipped.

    Results are cached in SQLite (~/.cache/tf-python-provider/discovery.db)
    keyed by Ansible version, so the expensive filesystem walk and YAML
    parsing only happens once per Ansible installation.
    """
    try:
        import ansible
        from ansible.plugins.loader import module_loader
    except ImportError:
        log.warning("ansible not importable; no task resources will be registered")
        return [], [], []

    ansible_version = ansible.__version__

    db = None
    try:
        db = _open_cache()
        cached = _load_cached(db, ansible_version)
        if cached is not None:
            resources, datasources, ephemerals = cached
            log.info(
                "Loaded %d resources, %d datasources, %d ephemerals from cache (ansible %s)",
                len(resources),
                len(datasources),
                len(ephemerals),
                ansible_version,
            )
            return resources, datasources, ephemerals
    except Exception as exc:
        log.debug("Discovery cache unavailable: %s", exc)
        if db is not None:
            with contextlib.suppress(Exception):
                db.close()
        db = None

    # Cache miss — do the full filesystem walk.
    resources: list[type] = []
    datasources: list[type] = []
    ephemerals: list[type] = []
    cache_rows: list[tuple] = []
    seen_fqcns: set[str] = set()

    try:
        for path in module_loader.all(path_only=True):
            if not path or not path.endswith(".py") or os.path.basename(path).startswith("_"):
                continue

            fqcn = _fqcn_for_path(path)
            if fqcn is None or fqcn in seen_fqcns:
                continue
            seen_fqcns.add(fqcn)

            types = _classify(fqcn)
            if not types:
                continue

            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    source = f.read()
            except OSError:
                continue

            doc = _parse_yaml_block(source, _DOC_RE)
            if doc is None:
                continue

            options = doc.get("options") or {}
            returns = _parse_yaml_block(source, _RET_RE) or {}
            support = _check_mode_support(doc)
            classification = ",".join(sorted(types))

            try:
                if "resource" in types:
                    resources.append(make_task_class(fqcn, options, returns, check_mode_support=support))
                    log.debug("Registered resource: %s", fqcn)
                if "datasource" in types:
                    datasources.append(make_datasource_class(fqcn, options, returns))
                    log.debug("Registered datasource: %s", fqcn)
                if "ephemeral" in types:
                    ephemerals.append(make_ephemeral_class(fqcn, options, returns))
                    log.debug("Registered ephemeral: %s", fqcn)
                cache_rows.append(
                    (ansible_version, fqcn, json.dumps(options), json.dumps(returns), support, classification)
                )
            except Exception as exc:
                log.debug("Failed to build class for %s: %s", fqcn, exc)

        log.info(
            "Discovered %d resources, %d datasources, %d ephemerals",
            len(resources),
            len(datasources),
            len(ephemerals),
        )

        if db is not None and cache_rows:
            try:
                _save_cache(db, ansible_version, cache_rows)
                log.debug("Saved discovery cache for ansible %s", ansible_version)
            except Exception as exc:
                log.debug("Failed to save discovery cache: %s", exc)
    finally:
        if db is not None:
            with contextlib.suppress(Exception):
                db.close()

    return resources, datasources, ephemerals
