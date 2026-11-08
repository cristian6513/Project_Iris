"""A08.6: third-party notices come from the installation's own inventories."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from core.runtime_descriptor import DESCRIPTOR_ENV, get_runtime
from tests.runtime_descriptor_fixtures import write_runtime_fixture
from wizard import licenses

LOCAL = {"base_url": "http://127.0.0.1:8765", "client": ("127.0.0.1", 50124)}
HERMES_LICENSE = b"MIT License\n\nCopyright (c) 2025 Nous Research\n"
THREE_LICENSE = b"The MIT License\n\nCopyright three.js authors\n"
LAME_COPYING = b"GNU LIBRARY GENERAL PUBLIC LICENSE\n"


def _entry(root: Path, relative: str) -> dict:
    data = (root / relative).read_bytes()
    return {"path": relative, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _write(root: Path, relative: str, data: bytes) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """A packaged layout with A08.4's runtime inventory and A08.3's web manifest."""
    descriptor, _spec = write_runtime_fixture(tmp_path / "Iris instalada á")
    monkeypatch.setenv(DESCRIPTOR_ENV, str(descriptor))
    monkeypatch.setenv("IRIS_HOME", str(tmp_path / "perfil"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "perfil/hermes"))
    licenses._cache.clear()
    runtime = get_runtime()
    base, iris = runtime.base, runtime.iris_root
    _write(base, "private/hermes/app/LICENSE", HERMES_LICENSE)
    _write(base, "licenses/audio/sources/lame-4.0.tar.gz", b"\x1f\x8b fixture archive")
    _write(base, "licenses/audio/LAME-COPYING", LAME_COPYING)
    inventory = {
        "schema": 1,
        "licenses": [
            {"component": "hermes-source", "package": "hermes-agent", "version": "0.21.4", "license": "MIT",
             "files": ["private/hermes/app/LICENSE"], "source": "https://github.com/NousResearch/hermes-agent"},
            {"component": "audio", "package": "LAME", "version": "4.0", "license": "LGPL-2.0-or-later",
             "files": ["licenses/audio/LAME-COPYING", "licenses/audio/sources/lame-4.0.tar.gz"],
             "source": "https://lame.sourceforge.io"},
        ],
        "files": [_entry(base, "private/hermes/app/LICENSE"), _entry(base, "licenses/audio/LAME-COPYING"),
                  _entry(base, "licenses/audio/sources/lame-4.0.tar.gz")],
    }
    (base / licenses.RUNTIME_INVENTORY).write_text(json.dumps(inventory), encoding="utf-8")
    _write(iris, "face/vendor/licenses/three-LICENSE.txt", THREE_LICENSE)
    web = {"schema": 1, "licenses": [{"package": "three", "version": "0.170.0", "license": "MIT",
                                      "files": ["face/vendor/licenses/three-LICENSE.txt"]}],
           "files": [_entry(iris, "face/vendor/licenses/three-LICENSE.txt")]}
    (iris / licenses.WEB_MANIFEST).write_text(json.dumps(web), encoding="utf-8")
    yield runtime
    licenses._cache.clear()


def test_packaged_catalog_lists_runtime_and_web_notices_without_paths(installed):
    catalog = licenses.license_catalog()
    assert catalog["mode"] == "packaged" and catalog["complete"] is True
    assert {s["id"]: s["available"] for s in catalog["sources"]} == {"runtime": True, "web": True}
    packages = {c["package"]: c for c in catalog["components"]}
    assert packages["hermes-agent"]["group_label"] == "Hermes Agent"
    assert packages["three"]["group_label"] == "Interfaz web y fuentes"
    assert packages["LAME"]["files"] == [{"path": "licenses/audio/LAME-COPYING", "viewable": True},
                                         {"path": "licenses/audio/sources/lame-4.0.tar.gz", "viewable": False}]
    assert str(installed.base) not in json.dumps(catalog)


def test_notice_text_is_served_only_after_its_recorded_hash_matches(installed):
    assert licenses.license_text("runtime", "private/hermes/app/LICENSE")["text"] == HERMES_LICENSE.decode()
    assert licenses.license_text("web", "face/vendor/licenses/three-LICENSE.txt")["text"].startswith("The MIT")
    (installed.base / "private/hermes/app/LICENSE").write_bytes(HERMES_LICENSE.replace(b"MIT", b"XYZ"))
    with pytest.raises(HTTPException) as caught:
        licenses.license_text("runtime", "private/hermes/app/LICENSE")
    assert caught.value.status_code == 409


@pytest.mark.parametrize("source,path,status", [
    ("runtime", "runtime.json", 404),                       # in the installation, but not a notice
    ("runtime", "../outside.txt", 404),
    ("runtime", "C:/Windows/win.ini", 404),
    ("runtime", "private\\hermes\\app\\LICENSE", 404),
    ("runtime", "/private/hermes/app/LICENSE", 404),
    ("runtime", "licenses/audio/sources/lame-4.0.tar.gz", 415),  # source archive: listed, not shown
    ("web", "private/hermes/app/LICENSE", 404),             # a notice of another inventory
    ("unknown", "LICENSE", 404),
    ("project", "LICENSE", 404),                            # development-only source
])
def test_only_listed_text_notices_are_readable(installed, source, path, status):
    with pytest.raises(HTTPException) as caught:
        licenses.license_text(source, path)
    assert caught.value.status_code == status


@pytest.mark.skipif(os.name != "nt", reason="directory junctions are Windows-specific")
def test_a_junction_inside_the_installation_cannot_redirect_a_notice(installed, tmp_path):
    assert licenses.license_text("runtime", "licenses/audio/LAME-COPYING")["text"].startswith("GNU")
    outside = tmp_path / "fuera"
    outside.mkdir()
    (outside / "LAME-COPYING").write_bytes(LAME_COPYING)  # same bytes and hash, different place
    audio = installed.base / "licenses/audio"
    moved = installed.base / "licenses/audio-real"
    audio.rename(moved)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(audio), str(outside)], check=True, capture_output=True)
    try:
        with pytest.raises(HTTPException) as caught:
            licenses.license_text("runtime", "licenses/audio/LAME-COPYING")
        assert caught.value.status_code == 404
    finally:
        os.rmdir(audio)  # the junction itself, never its target
        moved.rename(audio)
    assert (outside / "LAME-COPYING").read_bytes() == LAME_COPYING


def test_missing_or_inconsistent_inventory_is_reported_not_guessed(installed):
    (installed.iris_root / licenses.WEB_MANIFEST).unlink()
    inventory = installed.base / licenses.RUNTIME_INVENTORY
    document = json.loads(inventory.read_text(encoding="utf-8"))
    document["files"] = document["files"][1:]  # the Hermes notice is no longer vouched for
    inventory.write_text(json.dumps(document), encoding="utf-8")
    catalog = licenses.license_catalog()
    assert catalog["complete"] is False and catalog["components"] == []
    assert {s["id"]: s["available"] for s in catalog["sources"]} == {"runtime": False, "web": False}


def test_development_shows_the_project_license_and_says_the_inventory_is_built_later(tmp_path, monkeypatch):
    monkeypatch.delenv(DESCRIPTOR_ENV, raising=False)
    licenses._cache.clear()
    catalog = licenses.license_catalog()
    assert catalog["mode"] == "development" and catalog["complete"] is False
    assert "runtime" not in {s["id"] for s in catalog["sources"]}
    assert licenses.license_text("project", "LICENSE")["text"].startswith("MIT License")
    with pytest.raises(HTTPException):
        licenses.license_text("runtime", "hermes/LICENSE")


def test_license_routes_are_read_only_and_not_cached(installed):
    with TestClient(licenses_app(), **LOCAL) as client:
        listing = client.get("/api/licenses")
        text = client.get("/api/licenses/text", params={"source": "runtime", "path": "private/hermes/app/LICENSE"})
        refused = client.post("/api/licenses")
    assert listing.status_code == 200 and listing.headers["cache-control"] == "no-store"
    assert text.json()["text"] == HERMES_LICENSE.decode()
    assert text.headers["x-content-type-options"] == "nosniff"
    assert refused.status_code == 405


def licenses_app():
    from wizard.app import app

    return app


def test_manual_renders_notices_as_text_never_markup():
    html = (Path(__file__).resolve().parents[1] / "wizard/ui/help.html").read_text(encoding="utf-8")
    script = html[html.index("const licenseGroups"):]
    assert "licenseViewerText.textContent" in script and "innerHTML" not in script
    assert "/^https:\\/\\//.test(item.url)" in script
    assert 'id="licenses"' in html and 'href="#licenses"' in html


def test_wizard_pages_are_revalidated_and_scripts_keep_their_versioned_cache():
    """Review 2026-10-19: a heuristically cached page kept loading the previous wizard.js."""
    with TestClient(licenses_app(), **LOCAL) as client:
        for path in ("/ui/", "/ui/index.html", "/ui/help.html"):
            response = client.get(path)
            assert response.status_code == 200, path
            assert response.headers["cache-control"] == "no-cache", path
            assert response.headers.get("etag"), path
        script = client.get("/ui/wizard.js")
        assert script.status_code == 200 and script.headers.get("cache-control") != "no-cache"
        assert client.get("/api/licenses").headers["cache-control"] == "no-store"
