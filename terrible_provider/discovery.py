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
from tf.types import Bool, NormalizedJson, Number, String

from .task_base import _MODULE_TIMEOUT, TerribleTaskBase
from .task_datasource import TerribleTaskDataSource

log = logging.getLogger(__name__)

_ANSIBLE_BUILTIN_MODULES = re.compile(r".+/ansible/modules$")
_COLLECTION_MODULES = re.compile(r".+/ansible_collections/([^/]+)/([^/]+)/plugins/modules$")

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
    Attribute("result", NormalizedJson(), description="Full raw JSON result from Ansible", computed=True),
    Attribute("changed", Bool(), description="Whether the task reported a change", computed=True),
    Attribute(
        "triggers",
        NormalizedJson(),
        description="Arbitrary map of values; any change triggers task re-execution",
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
        NormalizedJson(),
        description="Environment variables set for the task (dict of name→value).",
        optional=True,
    ),
    Attribute(
        "tags",
        NormalizedJson(),
        description="Run only tasks with these Ansible tags (list of strings).",
        optional=True,
    ),
    Attribute(
        "skip_tags",
        NormalizedJson(),
        description="Skip tasks with these Ansible tags (list of strings).",
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

# Data source framework attributes — no id/triggers/changed; just host_id and result
_DS_FRAMEWORK_ATTRS = [
    Attribute(
        "host_id",
        String(),
        description="ID of the `terrible_host` to run this data source against",
        required=True,
    ),
    Attribute("result", NormalizedJson(), description="Full raw JSON result from Ansible", computed=True),
]
_DS_FRAMEWORK_NAMES = {a.name for a in _DS_FRAMEWORK_ATTRS}

_DOC_RE = re.compile(r"^DOCUMENTATION\s*=\s*[ru]?[\'\"]{3}(.*?)[\'\"]{3}", re.DOTALL | re.MULTILINE)
_RET_RE = re.compile(r"^RETURN\s*=\s*[ru]?[\'\"]{3}(.*?)[\'\"]{3}", re.DOTALL | re.MULTILINE)


def _check_mode_support(doc: dict) -> str:
    """Return 'full', 'partial', or 'none' from DOCUMENTATION attributes block."""
    return doc.get("attributes", {}).get("check_mode", {}).get("support", "none")


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


def _description(spec: dict) -> str:
    d = spec.get("description", "")
    return " ".join(d) if isinstance(d, list) else (d or "")


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


def _get_installed_collections(collection_paths=None) -> set[str]:
    """Return the set of 'namespace.collection' strings found in collection_paths.

    When *collection_paths* is None, reads ``ansible.constants.COLLECTIONS_PATHS``.
    """
    if collection_paths is None:
        try:
            import ansible.constants as C

            collection_paths = C.COLLECTIONS_PATHS or []
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
            PRIMARY KEY (ansible_version, fqcn)
        )
    """)
    db.commit()
    return db


def _load_cached(db: sqlite3.Connection, ansible_version: str) -> tuple[list[type], list[type]] | None:
    rows = db.execute(
        "SELECT fqcn, options_json, returns_json, check_mode FROM discovery_cache WHERE ansible_version = ?",
        (ansible_version,),
    ).fetchall()
    if not rows:
        return None
    resources = []
    datasources = []
    for fqcn, options_json, returns_json, check_mode in rows:
        try:
            options = json.loads(options_json)
            returns = json.loads(returns_json)
            klass = make_task_class(fqcn, options, returns, check_mode)
            resources.append(klass)
            if check_mode == "full":
                datasources.append(make_datasource_class(fqcn, options, returns))
        except Exception as exc:
            log.debug("Failed to restore cached class for %s: %s", fqcn, exc)
    return resources, datasources


def _save_cache(db: sqlite3.Connection, ansible_version: str, rows: list[tuple]) -> None:
    # Drop stale entries for other Ansible versions to keep the DB small.
    db.execute("DELETE FROM discovery_cache WHERE ansible_version != ?", (ansible_version,))
    db.executemany(
        "INSERT OR REPLACE INTO discovery_cache VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    db.commit()


def discover_task_resources() -> tuple[list[type], list[type]]:
    """
    Walk all Ansible module paths and return (resources, datasources).

    resources   — one Resource subclass per discovered task type
    datasources — one DataSource subclass for each module with check_mode == "full"

    Results are cached in SQLite (~/.cache/tf-python-provider/discovery.db)
    keyed by Ansible version, so the expensive filesystem walk and YAML
    parsing only happens once per Ansible installation.
    """
    try:
        import ansible
        from ansible.plugins.loader import module_loader
    except ImportError:
        log.warning("ansible not importable; no task resources will be registered")
        return [], []

    ansible_version = ansible.__version__

    db = None
    try:
        db = _open_cache()
        cached = _load_cached(db, ansible_version)
        if cached is not None:
            resources, datasources = cached
            log.info(
                "Loaded %d Ansible task types (%d data sources) from cache (ansible %s)",
                len(resources),
                len(datasources),
                ansible_version,
            )
            return resources, datasources
    except Exception as exc:
        log.debug("Discovery cache unavailable: %s", exc)
        if db is not None:
            with contextlib.suppress(Exception):
                db.close()
        db = None

    # Cache miss — do the full filesystem walk.
    resources: list[type] = []
    datasources: list[type] = []
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

            try:
                klass = make_task_class(fqcn, options, returns, check_mode_support=support)
                resources.append(klass)
                cache_rows.append((ansible_version, fqcn, json.dumps(options), json.dumps(returns), support))
                log.debug("Registered task type: %s", fqcn)
                if support == "full":
                    datasources.append(make_datasource_class(fqcn, options, returns))
                    log.debug("Registered data source type: %s", fqcn)
            except Exception as exc:
                log.debug("Failed to build class for %s: %s", fqcn, exc)

        # Warn about installed collections that contributed no discoverable modules.
        seen_collections = {
            ".".join(fqcn.split(".")[:2]) for fqcn in seen_fqcns if not fqcn.startswith("ansible.builtin.")
        }
        try:
            for coll in sorted(_get_installed_collections() - seen_collections):
                log.warning(
                    "Installed collection '%s' contributed no discoverable modules; "
                    "check that it is correctly installed",
                    coll,
                )
        except Exception as exc:
            log.debug("Collection presence check failed: %s", exc)

        log.info("Discovered %d Ansible task types (%d data sources)", len(resources), len(datasources))

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

    return resources, datasources
