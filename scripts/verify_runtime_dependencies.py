"""Check the installed Windows dependency graph without resolving or downloading.

Run with the private interpreter, whose ._pth supplies its isolated search path.
This checks actual wheel metadata, including dependencies absent from an upstream
universal lock. It never edits installed metadata to conceal an incompatibility.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import platform
import sys
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


# Packages a runtime may leave out, each with the exact dependents reviewed to work
# without it. It mirrors LOCAL_EXCLUSIONS in prepare_private_runtime.py (a test
# keeps both equal); a new version of either side needs a new review.
# fetch-use publishes no license text; browser-harness 0.1.13 imports it only for
# the Browser Use cloud proxy and falls back to plain urllib HTTP without it.
REVIEWED_EXCLUSIONS = {
    "fetch-use": {"version": "0.4.0", "dependents": {"browser-harness": "0.1.13"}},
}


def check_dependencies(distributions, requirements, environment, excluded=None):
    """``excluded``: ``{name: {"version", "dependents": {name: version}}}`` left out of this
    runtime (``exclusion_policy``). Only those exact dependents may require it, and it
    must not be installed; every other missing dependency is still an error."""
    excluded = excluded or {}
    installed = {}
    for distribution in distributions:
        name = canonicalize_name(distribution.metadata["Name"])
        if name in installed:
            raise ValueError(f"Duplicate installed distribution: {name}")
        installed[name] = distribution
    if set(excluded) & set(installed):
        raise ValueError(f"Excluded package is installed: {', '.join(sorted(set(excluded) & set(installed)))}")
    activated, processed, errors = {}, set(), set()
    # (requirement, parent): parent is (name, version) of the distribution that
    # declares it, or None for the lock and the installed set themselves.
    pending = [(requirement, None) for requirement in requirements]
    # Every installed package is checked, including libraries whose parent is
    # source-only Hermes rather than a separately installed distribution.
    pending.extend((Requirement(name), None) for name in installed)
    while pending:
        requirement, parent = pending.pop()
        name = canonicalize_name(requirement.name)
        if requirement.marker and not requirement.marker.evaluate({**environment, "extra": ""}):
            continue
        if name in excluded:
            policy = excluded[name]
            if parent is None or policy["dependents"].get(parent[0]) != parent[1]:
                who = f"{parent[0]}=={parent[1]}" if parent else "the runtime lock"
                errors.add(f"Excluded {name} is required by an unreviewed dependent: {who}")
            elif requirement.specifier and not requirement.specifier.contains(policy["version"]):
                errors.add(f"{parent[0]} requires {requirement}, not the reviewed {name}=={policy['version']}")
            continue
        distribution = installed.get(name)
        if distribution is None:
            errors.add(f"Missing dependency: {requirement}")
            continue
        if requirement.specifier and not requirement.specifier.contains(distribution.version):
            errors.add(f"Incompatible dependency: {requirement}; installed {distribution.version}")
        extras = activated.setdefault(name, {""})
        extras.update(requirement.extras)
        for extra in sorted(extras):
            if (name, extra) in processed:
                continue
            processed.add((name, extra))
            for raw in distribution.requires or []:
                dependency = Requirement(raw)
                if dependency.marker and not dependency.marker.evaluate({**environment, "extra": extra}):
                    continue
                dependency.marker = None
                pending.append((dependency, (name, str(distribution.version))))
    if errors:
        raise ValueError("\n".join(sorted(errors)))
    return sorted(
        ({"package": name, "version": dist.version} for name, dist in installed.items()),
        key=lambda item: item["package"],
    )


def locked_requirements(path, environment=None):
    """Exact ``==`` requirements of the lock that apply to ``environment``.

    A requirement whose marker excludes this runtime is never installed here, so
    it need not be exact (Hermes 0.21.6 locks an Android-only ``psutil`` from Git).
    """
    requirements = []
    for raw in Path(path).read_text("utf8").splitlines():
        line = raw.strip().rstrip("\\").strip()
        if not line or line.startswith(("#", "--hash=")):
            continue
        if line.startswith("-"):
            raise ValueError("Only pinned package requirements are accepted")
        requirement = Requirement(line)
        if (environment is not None and requirement.marker
                and not requirement.marker.evaluate({**environment, "extra": ""})):
            continue
        if requirement.url or len(list(requirement.specifier)) != 1:
            raise ValueError("Expected an exact version in the runtime lock")
        specifier = next(iter(requirement.specifier))
        if specifier.operator != "==" or "*" in specifier.version:
            raise ValueError("Expected an exact version in the runtime lock")
        requirements.append(requirement)
    return requirements


def exclusion_policy(requirements, excluded, reviewed=None):
    """The reviewed policy of each ``name==version`` exclusion (``REVIEWED_EXCLUSIONS``).

    The exclusion must be reviewed for that exact version and match exactly one
    locked requirement; anything else is refused, whatever the lock says.
    """
    reviewed = REVIEWED_EXCLUSIONS if reviewed is None else reviewed
    policy = {}
    for item in excluded:
        requirement = Requirement(item)
        specifier = list(requirement.specifier)
        if requirement.marker or requirement.url or len(specifier) != 1 or specifier[0].operator != "==":
            raise ValueError(f"An exclusion names an exact locked version: {item}")
        name, version = canonicalize_name(requirement.name), specifier[0].version
        approved = reviewed.get(name)
        if approved is None or approved["version"] != version or name in policy:
            raise ValueError(f"Not a reviewed exclusion: {item}")
        matches = [r for r in requirements if canonicalize_name(r.name) == name]
        if len(matches) != 1 or str(next(iter(matches[0].specifier))) != str(specifier[0]):
            raise ValueError(f"Exclusion does not match exactly one locked requirement: {item}")
        policy[name] = {"version": version, "dependents": {
            canonicalize_name(dependent): dependent_version
            for dependent, dependent_version in approved["dependents"].items()}}
    return policy


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pin", type=Path, help="Verify an exact reviewed Hermes dependency override")
    parser.add_argument("--excluded", action="append", default=[],
                        help="name==version left out of this runtime; only REVIEWED_EXCLUSIONS are accepted")
    args = parser.parse_args(argv)
    prefix = Path(sys.prefix).resolve()
    if os.name != "nt" or platform.machine() != "AMD64":
        raise ValueError("The private runtime check requires Windows AMD64")
    hermes_source = prefix.parent.parent / "hermes" if prefix.name == "hermes" else None
    for path in sys.path:
        resolved = Path(path).resolve() if path else None
        if not resolved or not (resolved.is_relative_to(prefix) or resolved == hermes_source):
            raise ValueError(f"Unexpected private interpreter search path: {path}")
    overrides = []
    if args.pin is not None:
        stage = prefix.parent.parent
        if prefix.name != "hermes" or args.pin.resolve() != (stage / "runtime-pin.json").resolve():
            raise ValueError("Dependency override pin belongs to a different runtime")
        pin = json.loads(args.pin.read_text("utf-8"))
        if args.lock.resolve() != (stage / pin["locks"]["hermes"]["path"]).resolve():
            raise ValueError("Dependency override lock belongs to a different runtime")
        # Explicit sibling loading keeps the private interpreter search path intact.
        spec = importlib.util.spec_from_file_location(
            "iris_reviewed_runtime_overrides", Path(__file__).with_name("runtime_dependency_overrides.py"),
        )
        policy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(policy)
        overrides = policy.verify_dependency_overrides(pin, args.lock.read_text("utf-8"), stage / "hermes")
    environment = default_environment()
    requirements = locked_requirements(args.lock, environment)
    excluded = exclusion_policy(requirements, args.excluded)
    records = check_dependencies(
        list(importlib.metadata.distributions()),
        [r for r in requirements if canonicalize_name(r.name) not in excluded], environment, excluded,
    )
    document = {
        "python": platform.python_version(), "architecture": platform.machine(),
        "packages": records, "excluded": sorted(args.excluded), "search_paths": sys.path, "ok": True,
        "dependency_overrides": overrides,
    }
    if args.output:
        args.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf8")
    print(json.dumps({"ok": True, "packages": len(records)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
