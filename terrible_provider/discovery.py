"""
Dynamic discovery of Ansible task types and creation of per-task Resource subclasses.

Each Ansible module file embeds a DOCUMENTATION YAML string that describes its
options. We parse that to build a tf Schema, then dynamically subclass
TerribleTaskBase for each discovered task type.
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

_FRAMEWORK_ATTRS = [
    Attribute("id", String(), description="Unique task resource ID", computed=True),
    Attribute(
        "host_id",
        String(),
        description="ID of the `terrible_host` to run this task against",
        required=True,
        requires_replace=True,
    ),
    Attribute("result", NormalizedJson(), description="JSON result returned by Ansible", computed=True),
    Attribute("changed", Bool(), description="Whether the task reported a change", computed=True),
]

_DOC_RE = re.compile(r'^DOCUMENTATION\s*=\s*[ru]?[\'\"]{3}(.*?)[\'\"]{3}', re.DOTALL | re.MULTILINE)


def _fqcn_for_path(path: str) -> Optional[str]:
    """Derive the Ansible FQCN for a module file path."""
    directory = os.path.dirname(path)
    shortname = os.path.splitext(os.path.basename(path))[0]

    if _ANSIBLE_BUILTIN_MODULES.match(directory):
        return f"ansible.builtin.{shortname}"

    m = _COLLECTION_MODULES.match(directory)
    if m:
        namespace, collection = m.group(1), m.group(2)
        return f"{namespace}.{collection}.{shortname}"

    return None


def _parse_documentation(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            source = f.read()
    except OSError:
        return None
    m = _DOC_RE.search(source)
    if not m:
        return None
    try:
        return yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return None


def _tf_type_for(ansible_type: str):
    return _TYPE_MAP.get(str(ansible_type).lower(), String())


def _options_to_attrs(options: dict) -> list[Attribute]:
    attrs = []
    for name, spec in (options or {}).items():
        if not isinstance(spec, dict):
            continue
        atype = spec.get("type", "str")
        required = bool(spec.get("required", False))
        description = spec.get("description", "")
        if isinstance(description, list):
            description = " ".join(description)
        attrs.append(
            Attribute(
                name,
                _tf_type_for(atype),
                description=description,
                required=required,
                optional=not required,
            )
        )
    return attrs


def _resource_name_for(fqcn: str) -> str:
    if fqcn.startswith("ansible.builtin."):
        fqcn = fqcn[len("ansible.builtin."):]
    return fqcn.replace(".", "_").replace("-", "_")


def make_task_class(fqcn: str, options: dict) -> type:
    """Return a unique TerribleTaskBase subclass for an Ansible task type."""
    rname = _resource_name_for(fqcn)
    schema = Schema(attributes=_FRAMEWORK_ATTRS + _options_to_attrs(options))
    return type(
        f"Terrible_{rname}",
        (TerribleTaskBase,),
        {
            "_module_name": fqcn,
            "_schema": schema,
            "get_name": classmethod(lambda cls, _n=rname: _n),
        },
    )


def discover_task_resources() -> list[type]:
    """
    Walk all Ansible module paths and return one Resource subclass per
    discovered task type, with schema built from each module's DOCUMENTATION.
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

        doc = _parse_documentation(path)
        if doc is None:
            continue

        options = doc.get("options") or {}
        try:
            klass = make_task_class(fqcn, options)
            resources.append(klass)
            log.debug("Registered task type: %s", fqcn)
        except Exception as exc:
            log.debug("Failed to build class for %s: %s", fqcn, exc)

    log.info("Discovered %d Ansible task types", len(resources))
    return resources
