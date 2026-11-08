"""A08.1 contracts fail closed without touching actual installations/profiles."""

import copy
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from core import hermes_runtime
from core.runtime_descriptor import (
    DESCRIPTOR_ENV, MAX_DESCRIPTOR_BYTES, RuntimeDescriptorError, RuntimePolicyError,
    get_runtime, load_runtime_descriptor, require_runtime_capability,
    runtime_capability_allowed, runtime_public_info, runtime_pycache_dir, runtime_service_identity,
    runtime_subprocess_env,
)
from tests.runtime_descriptor_fixtures import write_runtime_fixture


@pytest.fixture
def installation(tmp_path, monkeypatch):
    descriptor, spec = write_runtime_fixture(tmp_path / "Instalación Iris con espacios")
    monkeypatch.setenv(DESCRIPTOR_ENV, str(descriptor))
    monkeypatch.setenv("IRIS_HOME", str(tmp_path / "profile"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile/hermes"))
    return descriptor, spec


def save(descriptor, spec):
    descriptor.write_text(json.dumps(spec, ensure_ascii=False), "utf-8")


def test_packaged_paths_do_not_depend_on_cwd(installation, tmp_path, monkeypatch):
    descriptor, _ = installation
    monkeypatch.chdir(tmp_path)
    runtime = get_runtime()
    assert runtime.packaged
    assert runtime.descriptor_path == descriptor.resolve()
    assert runtime.iris_root == (descriptor.parent / "iris").resolve()
    assert runtime.assets_root == (descriptor.parent / "web/Face á").resolve()
    assert runtime.hermes.python != runtime.hermes.cli.parent / "python.exe"


@pytest.mark.parametrize("role", ["iris.root", "iris.python", "iris.assets", "hermes.root", "hermes.python", "hermes.cli", "hermes.metadata"])
@pytest.mark.parametrize("bad", [
    "/absolute", "../outside", "iris/../outside", "iris/./core", "C:/outside", "C:outside", "//host/share",
    "\\\\host\\share", "iris\\core", "iris/file:stream", "iris//core", "iris/", "iris/trailing.", "iris/trailing ",
    "iris/NUL", "iris/CON.txt", "iris/CON .txt", "iris/COM1", "iris/LPT².txt", "iris/CLOCK$.txt", "iris/%USERPROFILE%", "iris/\x00file", "iris/\x85file", "",
])
def test_packaged_rejects_every_path_role(installation, role, bad):
    descriptor, spec = installation
    group, field = role.split(".")
    spec[group][field] = bad
    save(descriptor, spec)
    with pytest.raises(RuntimeDescriptorError):
        get_runtime()


@pytest.mark.parametrize("schema,mode", [(True, "packaged"), (2.0, "packaged"), (0, "development"), (3, "packaged"), (2, "unknown"), (2, []), (2, None)])
def test_unknown_schema_modes_and_wrong_types(installation, schema, mode):
    descriptor, spec = installation
    spec.update(schema_version=schema, mode=mode)
    save(descriptor, spec)
    with pytest.raises(RuntimeDescriptorError):
        get_runtime()


@pytest.mark.parametrize("target,field", [("top", "capabilities"), ("iris", "extra"), ("hermes", "command")])
def test_unknown_fields_cannot_enable_operations(installation, target, field):
    descriptor, spec = installation
    (spec if target == "top" else spec[target])[field] = True
    save(descriptor, spec)
    with pytest.raises(RuntimeDescriptorError):
        get_runtime()


@pytest.mark.parametrize("document", ["[]", "null", '{"schema_version":2,"schema_version":2}', '\ufeff{}', '{"mode":NaN}'])
def test_invalid_or_ambiguous_json(installation, document):
    descriptor, _ = installation
    descriptor.write_text(document, "utf-8")
    with pytest.raises(RuntimeDescriptorError):
        get_runtime()


def test_duplicate_nested_field_is_rejected(installation):
    descriptor, _ = installation
    descriptor.write_text(descriptor.read_text("utf-8").replace('"root": "iris"', '"root": "iris", "root": "iris"'), "utf-8")
    with pytest.raises(RuntimeDescriptorError):
        get_runtime()


def test_size_limit_applies_to_descriptor_and_pin(installation):
    descriptor, _ = installation
    original = descriptor.read_bytes()
    descriptor.write_bytes(b" " * (MAX_DESCRIPTOR_BYTES + 1))
    with pytest.raises(RuntimeDescriptorError):
        get_runtime()
    descriptor.write_bytes(original)
    pin = descriptor.parent / "private/hermes/app/runtime-pin.json"
    pin.write_bytes(b" " * (MAX_DESCRIPTOR_BYTES + 1))
    with pytest.raises(RuntimeDescriptorError):
        get_runtime()


@pytest.mark.parametrize("role", ["iris.root", "iris.python", "iris.assets", "hermes.root", "hermes.python", "hermes.cli", "hermes.metadata"])
def test_missing_resources_never_fall_back(installation, role, monkeypatch):
    descriptor, spec = installation
    group, field = role.split(".")
    spec[group][field] = "missing"
    save(descriptor, spec)
    fallback = Mock(side_effect=AssertionError("PATH must not be used"))
    monkeypatch.setattr(hermes_runtime.shutil, "which", fallback)
    with pytest.raises(RuntimeDescriptorError):
        hermes_runtime.resolve_hermes_python()
    fallback.assert_not_called()


@pytest.mark.parametrize("field,value", [("version", "different"), ("revision", "c" * 40), ("schema_version", True)])
def test_pin_mismatch_is_fatal(installation, field, value):
    descriptor, _ = installation
    pin = descriptor.parent / "private/hermes/app/runtime-pin.json"
    doc = json.loads(pin.read_text("utf-8"))
    doc[field] = value
    pin.write_text(json.dumps(doc), "utf-8")
    with pytest.raises(RuntimeDescriptorError):
        get_runtime()


def test_packaged_requires_hermes_and_valid_revisions(installation):
    descriptor, spec = installation
    original = copy.deepcopy(spec)
    del spec["hermes"]
    save(descriptor, spec)
    with pytest.raises(RuntimeDescriptorError):
        get_runtime()
    original["iris"]["revision"] = "not a revision"
    save(descriptor, original)
    with pytest.raises(RuntimeDescriptorError):
        get_runtime()


@pytest.mark.parametrize("value", ["", "  ", "0.21.4\n", "v\x85", "v" * 129, "v\ud800"])
def test_hermes_version_contract_matches_native_resolver(installation, value):
    descriptor, spec = installation
    spec["hermes"]["version"] = value
    descriptor.write_text(json.dumps(spec), "utf-8")
    pin = descriptor.parent / "private/hermes/app/runtime-pin.json"
    doc = json.loads(pin.read_text("utf-8"))
    doc["version"] = value
    pin.write_text(json.dumps(doc), "utf-8")
    with pytest.raises(RuntimeDescriptorError):
        get_runtime()


def test_legacy_schema_one_absolute_and_parent_paths(installation):
    descriptor, spec = installation
    for root in [str(descriptor.parent / "iris"), "../" + descriptor.parent.name + "/iris"]:
        save(descriptor, {"schema_version": 1, "mode": "development", "project_root": root, "python": str(descriptor.parent / spec["iris"]["python"])})
        assert load_runtime_descriptor(descriptor).mode == "development"


def test_schema_two_development_can_use_external_roots(installation, tmp_path):
    descriptor, spec = installation
    spec["mode"] = "development"
    del spec["hermes"]
    spec["iris"]["root"] = str(descriptor.parent / "iris")
    save(descriptor, spec)
    assert get_runtime().hermes is None
    assert runtime_capability_allowed("external_hermes_install")


@pytest.mark.parametrize("value", ["", "relative/runtime.json", "missing/runtime.json"])
def test_supplied_bad_descriptor_never_becomes_development(monkeypatch, value):
    monkeypatch.setenv(DESCRIPTOR_ENV, value)
    with pytest.raises(RuntimeDescriptorError):
        get_runtime()


def test_source_without_descriptor_preserves_development(monkeypatch):
    monkeypatch.delenv(DESCRIPTOR_ENV, raising=False)
    monkeypatch.setenv("IRIS_RUNTIME_MODE", "packaged")
    assert not get_runtime().packaged  # An unvalidated mode flag grants nothing.
    assert runtime_capability_allowed("external_hermes_update")


@pytest.mark.parametrize("name", ["external_hermes_install", "external_hermes_update", "source_update", "external_autostart"])
def test_packaged_operations_are_disabled(installation, name):
    assert not runtime_capability_allowed(name)
    with pytest.raises(RuntimePolicyError):
        require_runtime_capability(name)


def test_packaged_updates_only_through_the_bundled_channel(installation, monkeypatch):
    assert runtime_capability_allowed("bundled_update")
    monkeypatch.delenv(DESCRIPTOR_ENV)
    assert not runtime_capability_allowed("bundled_update")  # development keeps Git/hermes update


@pytest.mark.parametrize("name", ["install", "unknown", "", None, []])
def test_unknown_operations_are_rejected(installation, name):
    with pytest.raises(RuntimeDescriptorError):
        runtime_capability_allowed(name)


def test_runtime_info_and_identity_expose_no_paths_or_secrets(installation, tmp_path):
    descriptor, _ = installation
    public = runtime_public_info()
    identity = runtime_service_identity(tmp_path / "profile")
    assert public["capabilities"]["profile_configuration"]
    assert public["capabilities"]["profile_extras"]
    assert "base_package_install" not in public["capabilities"]
    assert identity !=runtime_service_identity(tmp_path / "another-profile")
    value = json.dumps([public, identity])
    assert str(descriptor.parent) not in value
    assert "HERMES_HOME" not in value
    assert len(identity["runtime_id"]) == 64


def test_private_hermes_selection_ignores_environment_and_path(installation, monkeypatch):
    runtime = get_runtime()
    monkeypatch.setenv("HERMES_REPO", "external/repo")
    monkeypatch.setenv("HERMES_AGENT_ROOT", "external/other")
    path = Mock(side_effect=AssertionError("external launcher used"))
    monkeypatch.setattr(hermes_runtime.shutil, "which", path)
    assert hermes_runtime.get_hermes_repo() == runtime.hermes.root
    assert hermes_runtime.resolve_hermes_cli() == str(runtime.hermes.cli)
    assert hermes_runtime.resolve_hermes_python() == str(runtime.hermes.python)
    assert hermes_runtime.hermes_command_prefix() == [
        str(runtime.hermes.python), "-E", "-s", "-X", f"pycache_prefix={runtime_pycache_dir()}", "-X", "utf8",
        str(runtime.hermes.cli),
    ]
    path.assert_not_called()


def test_child_environment_is_sanitized_and_propagates_descriptor(installation, monkeypatch):
    for key in ["PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__", "UV_PROJECT_ENVIRONMENT", "HERMES_REPO", "HERMES_AGENT_ROOT"]:
        monkeypatch.setenv(key, "external")
    env = hermes_runtime.hermes_subprocess_env()
    assert env[DESCRIPTOR_ENV] == str(installation[0].resolve())
    assert env["PYTHONNOUSERSITE"] == "1"
    assert env["PYTHONPYCACHEPREFIX"] == str(runtime_pycache_dir())
    assert "PYTHONDONTWRITEBYTECODE" not in env
    assert all(key not in env for key in ["PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "VIRTUAL_ENV", "HERMES_REPO", "HERMES_AGENT_ROOT"])
    assert "pythonpath" not in runtime_subprocess_env({"pythonpath": "external"})


def test_helpers_started_with_a_bare_interpreter_write_no_bytecode(installation, tmp_path):
    # A08.7: Hermes starts helpers as [sys.executable, ...] without flags. They
    # inherit the environment only, so it must keep bytecode out of the install:
    # since qa9 it goes to the profile's cache (PYTHONPYCACHEPREFIX).
    import sys

    cache = runtime_pycache_dir()
    assert cache is not None and not cache.is_relative_to(installation[0].parent)
    grandchild = "import sys; print(sys.pycache_prefix)"
    script = f"import subprocess, sys; print(subprocess.run([sys.executable, '-c', {grandchild!r}], capture_output=True, text=True).stdout.strip())"
    env = runtime_subprocess_env({"SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "PATH": ""})
    result = subprocess.run([sys.executable, "-B", "-c", script], env=env, capture_output=True, text=True, timeout=60)
    assert Path(result.stdout.strip()) == cache, result.stderr


def test_development_children_keep_their_bytecode_behaviour(monkeypatch):
    monkeypatch.delenv(DESCRIPTOR_ENV, raising=False)
    assert "PYTHONDONTWRITEBYTECODE" not in runtime_subprocess_env({})


def test_hermes_cli_uses_private_python_and_cwd(installation, monkeypatch, tmp_path):
    runtime = get_runtime()
    run = Mock(return_value=subprocess.CompletedProcess([], 0, "Árbol listo", ""))
    monkeypatch.setattr(hermes_runtime.subprocess, "run", run)
    assert hermes_runtime.run_hermes_cli(["status"])["stdout"] == "Árbol listo"
    assert run.call_args.kwargs["cwd"] == str(runtime.hermes.root)
    assert run.call_args.args[0][0] == str(runtime.hermes.python)
    with pytest.raises(RuntimeDescriptorError):
        hermes_runtime.run_hermes_cli(["status"], cwd=tmp_path)


def make_directory_alias(link: Path, target: Path):
    if os.name == "nt":
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, creationflags=0x08000000,
        )
        assert result.returncode == 0, (result.stdout + result.stderr).decode(errors="replace")
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("role", ["iris.root", "iris.python", "iris.assets", "hermes.root", "hermes.python", "hermes.cli", "hermes.metadata"])
def test_actual_directory_alias_cannot_escape_for_any_role(installation, tmp_path, role):
    descriptor, spec = installation
    outside, _ = write_runtime_fixture(tmp_path / "Instalación Iris con espacios-sibling")
    link = descriptor.parent / "alias"
    make_directory_alias(link, outside.parent)
    try:
        group, field = role.split(".")
        spec[group][field] = "alias/" + spec[group][field]
        save(descriptor, spec)
        with pytest.raises(RuntimeDescriptorError):
            get_runtime()
    finally:
        if os.name == "nt":
            link.rmdir()  # Remove the junction itself, never its target.
        else:
            link.unlink()


def test_core_anchor_alias_inside_installation_must_belong_to_iris_root(installation):
    descriptor, _ = installation
    source = descriptor.parent / "iris/core"
    other = descriptor.parent / "different-core"
    source.rename(other)
    make_directory_alias(source, other)
    try:
        with pytest.raises(RuntimeDescriptorError):
            get_runtime()
    finally:
        source.rmdir() if os.name == "nt" else source.unlink()


def test_actual_file_symlink_cannot_escape(installation, tmp_path):
    descriptor, _ = installation
    outside = tmp_path / "external-python.exe"
    outside.write_bytes(b"external fixture")
    link = descriptor.parent / "private/iris/python.exe"
    link.unlink()
    try:
        link.symlink_to(outside)
    except OSError as exc:
        if os.name == "nt" and exc.winerror == 1314:
            pytest.skip("Windows does not grant file symlink privilege; real junction tests cover each path role.")
        raise
    with pytest.raises(RuntimeDescriptorError):
        get_runtime()


def test_descriptor_alias_must_preserve_native_installation_base(installation, monkeypatch):
    descriptor, _ = installation
    nested = descriptor.parent / "internal-alias/runtime.json"
    nested.parent.mkdir()
    nested.write_bytes(descriptor.read_bytes())
    original_resolve = Path.resolve

    def simulated_file_alias(self, *args, **kwargs):
        if self == descriptor:
            return nested
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", simulated_file_alias)
    with pytest.raises(RuntimeDescriptorError, match="carpeta de instalación"):
        get_runtime()


# ── A08.7: installation folder budget for Windows MAX_PATH ─────────────────

def test_install_limit_is_optional_and_reported(installation):
    descriptor, spec = installation
    assert get_runtime().max_relative_path is None
    spec = copy.deepcopy(spec)
    spec["install"] = {"max_relative_path": 40}
    save(descriptor, spec)
    assert get_runtime().max_relative_path == 40


@pytest.mark.skipif(os.name != "nt", reason="MAX_PATH budget applies to Windows installations")
def test_packaged_folder_too_deep_for_its_staging_fails_before_starting(installation):
    descriptor, spec = installation
    spec = copy.deepcopy(spec)
    spec["install"] = {"max_relative_path": 250}  # leaves 8 characters for the folder
    save(descriptor, spec)
    with pytest.raises(RuntimeDescriptorError, match="ruta demasiado larga.*máximo 8"):
        get_runtime()


@pytest.mark.parametrize("value", [None, {}, {"max_relative_path": 0}, {"max_relative_path": 5000},
                                   {"max_relative_path": "40"}, {"max_relative_path": True},
                                   {"max_relative_path": 40.0}, {"max_relative_path": 40, "extra": 1}])
def test_invalid_install_limits_are_rejected(installation, value):
    descriptor, spec = installation
    spec = copy.deepcopy(spec)
    spec["install"] = value
    save(descriptor, spec)
    with pytest.raises(RuntimeDescriptorError):
        get_runtime()


def test_development_is_never_rejected_for_its_location(installation):
    descriptor, spec = installation
    spec = copy.deepcopy(spec)
    spec["mode"] = "development"
    spec["install"] = {"max_relative_path": 250}
    save(descriptor, spec)
    assert get_runtime().max_relative_path == 250


def test_windows_length_counts_utf16_units():
    from core.runtime_descriptor import install_root_limit, windows_path_length

    assert windows_path_length(r"C:\Iris ñ") == 9
    assert windows_path_length("C:\\😀") == 5
    assert install_root_limit(174) == 84


def test_windows_length_ignores_the_verbatim_prefix_the_launcher_passes():
    # Iris.exe hands Python the canonical (verbatim) descriptor path; both
    # resolvers must measure the same folder (seen in the A08.7 smoke).
    from core.runtime_descriptor import windows_path_length

    assert windows_path_length(r"\\?\C:\Iris") == windows_path_length(r"C:\Iris") == 7
    assert windows_path_length(r"\\?\UNC\server\share\Iris") == len(r"\\server\share\Iris")


@pytest.mark.skipif(os.name != "nt", reason="MAX_PATH budget applies to Windows installations")
def test_verbatim_descriptor_path_gets_the_same_budget(installation):
    descriptor, spec = installation
    from core.runtime_descriptor import windows_path_length

    spec = copy.deepcopy(spec)
    # Exactly at the limit for the plain path; 4 more characters if the prefix counted.
    spec["install"] = {"max_relative_path": 258 - windows_path_length(descriptor.parent.resolve())}
    save(descriptor, spec)
    runtime = load_runtime_descriptor(r"\\?" + "\\" + str(descriptor.resolve()))
    assert runtime.max_relative_path == spec["install"]["max_relative_path"]


def test_plain_windows_path_only_drops_the_verbatim_prefix():
    from core.runtime_descriptor import plain_windows_path

    assert plain_windows_path(r"\\?\C:\Iris ñ\runtime.json") == r"C:\Iris ñ\runtime.json"
    assert plain_windows_path(r"\\?\UNC\server\share\Iris") == r"\\server\share\Iris"
    assert plain_windows_path(r"\\?\Volume{x}\Iris") == r"\\?\Volume{x}\Iris"
    assert plain_windows_path(r"C:\Iris") == r"C:\Iris"


@pytest.mark.skipif(os.name != "nt", reason="verbatim paths are a Windows form")
def test_verbatim_descriptor_path_yields_paths_comparable_with_processes(installation):
    # A08.7: Iris.exe passed "\\?\C:\..."; every derived path kept the prefix,
    # so the bundled Gateway (reported by Windows without it) was never credited.
    descriptor, _ = installation
    runtime = load_runtime_descriptor(r"\\?" + "\\" + str(descriptor.resolve()))
    for value in (runtime.descriptor_path, runtime.base, runtime.iris_root, runtime.iris_python,
                  runtime.hermes.root, runtime.hermes.python, runtime.hermes.cli):
        assert not str(value).startswith(r"\\?"), value
    assert runtime.hermes.python == (descriptor.parent / "private/hermes/python.exe").resolve()
    assert runtime == load_runtime_descriptor(descriptor.resolve())


# ── Codex qa10 review, R1: the bytecode cache never works through a link ───

def _cache_profile():
    from core.runtime_descriptor import runtime_pycache_path

    path = runtime_pycache_path()
    return path.parents[2], path


def _foreign_version_cache(folder: Path, base: Path) -> Path:
    """A folder shaped like the cache of a removed version, with a witness file."""
    from core.runtime_descriptor import PYCACHE_BASE_MARKER, _cache_key

    entry = folder / _cache_key(base)
    entry.mkdir(parents=True)
    (entry / PYCACHE_BASE_MARKER).write_text(str(base), encoding="utf-8")
    (entry / "testigo.txt").write_text("no borrar", encoding="utf-8")
    return entry


@pytest.mark.parametrize("linked", ["cache", "pycache"])
def test_a_linked_cache_folder_is_never_written_or_pruned(installation, tmp_path, linked):
    import core.runtime_descriptor as descriptor

    home, current = _cache_profile()
    outside = tmp_path / "fuera del perfil"
    outside.mkdir()
    if linked == "cache":
        make_directory_alias(home / "cache", outside)
        witness = _foreign_version_cache(outside / "pycache", tmp_path / "version retirada")
    else:
        (home / "cache").mkdir()
        make_directory_alias(home / "cache" / "pycache", outside)
        witness = _foreign_version_cache(outside, tmp_path / "version retirada")
    try:
        before = sorted(p.relative_to(outside).as_posix() for p in outside.rglob("*"))
        assert descriptor.prune_runtime_pycache() == []
        assert descriptor.runtime_pycache_dir() is None  # falls back to -B
        assert descriptor.runtime_bytecode_flags() == ["-B"]
        assert (witness / "testigo.txt").read_text(encoding="utf-8") == "no borrar"
        # Nothing was created through the link either.
        assert sorted(p.relative_to(outside).as_posix() for p in outside.rglob("*")) == before
    finally:
        link = home / "cache" if linked == "cache" else home / "cache" / "pycache"
        link.rmdir() if os.name == "nt" else link.unlink()  # the junction itself, never its target


def test_a_linked_version_entry_is_kept_and_its_target_untouched(installation, tmp_path):
    import core.runtime_descriptor as descriptor

    home, current = _cache_profile()
    root = home / "cache" / "pycache"
    root.mkdir(parents=True)
    outside = _foreign_version_cache(tmp_path / "fuera", tmp_path / "version retirada")
    link = root / outside.name
    make_directory_alias(link, outside)
    try:
        assert descriptor.prune_runtime_pycache() == []
        assert (outside / "testigo.txt").exists() and os.path.lexists(link)
    finally:
        link.rmdir() if os.name == "nt" else link.unlink()


def test_only_caches_this_code_created_for_a_removed_version_are_pruned(installation, tmp_path):
    import core.runtime_descriptor as descriptor
    from core.runtime_descriptor import PYCACHE_BASE_MARKER

    home, current = _cache_profile()
    assert descriptor.runtime_pycache_dir() == current
    root = current.parent
    retired = _foreign_version_cache(root, tmp_path / "version retirada")
    kept_existing = _foreign_version_cache(root, tmp_path)  # its version folder still exists
    foreign_name = root / "no-es-una-clave"
    foreign_name.mkdir()
    (foreign_name / PYCACHE_BASE_MARKER).write_text(str(tmp_path / "otra"), encoding="utf-8")
    mismatched = root / ("0" * 12)  # marker names a base whose key is another one
    mismatched.mkdir()
    (mismatched / PYCACHE_BASE_MARKER).write_text(str(tmp_path / "otra"), encoding="utf-8")
    no_marker = root / ("1" * 12)
    no_marker.mkdir()

    assert descriptor.prune_runtime_pycache() == [retired.name]
    assert not retired.exists()
    assert current.is_dir() and kept_existing.is_dir() and foreign_name.is_dir()
    assert mismatched.is_dir() and no_marker.is_dir()


def test_another_versions_marker_in_this_folder_disables_the_cache(installation):
    import core.runtime_descriptor as descriptor
    from core.runtime_descriptor import PYCACHE_BASE_MARKER

    home, current = _cache_profile()
    current.mkdir(parents=True)
    (current / PYCACHE_BASE_MARKER).write_text("C:/otra/version", encoding="utf-8")
    assert descriptor.runtime_pycache_dir() is None


def _tree(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))


def _child_imports_a_module(tmp_path: Path) -> Path:
    """Start a Python child the way Iris starts Hermes' helpers and import a fresh module."""
    import sys

    from core.runtime_descriptor import runtime_bytecode_flags

    source = tmp_path / "fuente del hijo"
    source.mkdir(exist_ok=True)
    (source / "modulo_qa10.py").write_text("VALOR = 1\n", encoding="utf-8")
    env = runtime_subprocess_env({"SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "PATH": ""})
    script = f"import sys; sys.path.insert(0, {str(source)!r}); import modulo_qa10"
    result = subprocess.run(
        [sys.executable, "-s", *runtime_bytecode_flags(), "-c", script],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return source


@pytest.mark.parametrize("level", ["cache", "pycache", "entry"])
def test_a_prepared_cache_replaced_by_a_link_is_refused_before_the_next_process(installation, tmp_path, level):
    # Codex qa10 closure, C1: the first preparation was remembered, so a folder
    # swapped for a link afterwards still received the next processes' bytecode.
    import core.runtime_descriptor as descriptor
    from core.runtime_descriptor import PYCACHE_BASE_MARKER

    home, current = _cache_profile()
    assert descriptor.runtime_pycache_dir() == current  # prepared once, legitimately
    names = {"cache": home / "cache", "pycache": home / "cache" / "pycache", "entry": current}
    replaced = names[level]
    kept = replaced.with_name(replaced.name + "-conservada")
    replaced.rename(kept)  # kept, never deleted
    outside = tmp_path / "fuera del perfil"
    # The outside folder even carries this version's own marker: only the chain can refuse it.
    rest = current.relative_to(replaced)
    (outside / rest).mkdir(parents=True)
    (outside / rest / PYCACHE_BASE_MARKER).write_text(str(get_runtime().base), encoding="utf-8")
    make_directory_alias(replaced, outside)
    try:
        before = _tree(outside)
        assert descriptor.runtime_pycache_dir() is None
        assert descriptor.runtime_bytecode_flags() == ["-B"]
        env = runtime_subprocess_env({})
        assert env["PYTHONDONTWRITEBYTECODE"] == "1" and "PYTHONPYCACHEPREFIX" not in env
        source = _child_imports_a_module(tmp_path)
        assert _tree(outside) == before  # no folder and no .pyc outside the profile
        assert not (source / "__pycache__").exists()
        assert (kept / rest / PYCACHE_BASE_MARKER).is_file()
    finally:
        replaced.rmdir() if os.name == "nt" else replaced.unlink()  # the link, never its target


def test_a_prepared_cache_is_reused_while_it_stays_valid(installation, tmp_path):
    import core.runtime_descriptor as descriptor
    from core.runtime_descriptor import PYCACHE_BASE_MARKER

    home, current = _cache_profile()
    assert descriptor.runtime_pycache_dir() == current
    marker = (current / PYCACHE_BASE_MARKER).read_bytes()
    assert descriptor.runtime_pycache_dir() == current
    assert (current / PYCACHE_BASE_MARKER).read_bytes() == marker
    _child_imports_a_module(tmp_path)
    assert any(path.suffix == ".pyc" for path in current.rglob("*"))  # the cache does its job


def test_a_changed_marker_or_a_file_in_place_of_the_folder_disables_a_prepared_cache(installation, tmp_path):
    import core.runtime_descriptor as descriptor
    from core.runtime_descriptor import PYCACHE_BASE_MARKER

    home, current = _cache_profile()
    assert descriptor.runtime_pycache_dir() == current
    marker = current / PYCACHE_BASE_MARKER
    original = marker.read_bytes()
    marker.write_text("C:/otra/version", encoding="utf-8")
    assert descriptor.runtime_pycache_dir() is None
    assert descriptor.runtime_bytecode_flags() == ["-B"]
    marker.write_bytes(original)
    assert descriptor.runtime_pycache_dir() == current

    kept = current.with_name(current.name + "-conservada")
    current.rename(kept)
    current.write_text("no soy una carpeta", encoding="utf-8")
    assert descriptor.runtime_pycache_dir() is None
    assert descriptor.runtime_bytecode_flags() == ["-B"]
    assert current.read_text(encoding="utf-8") == "no soy una carpeta"
    assert (kept / PYCACHE_BASE_MARKER).read_bytes() == original


def test_a_prepared_cache_whose_marker_becomes_a_link_is_refused(installation, tmp_path):
    import core.runtime_descriptor as descriptor
    from core.runtime_descriptor import PYCACHE_BASE_MARKER

    home, current = _cache_profile()
    assert descriptor.runtime_pycache_dir() == current
    marker = current / PYCACHE_BASE_MARKER
    outside = tmp_path / "marcador de fuera.txt"
    outside.write_bytes(marker.read_bytes())
    marker.rename(current / "marcador-conservado.txt")
    try:
        os.symlink(outside, marker)
    except OSError as exc:  # file symlinks need a privilege on Windows
        pytest.skip(f"no file symlinks here: {exc}")
    try:
        assert descriptor.runtime_pycache_dir() is None
        assert descriptor.runtime_bytecode_flags() == ["-B"]
    finally:
        marker.unlink()
