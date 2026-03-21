"""Unit tests for discovery schema-building and class-factory functions."""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from tf.types import Bool, NormalizedJson, Number

from terrible_provider.discovery import (
    _DOC_RE,
    _build_datasource_schema,
    _build_schema,
    _cache_db_path,
    _check_mode_support,
    _fqcn_for_path,
    _get_installed_collections,
    _load_cached,
    _open_cache,
    _parse_yaml_block,
    _resource_name_for,
    _save_cache,
    discover_task_resources,
    make_datasource_class,
    make_task_class,
)
from terrible_provider.task_base import TerribleTaskBase
from terrible_provider.task_datasource import TerribleTaskDataSource

# ---------------------------------------------------------------------------
# Fake module file content for filesystem-walk tests
# ---------------------------------------------------------------------------

_FAKE_MODULE_FULL = '''\
DOCUMENTATION = """
short_description: Fake module with full check mode
options:
  path:
    description: A path.
    type: str
    required: true
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

_FAKE_MODULE_NONE = '''\
DOCUMENTATION = """
short_description: Fake module no check mode
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
# _coercers_for — Bool branch
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
        # "host_id" is a framework name; it must not be duplicated
        options = {"host_id": {"type": "str"}, "path": {"type": "str"}}
        schema, _ = _build_datasource_schema(options, {})
        names = [a.name for a in schema.attributes]
        assert names.count("host_id") == 1

    def test_framework_name_in_returns_is_skipped(self):
        # "result" is a framework name; it must not be added again
        schema, return_names = _build_datasource_schema({}, {"result": {"type": "dict"}, "rc": {"type": "int"}})
        names = [a.name for a in schema.attributes]
        assert names.count("result") == 1
        assert "rc" in return_names


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


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

    def test_load_cached_empty_returns_none(self):
        db = sqlite3.connect(":memory:")
        db.execute("""
            CREATE TABLE discovery_cache (
                ansible_version TEXT, fqcn TEXT, options_json TEXT,
                returns_json TEXT, check_mode TEXT, PRIMARY KEY (ansible_version, fqcn)
            )
        """)
        assert _load_cached(db, "2.99") is None
        db.close()

    def test_load_cached_returns_classes(self):
        db = sqlite3.connect(":memory:")
        db.execute("""
            CREATE TABLE discovery_cache (
                ansible_version TEXT, fqcn TEXT, options_json TEXT,
                returns_json TEXT, check_mode TEXT, PRIMARY KEY (ansible_version, fqcn)
            )
        """)
        db.execute(
            "INSERT INTO discovery_cache VALUES (?,?,?,?,?)", ("2.99", "ansible.builtin.ping", "{}", "{}", "full")
        )
        db.commit()
        resources, datasources = _load_cached(db, "2.99")
        assert len(resources) == 1
        assert len(datasources) == 1  # full check mode → also a datasource
        db.close()

    def test_load_cached_bad_json_skipped(self):
        db = sqlite3.connect(":memory:")
        db.execute("""
            CREATE TABLE discovery_cache (
                ansible_version TEXT, fqcn TEXT, options_json TEXT,
                returns_json TEXT, check_mode TEXT, PRIMARY KEY (ansible_version, fqcn)
            )
        """)
        db.execute(
            "INSERT INTO discovery_cache VALUES (?,?,?,?,?)", ("2.99", "ansible.builtin.bad", "not json", "{}", "none")
        )
        db.commit()
        resources, datasources = _load_cached(db, "2.99")
        assert resources == []
        db.close()

    def test_save_cache_inserts_rows(self):
        db = sqlite3.connect(":memory:")
        db.execute("""
            CREATE TABLE discovery_cache (
                ansible_version TEXT, fqcn TEXT, options_json TEXT,
                returns_json TEXT, check_mode TEXT, PRIMARY KEY (ansible_version, fqcn)
            )
        """)
        _save_cache(db, "2.99", [("2.99", "ansible.builtin.ping", "{}", "{}", "none")])
        rows = db.execute("SELECT fqcn FROM discovery_cache").fetchall()
        assert rows == [("ansible.builtin.ping",)]
        db.close()

    def test_save_cache_deletes_stale_versions(self):
        db = sqlite3.connect(":memory:")
        db.execute("""
            CREATE TABLE discovery_cache (
                ansible_version TEXT, fqcn TEXT, options_json TEXT,
                returns_json TEXT, check_mode TEXT, PRIMARY KEY (ansible_version, fqcn)
            )
        """)
        db.execute(
            "INSERT INTO discovery_cache VALUES (?,?,?,?,?)", ("1.0", "ansible.builtin.ping", "{}", "{}", "none")
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
            patch("terrible_provider.discovery._load_cached", return_value=([fake_class], [])),
        ):
            resources, datasources = discover_task_resources()
        assert resources == [fake_class]
        assert datasources == []

    def test_cache_miss_empty_walk(self):
        db_mock = MagicMock()
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", return_value=db_mock),
            patch("terrible_provider.discovery._load_cached", return_value=None),
            patch("terrible_provider.discovery._save_cache") as mock_save,
            patch.object(loader.module_loader, "all", return_value=[]),
        ):
            resources, datasources = discover_task_resources()
        assert resources == []
        assert datasources == []
        mock_save.assert_not_called()

    def test_cache_open_exception_still_walks(self):
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", side_effect=Exception("disk full")),
            patch.object(loader.module_loader, "all", return_value=[]),
        ):
            resources, datasources = discover_task_resources()
        assert resources == []

    def test_ansible_not_importable(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "ansible", None)
        resources, datasources = discover_task_resources()
        assert resources == []
        assert datasources == []

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

    def test_walk_valid_module_full_check_mode(self, tmp_path):
        mod_dir = tmp_path / "ansible" / "modules"
        mod_dir.mkdir(parents=True)
        (mod_dir / "fakemod.py").write_text(_FAKE_MODULE_FULL)
        db_mock = MagicMock()
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", return_value=db_mock),
            patch("terrible_provider.discovery._load_cached", return_value=None),
            patch("terrible_provider.discovery._save_cache") as mock_save,
            patch.object(loader.module_loader, "all", return_value=[str(mod_dir / "fakemod.py")]),
        ):
            resources, datasources = discover_task_resources()
        assert len(resources) == 1
        assert resources[0].get_name() == "fakemod"
        assert len(datasources) == 1
        mock_save.assert_called_once()

    def test_walk_module_no_check_mode(self, tmp_path):
        mod_dir = tmp_path / "ansible" / "modules"
        mod_dir.mkdir(parents=True)
        (mod_dir / "nocheck.py").write_text(_FAKE_MODULE_NONE)
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", side_effect=Exception("no cache")),
            patch.object(loader.module_loader, "all", return_value=[str(mod_dir / "nocheck.py")]),
        ):
            resources, datasources = discover_task_resources()
        assert len(resources) == 1
        assert datasources == []

    def test_walk_skips_underscore_files(self, tmp_path):
        mod_dir = tmp_path / "ansible" / "modules"
        mod_dir.mkdir(parents=True)
        (mod_dir / "_private.py").write_text(_FAKE_MODULE_FULL)
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", side_effect=Exception("no cache")),
            patch.object(loader.module_loader, "all", return_value=[str(mod_dir / "_private.py")]),
        ):
            resources, _ = discover_task_resources()
        assert resources == []

    def test_walk_skips_non_py_and_empty_paths(self, tmp_path):
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", side_effect=Exception("no cache")),
            patch.object(loader.module_loader, "all", return_value=["", None, "/some/file.pyc"]),
        ):
            resources, _ = discover_task_resources()
        assert resources == []

    def test_walk_skips_unrecognized_paths(self, tmp_path):
        unknown = tmp_path / "random" / "place" / "mymod.py"
        unknown.parent.mkdir(parents=True)
        unknown.write_text(_FAKE_MODULE_FULL)
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", side_effect=Exception("no cache")),
            patch.object(loader.module_loader, "all", return_value=[str(unknown)]),
        ):
            resources, _ = discover_task_resources()
        assert resources == []

    def test_walk_skips_oserror_on_open(self, tmp_path):
        # File path matches fqcn regex but doesn't exist → OSError on open → skip
        mod_dir = tmp_path / "ansible" / "modules"
        mod_dir.mkdir(parents=True)
        nonexistent = str(mod_dir / "ghost.py")
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", side_effect=Exception("no cache")),
            patch.object(loader.module_loader, "all", return_value=[nonexistent]),
        ):
            resources, _ = discover_task_resources()
        assert resources == []

    def test_walk_skips_modules_without_docs(self, tmp_path):
        mod_dir = tmp_path / "ansible" / "modules"
        mod_dir.mkdir(parents=True)
        (mod_dir / "nodoc.py").write_text("# No documentation block here\n")
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", side_effect=Exception("no cache")),
            patch.object(loader.module_loader, "all", return_value=[str(mod_dir / "nodoc.py")]),
        ):
            resources, _ = discover_task_resources()
        assert resources == []

    def test_walk_handles_make_task_class_exception(self, tmp_path):
        mod_dir = tmp_path / "ansible" / "modules"
        mod_dir.mkdir(parents=True)
        (mod_dir / "badmod.py").write_text(_FAKE_MODULE_FULL)
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", side_effect=Exception("no cache")),
            patch.object(loader.module_loader, "all", return_value=[str(mod_dir / "badmod.py")]),
            patch("terrible_provider.discovery.make_task_class", side_effect=ValueError("bad class")),
        ):
            resources, _ = discover_task_resources()
        assert resources == []

    def test_save_cache_exception_handled(self, tmp_path):
        mod_dir = tmp_path / "ansible" / "modules"
        mod_dir.mkdir(parents=True)
        (mod_dir / "goodmod.py").write_text(_FAKE_MODULE_FULL)
        db_mock = MagicMock()
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", return_value=db_mock),
            patch("terrible_provider.discovery._load_cached", return_value=None),
            patch("terrible_provider.discovery._save_cache", side_effect=Exception("disk full")),
            patch.object(loader.module_loader, "all", return_value=[str(mod_dir / "goodmod.py")]),
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

    def test_installed_collection_with_no_modules_warns(self, tmp_path, caplog):
        import logging

        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", side_effect=Exception("no cache")),
            patch.object(loader.module_loader, "all", return_value=[]),
            patch(
                "terrible_provider.discovery._get_installed_collections",
                return_value={"community.general", "community.crypto"},
            ),caplog.at_level(logging.WARNING, logger="terrible_provider.discovery")
        ):
            discover_task_resources()
        warned = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("community.general" in m for m in warned)
        assert any("community.crypto" in m for m in warned)

    def test_installed_collection_with_modules_no_warn(self, tmp_path, caplog):
        mod_dir = tmp_path / "ansible_collections" / "community" / "general" / "plugins" / "modules"
        mod_dir.mkdir(parents=True)
        (mod_dir / "ping.py").write_text(_FAKE_MODULE_NONE)
        import logging

        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", side_effect=Exception("no cache")),
            patch.object(loader.module_loader, "all", return_value=[str(mod_dir / "ping.py")]),
            patch("terrible_provider.discovery._get_installed_collections", return_value={"community.general"}),
            caplog.at_level(logging.WARNING, logger="terrible_provider.discovery"),
        ):
            discover_task_resources()
        warned = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert not any("community.general" in m for m in warned)

    def test_collection_presence_check_exception_handled(self, caplog):
        import ansible.plugins.loader as loader

        with (
            patch("terrible_provider.discovery._open_cache", side_effect=Exception("no cache")),
            patch.object(loader.module_loader, "all", return_value=[]),
            patch("terrible_provider.discovery._get_installed_collections", side_effect=RuntimeError("boom")),
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
