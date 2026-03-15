"""
Dynamic discovery of Ansible task types and creation of per-task Resource subclasses.

Each Ansible module file embeds DOCUMENTATION (input options) and RETURN (output
attributes) YAML blocks. We parse both to build a full tf Schema — options become
required/optional attributes, return values become computed attributes — then
dynamically subclass TerribleTaskBase for each discovered task type.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

import yaml

from tf.schema import Schema, Attribute
from tf.types import Bool, NormalizedJson, Number, String

from .task_base import TerribleTaskBase

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
]

_FRAMEWORK_NAMES = {a.name for a in _FRAMEWORK_ATTRS}

_DOC_RE = re.compile(r'^DOCUMENTATION\s*=\s*[ru]?[\'\"]{3}(.*?)[\'\"]{3}', re.DOTALL | re.MULTILINE)
_RET_RE = re.compile(r'^RETURN\s*=\s*[ru]?[\'\"]{3}(.*?)[\'\"]{3}', re.DOTALL | re.MULTILINE)


def _check_mode_support(doc: dict) -> str:
    """Return 'full', 'partial', or 'none' from DOCUMENTATION attributes block."""
    return (
        doc.get("attributes", {})
           .get("check_mode", {})
           .get("support", "none")
    )


def _fqcn_for_path(path: str) -> Optional[str]:
    directory = os.path.dirname(path)
    shortname = os.path.splitext(os.path.basename(path))[0]
    if _ANSIBLE_BUILTIN_MODULES.match(directory):
        return f"ansible.builtin.{shortname}"
    m = _COLLECTION_MODULES.match(directory)
    if m:
        return f"{m.group(1)}.{m.group(2)}.{shortname}"
    return None


def _parse_yaml_block(source: str, regex: re.Pattern) -> Optional[dict]:
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
    return_names = {k for k in returns if k not in _FRAMEWORK_NAMES}

    attrs: list[Attribute] = list(_FRAMEWORK_ATTRS)

    # Input-only options
    for name, spec in options.items():
        if name in _FRAMEWORK_NAMES or not isinstance(spec, dict):
            continue
        required = bool(spec.get("required", False))
        in_return = name in return_names
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
        fqcn = fqcn[len("ansible.builtin."):]
    return fqcn.replace(".", "_").replace("-", "_")


def make_task_class(fqcn: str, options: dict, returns: dict, check_mode_support: str = "none") -> type:
    """Return a unique TerribleTaskBase subclass for an Ansible task type."""
    rname = _resource_name_for(fqcn)
    schema, return_names = _build_schema(options, returns)
    return type(
        f"Terrible_{rname}",
        (TerribleTaskBase,),
        {
            "_module_name": fqcn,
            "_schema": schema,
            "_return_attr_names": return_names,
            "_check_mode_support": check_mode_support,
            "get_name": classmethod(lambda cls, _n=rname: _n),
        },
    )


def discover_task_resources() -> list[type]:
    """
    Walk all Ansible module paths and return one Resource subclass per
    discovered task type.
    """
    try:
        from ansible.plugins.loader import module_loader
    except ImportError:
        log.warning("ansible not importable; no task resources will be registered")
        return []

    resources: list[type] = []
    seen_fqcns: set[str] = set()

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
            log.debug("Registered task type: %s", fqcn)
        except Exception as exc:
            log.debug("Failed to build class for %s: %s", fqcn, exc)

    log.info("Discovered %d Ansible task types", len(resources))
    return resources
