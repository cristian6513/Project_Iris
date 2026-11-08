from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from scripts.verify_distribution import DistributionError, forbidden_reason, verify_archive


def _wheel(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return path


@pytest.mark.parametrize(
    "name",
    (
        "iris/face/animations/private.vrma",
        "iris/face/animations/draft.glb",
        "iris/.hermes/skills/iris-os/scripts/__pycache__/control.pyc",
        "iris/avatar.blend",
        "iris/.env.local",
        "iris/server.key",
        "iris/identity.p12",
        "iris/credentials-prod.json",
        "iris/secrets.local.json",
    ),
)
def test_distribution_policy_rejects_machine_local_content(name: str) -> None:
    assert forbidden_reason(name)


def test_distribution_policy_keeps_public_animation_contracts() -> None:
    assert forbidden_reason("iris/face/animations/manifest.json") is None
    assert forbidden_reason("iris/face/animations/README.md") is None


def test_distribution_archive_reports_private_animation(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path / "iris.whl", {"face/animations/private.vrma": b"private"})

    with pytest.raises(DistributionError, match="animación privado"):
        verify_archive(wheel)


def test_distribution_validates_optional_companion_installer_hash(tmp_path: Path) -> None:
    from test_distribution_assets import fixture_contract

    contract, assets = fixture_contract()
    required = {f"face/animations/{name}": data for name, data in assets.items()}
    installer = b"MZ" + (b"safe-release" * 8)
    filename = "IRIS Desktop Companion_0.1.1_x64-setup.exe"
    manifest = {
        "filename": filename,
        "sha256": hashlib.sha256(installer).hexdigest(),
    }
    wheel = _wheel(
        tmp_path / "iris.whl",
        {
            **required,
            f"iris.data/data/share/iris-os/companion/{filename}": installer,
            "iris.data/data/share/iris-os/companion/release-manifest.json": json.dumps(
                manifest
            ).encode(),
        },
    )

    assert verify_archive(wheel, contract=contract, web_contract=None) == wheel

    bad_manifest = dict(manifest, sha256="0" * 64)
    bad_wheel = _wheel(
        tmp_path / "bad.whl",
        {
            **required,
            f"iris.data/data/share/iris-os/companion/{filename}": installer,
            "iris.data/data/share/iris-os/companion/release-manifest.json": json.dumps(
                bad_manifest
            ).encode(),
        },
    )
    with pytest.raises(DistributionError, match="hash"):
        verify_archive(bad_wheel, contract=contract, web_contract=None)
