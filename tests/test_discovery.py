"""Unit tests for discovery schema-building and class-factory functions."""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from tf.types import Bool, Map, NormalizedJson, Number, String

from terrible_provider.discovery import (
    _DOC_RE,
    _build_datasource_schema,
    _build_ephemeral_schema,
    _build_schema,
    _cache_db_path,
    _check_mode_support,
    _classify,
    _fqcn_for_path,
    _get_installed_collections,
    _has_absent_state,
    _iter_collection_module_paths,
    _load_cached,
    _open_cache,
    _parse_yaml_block,
    _render_rst,
    _resource_name_for,
    _save_cache,
    discover_task_resources,
    make_datasource_class,
    make_ephemeral_class,
    make_task_class,
)
from terrible_provider.ephemeral import EphemeralResource
from terrible_provider.task_base import TerribleTaskBase
from terrible_provider.task_datasource import TerribleTaskDataSource

# ---------------------------------------------------------------------------
# Fake module file content for filesystem-walk tests
# ---------------------------------------------------------------------------

_FAKE_MODULE = '''\
DOCUMENTATION = """
short_description: Fake module
options:
  path:
    description: A path.
    type: str
    required: true
  state:
    description: State.
    type: str
    choices: [present, absent]
attributes:
  check_mode:
    support: full
"""
RETURN = """
stat:
  description: Stat result.
  type: dict
"""
'''

_FAKE_MODULE_NO_STATE = '''\
DOCUMENTATION = """
short_description: Fake module without state option
options:
  name:
    description: A name.
    type: str
"""
RETURN = """
"""
'''


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
# _classify
# ---------------------------------------------------------------------------


class TestClassify:
    def test_resource_module(self):
        assert _classify("ansible.builtin.file") == {"resource"}

    def test_datasource_module(self):
        assert _classify("ansible.builtin.stat") == {"datasource"}

    def test_ephemeral_module(self):
        assert _classify("ansible.builtin.async_status") == {"ephemeral"}

    def test_internal_module_is_empty(self):
        assert _classify("ansible.builtin.debug") == set()

    def test_community_module_is_empty(self):
        assert _classify("community.general.git_config") == set()

    def test_unknown_builtin_is_empty(self):
        assert _classify("ansible.builtin.nonexistent_module") == set()

    def test_all_resource_modules_classified(self):
        for name in ["copy", "template", "get_url", "apt", "dnf", "user", "group", "git"]:
            assert "resource" in _classify(f"ansible.builtin.{name}"), f"{name} should be resource"

    def test_all_datasource_modules_classified(self):
        for name in ["slurp", "find", "getent", "package_facts", "service_facts", "setup", "mount_facts"]:
            assert "datasource" in _classify(f"ansible.builtin.{name}"), f"{name} should be datasource"

    def test_all_ephemeral_modules_classified(self):
        for name in ["command", "shell", "raw", "script", "reboot", "wait_for", "uri", "fetch"]:
            assert "ephemeral" in _classify(f"ansible.builtin.{name}"), f"{name} should be ephemeral"

    def test_transition_command_is_resource_and_ephemeral(self):
        cls = _classify("ansible.builtin.command")
        assert "resource" in cls and "ephemeral" in cls

    def test_all_internal_modules_empty(self):
        for name in ["debug", "assert", "fail", "set_fact", "meta", "pause", "gather_facts"]:
            assert _classify(f"ansible.builtin.{name}") == set(), f"{name} should be empty"


# ---------------------------------------------------------------------------
# _has_absent_state
# ---------------------------------------------------------------------------


class TestHasAbsentState:
    def test_returns_true_when_absent_in_choices(self):
        options = {"state": {"type": "str", "choices": ["present", "absent"]}}
        assert _has_absent_state(options) is True

    def test_returns_false_when_no_state_option(self):
        assert _has_absent_state({"path": {"type": "str"}}) is False

    def test_returns_false_when_absent_not_in_choices(self):
        options = {"state": {"type": "str", "choices": ["present", "latest"]}}
        assert _has_absent_state(options) is False

    def test_returns_false_when_choices_empty(self):
        options = {"state": {"type": "str", "choices": []}}
        assert _has_absent_state(options) is False

    def test_returns_false_when_state_not_a_dict(self):
        assert _has_absent_state({"state": "present"}) is False

    def test_returns_false_when_choices_not_a_list(self):
        options = {"state": {"type": "str", "choices": "present/absent"}}
        assert _has_absent_state(options) is False


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
        assert {"id", "host_id", "changed", "triggers"} <= names

    def test_framework_attr_types(self):
        schema, _ = _build_schema({}, {})
        attr_map = {a.name: a for a in schema.attributes}
        assert isinstance(attr_map["triggers"].type, Map)
        assert isinstance(attr_map["triggers"].type.value_type, String)
        assert isinstance(attr_map["environment"].type, Map)
        assert isinstance(attr_map["environment"].type.value_type, String)
        assert "tags" not in attr_map
        assert "skip_tags" not in attr_map

    def test_framework_names_excluded_from_options(self):
        options = {"id": {"type": "str"}, "path": {"type": "str"}}
        schema, _ = _build_schema(options, {})
        names = [a.name for a in schema.attributes]
        assert names.count("id") == 1

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
        options = {"path": {"type": "str"}}
        returns = {"path": {"type": "str"}, "uid": {"type": "int"}}
        _, return_names = _build_schema(options, returns)
        assert "path" not in return_names
        assert "uid" in return_names


# ---------------------------------------------------------------------------
# _build_datasource_schema
# ---------------------------------------------------------------------------


class TestBuildDatasourceSchema:
    def test_has_host_id(self):
        schema, _ = _build_datasource_schema({}, {})
        names = {a.name for a in schema.attributes}
        assert "host_id" in names
        assert "result" not in names

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
# _build_ephemeral_schema
# ---------------------------------------------------------------------------


class TestBuildEphemeralSchema:
    def test_has_host_id(self):
        schema, _ = _build_ephemeral_schema({}, {})
        names = {a.name for a in schema.attributes}
        assert "host_id" in names

    def test_no_state_concepts(self):
        schema, _ = _build_ephemeral_schema({}, {})
        names = {a.name for a in schema.attributes}
        assert "id" not in names
        assert "changed" not in names
        assert "triggers" not in names
        assert "changed_when" not in names

    def test_no_async_attrs(self):
        schema, _ = _build_ephemeral_schema({}, {})
        names = {a.name for a in schema.attributes}
        assert "async_seconds" not in names
        assert "poll_interval" not in names

    def test_has_execution_context_attrs(self):
        schema, _ = _build_ephemeral_schema({}, {})
        names = {a.name for a in schema.attributes}
        expected = {"timeout", "ignore_errors", "failed_when", "environment", "delegate_to_id"}
        assert expected <= names
        assert "tags" not in names
        assert "skip_tags" not in names

    def test_ephemeral_framework_attr_types(self):
        schema, _ = _build_ephemeral_schema({}, {})
        attr_map = {a.name: a for a in schema.attributes}
        assert isinstance(attr_map["environment"].type, Map)
        assert isinstance(attr_map["environment"].type.value_type, String)

    def test_options_included(self):
        options = {"cmd": {"type": "str", "required": True}}
        schema, _ = _build_ephemeral_schema(options, {})
        names = {a.name for a in schema.attributes}
        assert "cmd" in names

    def test_return_only_computed(self):
        schema, return_names = _build_ephemeral_schema({}, {"stdout": {"type": "str"}})
        attr = next(a for a in schema.attributes if a.name == "stdout")
        assert attr.computed
        assert "stdout" in return_names

    def test_framework_name_in_options_not_duplicated(self):
        options = {"host_id": {"type": "str"}, "cmd": {"type": "str"}}
        schema, _ = _build_ephemeral_schema(options, {})
        names = [a.name for a in schema.attributes]
        assert names.count("host_id") == 1

    def test_return_skipped_when_name_is_framework_attr(self):
        """Returns whose names collide with framework attrs (e.g. 'timeout') are not added."""
        schema, return_names = _build_ephemeral_schema({}, {"timeout": {"type": "int"}})
        names = {a.name for a in schema.attributes}
        # timeout comes from framework attrs, but the return spec should not create a second entry
        assert names.count("timeout") if isinstance(names, list) else "timeout" in names
        assert "timeout" not in return_names

    def test_return_skipped_when_name_in_options(self):
        """Returns whose names shadow an option are not added as separate computed attrs."""
        options = {"path": {"type": "str", "required": True}}
        schema, return_names = _build_ephemeral_schema(options, {"path": {"type": "str"}})
        names = [a.name for a in schema.attributes]
        assert names.count("path") == 1
        assert "path" not in return_names


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

    def test_has_state_absent_true(self):
        options = {"state": {"type": "str", "choices": ["present", "absent"]}}
        klass = make_task_class("ansible.builtin.file", options, {})
        assert klass._has_state_absent is True

    def test_has_state_absent_false(self):
        klass = make_task_class("ansible.builtin.ping", {}, {})
        assert klass._has_state_absent is False

    def test_unique_classes_per_fqcn(self):
        a = make_task_class("ansible.builtin.ping", {}, {})
        b = make_task_class("ansible.builtin.copy", {}, {})
        assert a is not b
        assert a.get_name() != b.get_name()

    def test_get_name_closure_is_correct(self):
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


# ---------------------------------------------------------------------------
# make_ephemeral_class
# ---------------------------------------------------------------------------


class TestMakeEphemeralClass:
    def test_is_subclass_of_ephemeral_resource(self):
        klass = make_ephemeral_class("ansible.builtin.ping", {}, {})
        assert issubclass(klass, EphemeralResource)

    def test_name_is_ping(self):
        klass = make_ephemeral_class("ansible.builtin.ping", {}, {})
        assert klass.get_name() == "ping"

    def test_module_name_stored(self):
        klass = make_ephemeral_class("ansible.builtin.ping", {}, {})
        assert klass._module_name == "ansible.builtin.ping"

    def test_schema_has_no_state_concepts(self):
        klass = make_ephemeral_class("ansible.builtin.ping", {}, {})
        names = {a.name for a in klass.get_schema().attributes}
        assert "id" not in names
        assert "changed" not in names
        assert "triggers" not in names

    def test_schema_has_execution_attrs(self):
        klass = make_ephemeral_class("ansible.builtin.ping", {}, {})
        names = {a.name for a in klass.get_schema().attributes}
        assert "host_id" in names
        assert "timeout" in names

    def test_return_attr_names_set(self):
        klass = make_ephemeral_class("ansible.builtin.ping", {}, {"stdout": {"type": "str"}})
        assert "stdout" in klass._return_attr_names

    def test_distinct_from_resource_and_datasource(self):
        resource = make_task_class("ansible.builtin.ping", {}, {})
        datasource = make_datasource_class("ansible.builtin.ping", {}, {})
        ephemeral = make_ephemeral_class("ansible.builtin.ping", {}, {})
        assert not issubclass(ephemeral, TerribleTaskBase)
        assert not issubclass(ephemeral, TerribleTaskDataSource)
        assert ephemeral is not resource
        assert ephemeral is not datasource

    def test_get_name_closure_correct(self):
        klasses = [make_ephemeral_class(f"ansible.builtin.mod{i}", {}, {}) for i in range(3)]
        names = [k.get_name() for k in klasses]
        assert names == [f"mod{i}" for i in range(3)]


# ---------------------------------------------------------------------------
# _check_mode_support
# ---------------------------------------------------------------------------


class TestCheckModeSupport:
    def test_full_support(self):
        doc = {"attributes": {"check_mode": {"support": "full"}}}
        assert _check_mode_support(doc) == "full"

    def test_partial_support(self):
        doc = {"attributes": {"check_mode": {"support": "partial"}}}
        assert _check_mode_support(doc) == "partial"

    def test_missing_returns_none(self):
        assert _check_mode_support({}) == "none"

    def test_missing_check_mode_key_returns_none(self):
        assert _check_mode_support({"attributes": {}}) == "none"


# ---------------------------------------------------------------------------
# _fqcn_for_path
# ---------------------------------------------------------------------------


class TestFqcnForPath:
    def test_ansible_builtin(self):
        assert _fqcn_for_path("/path/to/ansible/modules/ping.py") == "ansible.builtin.ping"

    def test_collection_module(self):
        path = "/path/to/ansible_collections/community/general/plugins/modules/git_config.py"
        assert _fqcn_for_path(path) == "community.general.git_config"

    def test_unknown_path_returns_none(self):
        assert _fqcn_for_path("/some/random/path/mymod.py") is None


# ---------------------------------------------------------------------------
# _parse_yaml_block
# ---------------------------------------------------------------------------


class TestParseYamlBlock:
    def test_parses_doc_block(self):
        source = 'DOCUMENTATION = """\noptions:\n  path:\n    type: str\n"""'
        result = _parse_yaml_block(source, _DOC_RE)
        assert result is not None
        assert "options" in result

    def test_returns_none_when_no_match(self):
        assert _parse_yaml_block("no docs here", _DOC_RE) is None

    def test_returns_none_on_yaml_error(self):
        source = 'DOCUMENTATION = """\nkey: [unclosed\n"""'
        assert _parse_yaml_block(source, _DOC_RE) is None


# ---------------------------------------------------------------------------
# _coercers_for
# ---------------------------------------------------------------------------


class TestCoercersFor:
    def test_bool_return_attr_gets_coercer(self):
        klass = make_task_class("ansible.builtin.x", {}, {"flag": {"type": "bool"}})
        coercers = klass._return_attr_coercers
        assert "flag" in coercers
        assert coercers["flag"](1) is True
        assert coercers["flag"](None) is None

    def test_number_return_attr_gets_coercer(self):
        klass = make_task_class("ansible.builtin.x", {}, {"rc": {"type": "int"}})
        assert "rc" in klass._return_attr_coercers


# ---------------------------------------------------------------------------
# _build_datasource_schema — branch coverage
# ---------------------------------------------------------------------------


class TestBuildDatasourceSchemaExtraBranches:
    def test_framework_name_in_options_is_skipped(self):
        options = {"host_id": {"type": "str"}, "path": {"type": "str"}}
        schema, _ = _build_datasource_schema(options, {})
        names = [a.name for a in schema.attributes]
        assert names.count("host_id") == 1

    def test_framework_name_in_returns_is_skipped(self):
        schema, return_names = _build_datasource_schema({}, {"host_id": {"type": "str"}, "rc": {"type": "int"}})
        names = [a.name for a in schema.attributes]
        assert names.count("host_id") == 1
        assert "rc" in return_names


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

_CACHE_SCHEMA = """
    CREATE TABLE discovery_cache (
        ansible_version TEXT, fqcn TEXT, options_json TEXT,
        returns_json TEXT, check_mode TEXT, classification TEXT,
        PRIMARY KEY (ansible_version, fqcn)
    )
"""


class TestCacheHelpers:
    def test_cache_db_path_returns_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        p = _cache_db_path()
        assert p.name == "discovery.db"
        assert p.parent.exists()

    def test_open_cache_creates_table(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        db = _open_cache()
        try:
            rows = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            assert any("discovery_cache" in r[0] for r in rows)
        finally:
            db.close()

    def test_open_cache_has_classification_column(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        db = _open_cache()
        try:
            cols = [r[1] for r in db.execute("PRAGMA table_info(discovery_cache)").fetchall()]
            assert "classification" in cols
        finally:
            db.close()

    def test_open_cache_migrates_old_schema(self, tmp_path, monkeypatch):
        """Old 5-column cache gains classification column and is cleared."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        db_path = tmp_path / ".cache" / "tf-python-provider" / "discovery.db"
        db_path.parent.mkdir(parents=True)
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE discovery_cache (
                ansible_version TEXT, fqcn TEXT, options_json TEXT,
                returns_json TEXT, check_mode TEXT, PRIMARY KEY (ansible_version, fqcn)
            )
        """)
        conn.execute(
            "INSERT INTO discovery_cache VALUES (?,?,?,?,?)",
            ("1.0", "ansible.builtin.ping", "{}", "{}", "none"),
        )
        conn.commit()
        conn.close()

        db = _open_cache()
        try:
            cols = [r[1] for r in db.execute("PRAGMA table_info(discovery_cache)").fetchall()]
            assert "classification" in cols
            rows = db.execute("SELECT * FROM discovery_cache").fetchall()
            assert rows == []
        finally:
            db.close()

    def test_load_cached_empty_returns_none(self):
        db = sqlite3.connect(":memory:")
        db.execute(_CACHE_SCHEMA)
        assert _load_cached(db, "2.99") is None
        db.close()

    def test_load_cached_returns_resource(self):
        db = sqlite3.connect(":memory:")
        db.execute(_CACHE_SCHEMA)
        db.execute(
            "INSERT INTO discovery_cache VALUES (?,?,?,?,?,?)",
            ("2.99", "ansible.builtin.file", "{}", "{}", "none", "resource"),
        )
        db.commit()
        resources, datasources, ephemerals = _load_cached(db, "2.99")
        assert len(resources) == 1
        assert datasources == []
        assert ephemerals == []
        db.close()

    def test_load_cached_returns_datasource(self):
        db = sqlite3.connect(":memory:")
        db.execute(_CACHE_SCHEMA)
        db.execute(
            "INSERT INTO discovery_cache VALUES (?,?,?,?,?,?)",
            ("2.99", "ansible.builtin.stat", "{}", "{}", "full", "datasource"),
        )
        db.commit()
        resources, datasources, ephemerals = _load_cached(db, "2.99")
        assert resources == []
        assert len(datasources) == 1
        assert ephemerals == []
        db.close()

    def test_load_cached_returns_ephemeral(self):
        db = sqlite3.connect(":memory:")
        db.execute(_CACHE_SCHEMA)
        db.execute(
            "INSERT INTO discovery_cache VALUES (?,?,?,?,?,?)",
            ("2.99", "ansible.builtin.async_status", "{}", "{}", "none", "ephemeral"),
        )
        db.commit()
        resources, datasources, ephemerals = _load_cached(db, "2.99")
        assert resources == []
        assert datasources == []
        assert len(ephemerals) == 1
        db.close()

    def test_load_cached_bad_json_skipped(self):
        db = sqlite3.connect(":memory:")
        db.execute(_CACHE_SCHEMA)
        db.execute(
            "INSERT INTO discovery_cache VALUES (?,?,?,?,?,?)",
            ("2.99", "ansible.builtin.file", "not json", "{}", "none", "resource"),
        )
        db.commit()
        resources, datasources, ephemerals = _load_cached(db, "2.99")
        assert resources == []
        db.close()

    def test_load_cached_invalidated_on_classification_change(self):
        db = sqlite3.connect(":memory:")
        db.execute(_CACHE_SCHEMA)
        db.execute(
            "INSERT INTO discovery_cache VALUES (?,?,?,?,?,?)",
            ("2.99", "ansible.builtin.command", "{}", "{}", "partial", "ephemeral"),
        )
        db.commit()
        assert _load_cached(db, "2.99") is None
        db.close()

    def test_save_cache_inserts_rows(self):
        db = sqlite3.connect(":memory:")
        db.execute(_CACHE_SCHEMA)
        _save_cache(db, "2.99", [("2.99", "ansible.builtin.file", "{}", "{}", "none", "resource")])
        rows = db.execute("SELECT fqcn FROM discovery_cache").fetchall()
        assert rows == [("ansible.builtin.file",)]
        db.close()

    def test_save_cache_deletes_stale_versions(self):
        db = sqlite3.connect(":memory:")
        db.execute(_CACHE_SCHEMA)
        db.execute(
            "INSERT INTO discovery_cache VALUES (?,?,?,?,?,?)",
            ("1.0", "ansible.builtin.file", "{}", "{}", "none", "resource"),
        )
        db.commit()
        _save_cache(db, "2.99", [])
        rows = db.execute("SELECT * FROM discovery_cache").fetchall()
        assert rows == []
        db.close()


# ---------------------------------------------------------------------------
# discover_task_resources
# ---------------------------------------------------------------------------


class TestDiscoverTaskResources:
    def test_cache_hit_returns_cached(self):
        fake_class = MagicMock()
        db_mock = MagicMock()
        with (
            patch("terrible_provider.discovery._open_cache", return_value=db_mock),
            patch("terrible_provider.discovery._load_cached", return_value=([fake_class], [], [])),
        ):
            resources, datasources, ephemerals = discover_task_resources()
        assert resources == [fake_class]
        assert datasources == []
        assert ephemerals == []

    def test_cache_miss_empty_walk(self):
        db_mock = MagicMock()
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", return_value=db_mock),
            patch("terrible_provider.discovery._load_cached", return_value=None),
            patch("terrible_provider.discovery._save_cache") as mock_save,
            patch.object(loader.module_loader, "all", return_value=[]),
        ):
            resources, datasources, ephemerals = discover_task_resources()
        assert resources == []
        assert datasources == []
        assert ephemerals == []
        mock_save.assert_not_called()

    def test_cache_open_exception_still_walks(self):
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", side_effect=Exception("disk full")),
            patch.object(loader.module_loader, "all", return_value=[]),
        ):
            resources, datasources, ephemerals = discover_task_resources()
        assert resources == []

    def test_ansible_not_importable(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "ansible", None)
        resources, datasources, ephemerals = discover_task_resources()
        assert resources == []
        assert datasources == []
        assert ephemerals == []

    def test_cache_load_raises_closes_db(self):
        db_mock = MagicMock()
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", return_value=db_mock),
            patch("terrible_provider.discovery._load_cached", side_effect=RuntimeError("load failed")),
            patch.object(loader.module_loader, "all", return_value=[]),
        ):
            discover_task_resources()
        db_mock.close.assert_called()

    def test_cache_load_raises_close_also_raises(self):
        db_mock = MagicMock()
        db_mock.close.side_effect = OSError("cannot close")
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", return_value=db_mock),
            patch("terrible_provider.discovery._load_cached", side_effect=RuntimeError("load failed")),
            patch.object(loader.module_loader, "all", return_value=[]),
        ):
            discover_task_resources()  # Must not raise despite close failing

    def test_walk_resource_module(self, tmp_path):
        mod_dir = tmp_path / "ansible" / "modules"
        mod_dir.mkdir(parents=True)
        (mod_dir / "file.py").write_text(_FAKE_MODULE)
        db_mock = MagicMock()
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", return_value=db_mock),
            patch("terrible_provider.discovery._load_cached", return_value=None),
            patch("terrible_provider.discovery._save_cache") as mock_save,
            patch.object(loader.module_loader, "all", return_value=[str(mod_dir / "file.py")]),
        ):
            resources, datasources, ephemerals = discover_task_resources()
        assert len(resources) == 1
        assert resources[0].get_name() == "file"
        assert datasources == []
        assert ephemerals == []
        mock_save.assert_called_once()

    def test_walk_datasource_module(self, tmp_path):
        mod_dir = tmp_path / "ansible" / "modules"
        mod_dir.mkdir(parents=True)
        (mod_dir / "stat.py").write_text(_FAKE_MODULE)
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", side_effect=Exception("no cache")),
            patch.object(loader.module_loader, "all", return_value=[str(mod_dir / "stat.py")]),
        ):
            resources, datasources, ephemerals = discover_task_resources()
        assert resources == []
        assert len(datasources) == 1
        assert datasources[0].get_name() == "stat"
        assert ephemerals == []

    def test_walk_ephemeral_module(self, tmp_path):
        mod_dir = tmp_path / "ansible" / "modules"
        mod_dir.mkdir(parents=True)
        (mod_dir / "async_status.py").write_text(_FAKE_MODULE)
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", side_effect=Exception("no cache")),
            patch.object(loader.module_loader, "all", return_value=[str(mod_dir / "async_status.py")]),
        ):
            resources, datasources, ephemerals = discover_task_resources()
        assert resources == []
        assert datasources == []
        assert len(ephemerals) == 1
        assert ephemerals[0].get_name() == "async_status"

    def test_walk_skips_internal_module(self, tmp_path):
        mod_dir = tmp_path / "ansible" / "modules"
        mod_dir.mkdir(parents=True)
        (mod_dir / "debug.py").write_text(_FAKE_MODULE)
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", side_effect=Exception("no cache")),
            patch.object(loader.module_loader, "all", return_value=[str(mod_dir / "debug.py")]),
        ):
            resources, datasources, ephemerals = discover_task_resources()
        assert resources == [] and datasources == [] and ephemerals == []

    def test_walk_skips_unclassified_module(self, tmp_path):
        mod_dir = tmp_path / "ansible" / "modules"
        mod_dir.mkdir(parents=True)
        (mod_dir / "nomod.py").write_text(_FAKE_MODULE)
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", side_effect=Exception("no cache")),
            patch.object(loader.module_loader, "all", return_value=[str(mod_dir / "nomod.py")]),
        ):
            resources, datasources, ephemerals = discover_task_resources()
        assert resources == [] and datasources == [] and ephemerals == []

    def test_walk_skips_underscore_files(self, tmp_path):
        mod_dir = tmp_path / "ansible" / "modules"
        mod_dir.mkdir(parents=True)
        (mod_dir / "_private.py").write_text(_FAKE_MODULE)
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", side_effect=Exception("no cache")),
            patch.object(loader.module_loader, "all", return_value=[str(mod_dir / "_private.py")]),
        ):
            resources, _, _ = discover_task_resources()
        assert resources == []

    def test_walk_skips_non_py_and_empty_paths(self, tmp_path):
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", side_effect=Exception("no cache")),
            patch.object(loader.module_loader, "all", return_value=["", None, "/some/file.pyc"]),
        ):
            resources, _, _ = discover_task_resources()
        assert resources == []

    def test_walk_skips_unrecognized_paths(self, tmp_path):
        unknown = tmp_path / "random" / "place" / "file.py"
        unknown.parent.mkdir(parents=True)
        unknown.write_text(_FAKE_MODULE)
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", side_effect=Exception("no cache")),
            patch.object(loader.module_loader, "all", return_value=[str(unknown)]),
        ):
            resources, _, _ = discover_task_resources()
        assert resources == []

    def test_walk_skips_oserror_on_open(self, tmp_path):
        mod_dir = tmp_path / "ansible" / "modules"
        mod_dir.mkdir(parents=True)
        nonexistent = str(mod_dir / "file.py")
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", side_effect=Exception("no cache")),
            patch.object(loader.module_loader, "all", return_value=[nonexistent]),
        ):
            resources, _, _ = discover_task_resources()
        assert resources == []

    def test_walk_skips_modules_without_docs(self, tmp_path):
        mod_dir = tmp_path / "ansible" / "modules"
        mod_dir.mkdir(parents=True)
        (mod_dir / "file.py").write_text("# No documentation block here\n")
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", side_effect=Exception("no cache")),
            patch.object(loader.module_loader, "all", return_value=[str(mod_dir / "file.py")]),
        ):
            resources, _, _ = discover_task_resources()
        assert resources == []

    def test_walk_handles_make_task_class_exception(self, tmp_path):
        mod_dir = tmp_path / "ansible" / "modules"
        mod_dir.mkdir(parents=True)
        (mod_dir / "file.py").write_text(_FAKE_MODULE)
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", side_effect=Exception("no cache")),
            patch.object(loader.module_loader, "all", return_value=[str(mod_dir / "file.py")]),
            patch("terrible_provider.discovery.make_task_class", side_effect=ValueError("bad class")),
        ):
            resources, _, _ = discover_task_resources()
        assert resources == []

    def test_save_cache_exception_handled(self, tmp_path):
        mod_dir = tmp_path / "ansible" / "modules"
        mod_dir.mkdir(parents=True)
        (mod_dir / "file.py").write_text(_FAKE_MODULE)
        db_mock = MagicMock()
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", return_value=db_mock),
            patch("terrible_provider.discovery._load_cached", return_value=None),
            patch("terrible_provider.discovery._save_cache", side_effect=Exception("disk full")),
            patch.object(loader.module_loader, "all", return_value=[str(mod_dir / "file.py")]),
        ):
            discover_task_resources()  # Must not raise

    def test_finally_db_close_exception_handled(self):
        db_mock = MagicMock()
        db_mock.close.side_effect = OSError("final close failed")
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", return_value=db_mock),
            patch("terrible_provider.discovery._load_cached", return_value=None),
            patch("terrible_provider.discovery._save_cache"),
            patch.object(loader.module_loader, "all", return_value=[]),
        ):
            discover_task_resources()  # Must not raise


# ---------------------------------------------------------------------------
# _get_installed_collections
# ---------------------------------------------------------------------------


class TestGetInstalledCollections:
    def test_empty_paths_returns_empty(self):
        assert _get_installed_collections([]) == set()

    def test_nonexistent_dir_skipped(self, tmp_path):
        result = _get_installed_collections([str(tmp_path / "nonexistent")])
        assert result == set()

    def test_single_collection_found(self, tmp_path):
        coll_dir = tmp_path / "ansible_collections" / "community" / "general"
        coll_dir.mkdir(parents=True)
        result = _get_installed_collections([str(tmp_path)])
        assert result == {"community.general"}

    def test_multiple_collections_found(self, tmp_path):
        for ns, coll in [("community", "general"), ("community", "crypto"), ("ansible", "netcommon")]:
            (tmp_path / "ansible_collections" / ns / coll).mkdir(parents=True)
        result = _get_installed_collections([str(tmp_path)])
        assert result == {"community.general", "community.crypto", "ansible.netcommon"}

    def test_hidden_namespace_dirs_skipped(self, tmp_path):
        (tmp_path / "ansible_collections" / ".hidden" / "general").mkdir(parents=True)
        (tmp_path / "ansible_collections" / "community" / ".hidden_coll").mkdir(parents=True)
        (tmp_path / "ansible_collections" / "community" / "real").mkdir(parents=True)
        result = _get_installed_collections([str(tmp_path)])
        assert result == {"community.real"}

    def test_files_not_dirs_skipped(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        ac.mkdir()
        (ac / "notadir.txt").write_text("x")
        ns = ac / "community"
        ns.mkdir()
        (ns / "notacoll.txt").write_text("x")
        (ns / "real").mkdir()
        result = _get_installed_collections([str(tmp_path)])
        assert result == {"community.real"}

    def test_multiple_collection_paths(self, tmp_path):
        p1 = tmp_path / "path1"
        p2 = tmp_path / "path2"
        (p1 / "ansible_collections" / "community" / "general").mkdir(parents=True)
        (p2 / "ansible_collections" / "ansible" / "netcommon").mkdir(parents=True)
        result = _get_installed_collections([str(p1), str(p2)])
        assert result == {"community.general", "ansible.netcommon"}

    def test_oserror_on_iterdir_handled(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        ac.mkdir()
        with patch("pathlib.Path.iterdir", side_effect=OSError("perm denied")):
            result = _get_installed_collections([str(tmp_path)])
        assert result == set()

    def test_uses_ansible_constants_when_no_paths_given(self, tmp_path):
        coll_dir = tmp_path / "ansible_collections" / "community" / "general"
        coll_dir.mkdir(parents=True)
        with patch("ansible.constants.COLLECTIONS_PATHS", [str(tmp_path)]):
            result = _get_installed_collections()
        assert "community.general" in result

    def test_ansible_import_error_returns_empty(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "ansible.constants", None)
        result = _get_installed_collections()
        assert result == set()


# ---------------------------------------------------------------------------
# _iter_collection_module_paths
# ---------------------------------------------------------------------------


class TestIterCollectionModulePaths:
    def test_yields_py_files_in_modules_dir(self, tmp_path):
        mod = tmp_path / "ansible_collections" / "community" / "general" / "plugins" / "modules"
        mod.mkdir(parents=True)
        (mod / "mymod.py").write_text("")
        (mod / "_private.py").write_text("")
        (mod / "README.md").write_text("")
        result = list(_iter_collection_module_paths([str(tmp_path)]))
        assert len(result) == 1
        assert result[0].endswith("mymod.py")

    def test_deduplicates_across_paths(self, tmp_path):
        mod = tmp_path / "ansible_collections" / "community" / "general" / "plugins" / "modules"
        mod.mkdir(parents=True)
        (mod / "mymod.py").write_text("")
        result = list(_iter_collection_module_paths([str(tmp_path), str(tmp_path)]))
        assert len(result) == 1

    def test_nonexistent_path_skipped(self, tmp_path):
        result = list(_iter_collection_module_paths([str(tmp_path / "nonexistent")]))
        assert result == []

    def test_oserror_handled(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        ac.mkdir()
        with patch("pathlib.Path.glob", side_effect=OSError("perm denied")):
            result = list(_iter_collection_module_paths([str(tmp_path)]))
        assert result == []

    def test_uses_ansible_constants_and_site_packages_when_no_paths_given(self, tmp_path, monkeypatch):
        mod = tmp_path / "ansible_collections" / "community" / "general" / "plugins" / "modules"
        mod.mkdir(parents=True)
        (mod / "mymod.py").write_text("")
        monkeypatch.setattr("site.getsitepackages", lambda: [])
        with patch("ansible.constants.COLLECTIONS_PATHS", [str(tmp_path)]):
            result = list(_iter_collection_module_paths())
        assert any("mymod.py" in p for p in result)

    def test_ansible_import_error_falls_back_to_site_packages(self, tmp_path, monkeypatch):
        mod = tmp_path / "ansible_collections" / "community" / "general" / "plugins" / "modules"
        mod.mkdir(parents=True)
        (mod / "mymod.py").write_text("")
        monkeypatch.setitem(sys.modules, "ansible.constants", None)
        monkeypatch.setattr("site.getsitepackages", lambda: [str(tmp_path)])
        result = list(_iter_collection_module_paths())
        assert any("mymod.py" in p for p in result)


# ---------------------------------------------------------------------------
# _render_rst
# ---------------------------------------------------------------------------


class TestRenderRst:
    def test_option(self):
        assert _render_rst("See O(name)") == "See `name`"

    def test_value(self):
        assert _render_rst("Use V(present)") == "Use `present`"

    def test_code(self):
        assert _render_rst("Run C(apt-get update)") == "Run `apt-get update`"

    def test_env_var(self):
        assert _render_rst("Set E(PATH)") == "Set `PATH`"

    def test_module(self):
        assert _render_rst("See M(ansible.builtin.copy)") == "See `ansible.builtin.copy`"

    def test_plugin(self):
        assert _render_rst("Use P(amazon.aws.aws_ec2#inventory)") == "Use `amazon.aws.aws_ec2#inventory`"

    def test_bold(self):
        assert _render_rst("B(important) note") == "**important** note"

    def test_italic(self):
        assert _render_rst("I(emphasis) here") == "*emphasis* here"

    def test_url(self):
        assert _render_rst("See U(https://example.com)") == "See https://example.com"

    def test_link(self):
        assert _render_rst("See L(the docs,https://example.com)") == "See [the docs](https://example.com)"

    def test_ref(self):
        assert _render_rst("See R(specification format,role_argument_spec)") == "See specification format"

    def test_no_markup(self):
        assert _render_rst("plain text") == "plain text"

    def test_multiple(self):
        assert _render_rst("O(src) and V(true)") == "`src` and `true`"
