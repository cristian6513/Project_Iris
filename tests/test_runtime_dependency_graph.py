from pathlib import Path
from types import SimpleNamespace

import pytest
from packaging.requirements import Requirement

from scripts.verify_runtime_dependencies import check_dependencies, exclusion_policy, locked_requirements


def distribution(name, version="1.0", requires=()):
    return SimpleNamespace(metadata={"Name": name}, version=version, requires=list(requires))


def test_actual_windows_metadata_detects_dependencies_missing_from_universal_lock():
    packages = [distribution("sherpa-onnx", "1.13.4", ["sherpa-onnx-core==1.13.4"])]
    with pytest.raises(ValueError, match="Missing dependency: sherpa-onnx-core==1.13.4"):
        check_dependencies(packages, [Requirement("sherpa-onnx==1.13.4")], {})


def test_incompatible_crypto_is_rejected_and_minimal_msal_upgrade_is_accepted():
    packages = [distribution("cryptography", "50.0.0"), distribution("msal", "1.36", ["cryptography<49"])]
    with pytest.raises(ValueError, match="Incompatible dependency"):
        check_dependencies(packages, [], {})
    packages[1] = distribution("msal", "1.37", ["cryptography>=2.5,<51"])
    assert len(check_dependencies(packages, [], {})) == 2


def test_amd64_markers_and_recursive_requested_extras_are_checked():
    environment = {"platform_machine": "AMD64", "sys_platform": "win32"}
    packages = [distribution("parent", requires=["child[voice]; sys_platform == 'win32'"]),
                distribution("child", requires=["audio; extra == 'voice'", "unix; sys_platform == 'linux'"])]
    with pytest.raises(ValueError, match="Missing dependency: audio"):
        check_dependencies(packages, [], environment)
    packages.append(distribution("audio"))
    assert len(check_dependencies(packages, [Requirement("parent; platform_machine=='AMD64'")], environment)) == 3


@pytest.mark.parametrize("line", ["--index-url https://example.test", "package>=1", "package==1.*", "package @ https://example.test/wheel.whl"])
def test_dependency_check_refuses_resolution_and_url_inputs(tmp_path, line):
    lock = tmp_path / "lock.txt"
    lock.write_text(line)
    with pytest.raises(ValueError):
        locked_requirements(lock)


def test_hashes_and_markers_preserve_exact_lock_constraints(tmp_path):
    lock = tmp_path / "lock.txt"
    lock.write_text("# export\npackage==1.0 ; sys_platform == 'win32' \\\n    --hash=sha256:" + "a" * 64 + "\n")
    assert str(locked_requirements(lock)[0]) == 'package==1.0; sys_platform == "win32"'


WINDOWS = {"sys_platform": "win32", "platform_machine": "AMD64", "python_version": "3.14", "os_name": "nt"}


def test_a_requirement_this_runtime_never_installs_need_not_be_exact(tmp_path):
    # Hermes 0.21.6 locks an Android-only psutil from Git next to the exact Windows one.
    lock = tmp_path / "lock.txt"
    lock.write_text("psutil==7.2.2 ; sys_platform != 'android' \\\n    --hash=sha256:" + "a" * 64 + "\n"
                    "psutil @ git+https://github.com/giampaolo/psutil.git@" + "3" * 40 + " ; sys_platform == 'android'\n")
    assert [str(item) for item in locked_requirements(lock, WINDOWS)] == ['psutil==7.2.2; sys_platform != "android"']
    with pytest.raises(ValueError, match="exact version"):
        locked_requirements(lock)  # without the runtime's environment every line must be exact


@pytest.mark.parametrize("line", [
    "package @ https://example.test/wheel.whl ; sys_platform == 'win32'", "package>=1 ; sys_platform == 'win32'",
    "package==1.* ; python_version >= '3.14'",
])
def test_a_requirement_this_runtime_installs_must_still_be_exact(tmp_path, line):
    lock = tmp_path / "lock.txt"
    lock.write_text(line)
    with pytest.raises(ValueError):
        locked_requirements(lock, WINDOWS)


def test_duplicate_distribution_does_not_hide_incompatible_metadata():
    with pytest.raises(ValueError, match="Duplicate installed"):
        check_dependencies([distribution("name-one"), distribution("name_one")], [], {})


# A08.4b: fetch-use (no published license text) is left out of the Hermes runtime,
# only for the reviewed dependent browser-harness 0.1.13 (Codex review of the exclusion).

LOCK = [Requirement("fetch-use==0.4.0"), Requirement("cdp-use==1.4.5"), Requirement("numpy==1.26.0")]
POLICY = exclusion_policy(LOCK, ["fetch-use==0.4.0"])


def harness(version="0.1.13", needs="fetch-use==0.4.0"):
    return [distribution("browser-harness", version, [needs, "cdp-use==1.4.5"]), distribution("cdp-use", "1.4.5")]


def test_the_reviewed_dependent_may_require_the_excluded_package():
    with pytest.raises(ValueError, match="Missing dependency: fetch-use==0.4.0"):
        check_dependencies(harness(), [], {})
    assert len(check_dependencies(harness(), [], {}, POLICY)) == 2


@pytest.mark.parametrize("packages,message", [
    (harness(version="0.1.14"), "unreviewed dependent: browser-harness==0.1.14"),
    (harness() + [distribution("other-tool", "1.0", ["fetch-use==0.4.0"])], "unreviewed dependent: other-tool==1.0"),
    (harness(needs="fetch-use>=0.5"), "not the reviewed fetch-use==0.4.0"),
    (harness() + [distribution("fetch-use", "0.4.0")], "Excluded package is installed: fetch-use"),
])
def test_an_exclusion_never_hides_an_unreviewed_requirement(packages, message):
    with pytest.raises(ValueError, match=message):
        check_dependencies(packages, [], {}, POLICY)


def test_other_missing_dependencies_are_still_reported_with_an_exclusion():
    packages = harness() + [distribution("audio", "1.0", ["numpy==1.26.0"])]
    with pytest.raises(ValueError, match="Missing dependency: numpy==1.26.0"):
        check_dependencies(packages, [], {}, POLICY)


@pytest.mark.parametrize("item,message", [
    ("numpy==1.26.0", "Not a reviewed exclusion"), ("fetch-use==0.3.3", "Not a reviewed exclusion"),
    ("fetch-use>=0.4", "exact locked version"), ("fetch-use==0.4.0 ; sys_platform == 'win32'", "exact locked version"),
])
def test_only_reviewed_exclusions_are_accepted(item, message):
    with pytest.raises(ValueError, match=message):
        exclusion_policy(LOCK, [item])


def test_a_reviewed_exclusion_must_match_the_lock_and_appear_once():
    with pytest.raises(ValueError, match="exactly one locked requirement"):
        exclusion_policy([Requirement("fetch-use==0.3.3")], ["fetch-use==0.4.0"])
    with pytest.raises(ValueError, match="exactly one locked requirement"):
        exclusion_policy([Requirement("cdp-use==1.4.5")], ["fetch-use==0.4.0"])
    with pytest.raises(ValueError, match="Not a reviewed exclusion"):
        exclusion_policy(LOCK, ["fetch-use==0.4.0", "fetch_use==0.4.0"])


def test_the_real_hermes_lock_keeps_157_requirements_with_the_exclusion():
    environment = {**WINDOWS, "python_full_version": "3.14.8", "platform_system": "Windows",
                   "implementation_name": "cpython", "platform_python_implementation": "CPython"}
    lock = locked_requirements(Path(__file__).resolve().parents[1] / "scripts/locks/hermes-windows-cp314.txt", environment)
    policy = exclusion_policy(lock, ["fetch-use==0.4.0"])
    assert len([r for r in lock if r.name not in policy]) == 157


def test_builder_and_verifier_enforce_the_same_reviewed_exclusions():
    from scripts import prepare_private_runtime as builder
    from scripts import verify_runtime_dependencies as verifier

    builder_view = {name: {"version": item["version"], "dependents": item["dependents"]}
                    for name, item in builder.LOCAL_EXCLUSIONS.items()}
    assert builder_view == verifier.REVIEWED_EXCLUSIONS
