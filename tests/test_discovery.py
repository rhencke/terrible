"""Unit tests for discovery schema-building and class-factory functions."""

import pytest

from tf.types import Bool, NormalizedJson, Number, String

from terrible_provider.discovery import (
    _build_datasource_schema,
    _build_schema,
    _resource_name_for,
    make_datasource_class,
    make_task_class,
)
from terrible_provider.task_base import TerribleTaskBase
from terrible_provider.task_datasource import TerribleTaskDataSource


# ---------------------------------------------------------------------------
# _resource_name_for
# ---------------------------------------------------------------------------

class TestResourceNameFor:
    def test_builtin_strips_prefix(self):
        assert _resource_name_for("ansible.builtin.ping") == "ping"

    def test_collection_module(self):
        assert _resource_name_for("community.general.git_config") == "community_general_git_config"

    def test_hyphens_converted(self):
        assert _resource_name_for("my.col.some-module") == "my_col_some_module"


# ---------------------------------------------------------------------------
# _build_schema
# ---------------------------------------------------------------------------

class TestBuildSchema:
    def test_required_option_is_required(self):
        options = {"path": {"type": "str", "required": True, "description": "A path"}}
        schema, return_names = _build_schema(options, {})
        attr = next(a for a in schema.attributes if a.name == "path")
        assert attr.required
        assert not attr.computed
        assert not attr.optional

    def test_optional_option(self):
        options = {"mode": {"type": "str", "description": "File mode"}}
        schema, _ = _build_schema(options, {})
        attr = next(a for a in schema.attributes if a.name == "mode")
        assert attr.optional
        assert not attr.required
        assert not attr.computed

    def test_return_only_is_computed(self):
        schema, return_names = _build_schema({}, {"rc": {"type": "int", "description": "Return code"}})
        attr = next(a for a in schema.attributes if a.name == "rc")
        assert attr.computed
        assert not attr.required
        assert not attr.optional
        assert "rc" in return_names

    def test_framework_attrs_always_present(self):
        schema, _ = _build_schema({}, {})
        names = {a.name for a in schema.attributes}
        assert {"id", "host_id", "result", "changed", "triggers"} <= names

    def test_framework_names_excluded_from_options(self):
        # If a module happens to declare 'id' or 'changed' as an option, skip it
        options = {"id": {"type": "str"}, "path": {"type": "str"}}
        schema, _ = _build_schema(options, {})
        names = [a.name for a in schema.attributes]
        assert names.count("id") == 1  # only the framework id, not duplicated

    def test_type_mapping(self):
        options = {
            "flag": {"type": "bool"},
            "count": {"type": "int"},
            "data": {"type": "dict"},
        }
        schema, _ = _build_schema(options, {})
        attr_map = {a.name: a for a in schema.attributes}
        assert isinstance(attr_map["flag"].type, Bool)
        assert isinstance(attr_map["count"].type, Number)
        assert isinstance(attr_map["data"].type, NormalizedJson)

    def test_return_names_excludes_option_names(self):
        # Fields in both options and returns are NOT in return_names
        # (the resource keeps the user's value, not Ansible's echo)
        options = {"path": {"type": "str"}}
        returns = {"path": {"type": "str"}, "uid": {"type": "int"}}
        _, return_names = _build_schema(options, returns)
        assert "path" not in return_names
        assert "uid" in return_names


# ---------------------------------------------------------------------------
# _build_datasource_schema
# ---------------------------------------------------------------------------

class TestBuildDatasourceSchema:
    def test_has_host_id_and_result(self):
        schema, _ = _build_datasource_schema({}, {})
        names = {a.name for a in schema.attributes}
        assert "host_id" in names
        assert "result" in names

    def test_no_id_triggers_changed(self):
        schema, _ = _build_datasource_schema({}, {})
        names = {a.name for a in schema.attributes}
        assert "id" not in names
        assert "triggers" not in names
        assert "changed" not in names

    def test_options_included(self):
        options = {"path": {"type": "str", "required": True}}
        schema, _ = _build_datasource_schema(options, {})
        names = {a.name for a in schema.attributes}
        assert "path" in names

    def test_return_only_computed(self):
        schema, return_names = _build_datasource_schema({}, {"stat": {"type": "dict"}})
        attr = next(a for a in schema.attributes if a.name == "stat")
        assert attr.computed
        assert "stat" in return_names


# ---------------------------------------------------------------------------
# make_task_class
# ---------------------------------------------------------------------------

class TestMakeTaskClass:
    def test_is_subclass_of_task_base(self):
        klass = make_task_class("ansible.builtin.ping", {}, {})
        assert issubclass(klass, TerribleTaskBase)

    def test_name_is_ping(self):
        klass = make_task_class("ansible.builtin.ping", {}, {})
        assert klass.get_name() == "ping"

    def test_module_name_stored(self):
        klass = make_task_class("ansible.builtin.ping", {}, {})
        assert klass._module_name == "ansible.builtin.ping"

    def test_check_mode_stored(self):
        klass = make_task_class("ansible.builtin.ping", {}, {}, check_mode_support="full")
        assert klass._check_mode_support == "full"

    def test_unique_classes_per_fqcn(self):
        a = make_task_class("ansible.builtin.ping", {}, {})
        b = make_task_class("ansible.builtin.copy", {}, {})
        assert a is not b
        assert a.get_name() != b.get_name()

    def test_get_name_closure_is_correct(self):
        # Classic Python closure-in-loop trap: each class must capture its own name
        classes = [make_task_class(f"ansible.builtin.mod{i}", {}, {}) for i in range(3)]
        names = [c.get_name() for c in classes]
        assert names == [f"mod{i}" for i in range(3)]


# ---------------------------------------------------------------------------
# make_datasource_class
# ---------------------------------------------------------------------------

class TestMakeDatasourceClass:
    def test_is_subclass_of_datasource(self):
        klass = make_datasource_class("ansible.builtin.ping", {}, {})
        assert issubclass(klass, TerribleTaskDataSource)

    def test_name_matches_resource(self):
        klass = make_datasource_class("ansible.builtin.ping", {}, {})
        assert klass.get_name() == "ping"

    def test_module_name_stored(self):
        klass = make_datasource_class("ansible.builtin.ping", {}, {})
        assert klass._module_name == "ansible.builtin.ping"

    def test_distinct_from_resource_class(self):
        resource = make_task_class("ansible.builtin.ping", {}, {})
        datasource = make_datasource_class("ansible.builtin.ping", {}, {})
        assert resource is not datasource
        assert not issubclass(resource, TerribleTaskDataSource)
        assert not issubclass(datasource, TerribleTaskBase)
