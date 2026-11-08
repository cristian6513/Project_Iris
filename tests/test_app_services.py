"""F09.3: Core mounts the Python service of an app only when reviewed code lists it.

The service lives under /api/apps/<id>/, behind Core's own HTTP protections, and gets a data
folder of its own in the profile that no request can choose.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import app_services
from core.app_actions import AppActionRegistry
from core.app_services import AppServiceError, app_data_dir, include_app_services, load_app_router

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "apps" / "_plantilla-servicio"


def test_no_app_service_ships_unreviewed() -> None:
    # The templates show the shape; none is mounted by the product.
    assert app_services.REVIEWED_APP_SERVICES == ()
    import core.websocket_server as server

    assert not [route.path for route in server.app.routes if "/api/apps/" in getattr(route, "path", "")]


@pytest.mark.parametrize("bad", ["", "../core", "Correo", "a", "x" * 40, "mi_app", "mi-app/../../x"])
def test_an_app_id_never_becomes_a_path(tmp_path: Path, bad: str) -> None:
    with pytest.raises(AppServiceError):
        app_data_dir(bad, tmp_path)
    with pytest.raises(AppServiceError):
        load_app_router(bad, home=tmp_path, source_dir=TEMPLATE)


def test_each_app_gets_its_own_folder_inside_the_profile(tmp_path: Path) -> None:
    one = app_data_dir("mi-servicio", tmp_path)
    other = app_data_dir("otra-app", tmp_path)
    assert one == (tmp_path / "app-data" / "mi-servicio").resolve()
    assert other.parent == one.parent and other != one
    assert one.is_dir()


def test_a_linked_data_folder_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "fuera"
    outside.mkdir()
    (tmp_path / "home" / "app-data").mkdir(parents=True)
    link = tmp_path / "home" / "app-data" / "mi-servicio"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        try:
            import _winapi  # Windows: a junction needs no privilege

            _winapi.CreateJunction(str(outside), str(link))
        except (ImportError, OSError):
            pytest.skip("Este sistema no permite crear enlaces de carpeta.")
    with pytest.raises(AppServiceError):
        app_data_dir("mi-servicio", tmp_path / "home")


def test_only_listed_apps_are_mounted_and_one_failure_stays_local(tmp_path: Path, monkeypatch) -> None:
    app = FastAPI()
    monkeypatch.setattr(app_services, "app_source_dirs", lambda app_id: [TEMPLATE] if app_id == "mi-servicio" else [tmp_path / "nada"])
    mounted = include_app_services(app, home=tmp_path, ids=["sin-servicio", "mi-servicio"], operations=AppActionRegistry())
    assert mounted == ["mi-servicio"]
    client = TestClient(app)
    assert client.get("/api/apps/mi-servicio/items").json()["source"] == "ejemplo"
    assert client.get("/api/apps/sin-servicio/items").status_code == 404
    assert include_app_services(FastAPI(), home=tmp_path, ids=[]) == []


def test_a_broken_service_is_reported_not_mounted(tmp_path: Path) -> None:
    broken = tmp_path / "rota" / "backend"
    broken.mkdir(parents=True)
    (broken / "service.py").write_text("raise RuntimeError('al importar')\n", encoding="utf-8")
    with pytest.raises(AppServiceError, match="falló al importarse"):
        load_app_router("rota", home=tmp_path, source_dir=tmp_path / "rota")
    (broken / "service.py").write_text("def create_router(context):\n    return 'no es un router'\n", encoding="utf-8")
    with pytest.raises(AppServiceError, match="no devolvió un APIRouter"):
        load_app_router("rota", home=tmp_path, source_dir=tmp_path / "rota")
    (broken / "service.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(AppServiceError, match="no define create_router"):
        load_app_router("rota", home=tmp_path, source_dir=tmp_path / "rota")


def test_a_mounted_service_sits_behind_cores_http_protections(tmp_path: Path) -> None:
    import core.websocket_server as server

    before = list(server.app.router.routes)
    try:
        server.app.include_router(load_app_router("mi-servicio", home=tmp_path, source_dir=TEMPLATE), prefix="/api/apps/mi-servicio")
        client = TestClient(server.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))
        assert client.get("/api/apps/mi-servicio/items").status_code == 200
        hostile = client.get("/api/apps/mi-servicio/items", headers={"host": "evil.example"})
        assert hostile.status_code == 403
        assert hostile.json()["detail"] == "Untrusted HTTP Host"
    finally:
        server.app.router.routes[:] = before


def _app(root: Path, app_id: str, source: str) -> None:
    backend = root / app_id / "backend"
    backend.mkdir(parents=True)
    (backend / "service.py").write_text(source, encoding="utf-8")


GOOD = (
    "from fastapi import APIRouter\n"
    "def create_router(context):\n"
    "    router = APIRouter()\n"
    "    @router.get('/ping')\n"
    "    def ping():\n"
    "        return {'ok': True}\n"
    "    return router\n"
)


def test_a_failing_factory_stays_in_its_app_and_core_keeps_going(tmp_path: Path, monkeypatch) -> None:
    """Codex S1: create_router/operations raising an ordinary exception."""
    _app(tmp_path, "rota", "def create_router(context):\n    raise RuntimeError('sin recursos')\n")
    _app(tmp_path, "rota-ops", "def operations(context):\n    raise KeyError('x')\n")
    _app(tmp_path, "buena", GOOD)
    monkeypatch.setattr(app_services, "app_source_dirs", lambda app_id: [tmp_path / app_id])
    app = FastAPI()
    assert include_app_services(app, home=tmp_path, ids=["rota", "rota-ops", "buena"], operations=AppActionRegistry()) == ["buena"]
    assert TestClient(app).get("/api/apps/buena/ping").json() == {"ok": True}
    with pytest.raises(AppServiceError, match="falló al iniciarse"):
        load_app_router("rota", home=tmp_path, source_dir=tmp_path / "rota")


def test_operations_register_all_or_nothing(tmp_path: Path, monkeypatch) -> None:
    """Codex S2: the same operation twice inside one app leaves nothing behind."""
    twice = (
        "from core.app_actions import APP_SURFACE_ROUTE, AppOperation\n"
        "def operations(context):\n"
        "    op = AppOperation(app=context.app_id, action='leer', label='leer', capability='apps.actions',\n"
        "                      effect='read', validate_args=dict, project_data=dict, surface=APP_SURFACE_ROUTE)\n"
        "    return [op, op]\n"
    )
    _app(tmp_path, "doble", twice)
    _app(tmp_path, "buena", GOOD)
    monkeypatch.setattr(app_services, "app_source_dirs", lambda app_id: [tmp_path / app_id])
    registry = AppActionRegistry()
    assert include_app_services(FastAPI(), home=tmp_path, ids=["doble", "buena"], operations=registry) == ["buena"]
    assert registry.catalog() == [], "no half-registered app"


def test_a_router_that_cannot_be_mounted_takes_its_operations_back(tmp_path: Path, monkeypatch) -> None:
    both = GOOD + (
        "from core.app_actions import APP_SURFACE_ROUTE, AppOperation\n"
        "def operations(context):\n"
        "    return [AppOperation(app=context.app_id, action='leer', label='leer', capability='apps.actions',\n"
        "                         effect='read', validate_args=dict, project_data=dict, surface=APP_SURFACE_ROUTE)]\n"
    )
    _app(tmp_path, "mixta", both)
    monkeypatch.setattr(app_services, "app_source_dirs", lambda app_id: [tmp_path / app_id])

    class Refuses(FastAPI):
        def include_router(self, *args, **kwargs):
            raise RuntimeError("no se pudo montar")

    registry = AppActionRegistry()
    assert include_app_services(Refuses(), home=tmp_path, ids=["mixta"], operations=registry) == []
    assert registry.get("mixta", "leer") is None

    # Codex R05: what was registered before stays, the app's own earlier operations included.
    from core.app_actions import APP_SURFACE_ROUTE, AppOperation
    from core.player_actions import register_player_actions

    register_player_actions(registry)
    registry.register(AppOperation(app="mixta", action="previa", label="previa", capability="apps.actions",
                                   effect="read", validate_args=dict, project_data=dict, surface=APP_SURFACE_ROUTE))
    before = registry.catalog()
    assert include_app_services(Refuses(), home=tmp_path, ids=["mixta"], operations=registry) == []
    assert registry.catalog() == before
    assert registry.get("mixta", "previa") is not None and registry.get("mixta", "leer") is None


def test_a_standard_backend_with_dataclasses_imports(tmp_path: Path) -> None:
    """Codex S6: deferred annotations and @dataclass need the module in sys.modules."""
    import sys

    _app(tmp_path, "datos", (
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "from fastapi import APIRouter\n"
        "@dataclass\n"
        "class Item:\n"
        "    id: str\n"
        "    title: str\n"
        "def create_router(context):\n"
        "    router = APIRouter()\n"
        "    @router.get('/items')\n"
        "    def items():\n"
        "        return {'items': [Item('a', 'A').__dict__]}\n"
        "    return router\n"
    ))
    app = FastAPI()
    app.include_router(load_app_router("datos", home=tmp_path, source_dir=tmp_path / "datos"), prefix="/api/apps/datos")
    assert TestClient(app).get("/api/apps/datos/items").json() == {"items": [{"id": "a", "title": "A"}]}
    _app(tmp_path, "falla", "raise RuntimeError('al importar')\n")
    with pytest.raises(AppServiceError):
        load_app_router("falla", home=tmp_path, source_dir=tmp_path / "falla")
    assert "iris_app_service_falla" not in sys.modules, "a failed import leaves nothing behind"


def test_an_apps_data_file_must_be_its_own(tmp_path: Path) -> None:
    """Codex S5: a hardlink, a link or an oversized file is not the app's own data."""
    import os

    from core.app_services import AppDataError, AppServiceContext

    context = AppServiceContext(app_id="mi-servicio", home=tmp_path / "home")
    assert context.read_file("items.json") is None
    own = context.data_dir / "items.json"
    own.write_text('[{"id": "a", "title": "A"}]', encoding="utf-8")
    assert context.read_file("items.json") == b'[{"id": "a", "title": "A"}]'

    outside = tmp_path / "otra-carpeta.json"
    outside.write_text('[{"id": "x", "title": "Ajeno"}]', encoding="utf-8")
    own.unlink()
    os.link(outside, own)
    with pytest.raises(AppDataError, match="no es un archivo propio"):
        context.read_file("items.json")
    own.unlink()
    own.write_bytes(b"[" + b" " * 300_000 + b"]")
    with pytest.raises(AppDataError, match="tamaño"):
        context.read_file("items.json")
    for bad in ("../x.json", "sub/x.json", "..", ""):
        with pytest.raises(AppDataError):
            context.read_file(bad)


def test_the_template_never_presents_linked_data_as_its_own(tmp_path: Path) -> None:
    import os

    own = tmp_path / "app-data" / "mi-servicio"
    own.mkdir(parents=True)
    outside = tmp_path / "fuera.json"
    outside.write_text('[{"id": "x", "title": "Ajeno", "detail": "de otra carpeta"}]', encoding="utf-8")
    os.link(outside, own / "items.json")
    app = FastAPI()
    app.include_router(load_app_router("mi-servicio", home=tmp_path, source_dir=TEMPLATE), prefix="/api/apps/mi-servicio")
    response = TestClient(app).get("/api/apps/mi-servicio/items")
    assert response.status_code == 409
    assert "Ajeno" not in response.text


def test_the_service_template_renamed_as_its_readme_says_still_passes_its_tests(tmp_path: Path) -> None:
    """Codex R14: renaming app.json and ui/ is enough; the service tests follow app.json."""
    import shutil
    import subprocess
    import sys

    folder = tmp_path / "apps" / "lector-datos"
    shutil.copytree(TEMPLATE, folder, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # The name lives only in app.json and ui/: the service and its tests never need renaming.
    for path in [folder / "app.json", *(folder / "ui").iterdir()]:
        path.write_bytes(path.read_bytes().replace(b"mi-servicio", b"lector-datos"))
    assert not [path.name for path in folder.rglob("*") if path.is_file() and path.suffix != ".md" and b"mi-servicio" in path.read_bytes()]

    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")  # collect only inside the copy
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c", str(tmp_path / "pytest.ini"), str(folder / "tests")],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "3 passed" in run.stdout
