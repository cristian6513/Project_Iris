"""Python services and operations of IRIS OS apps (F09.3–F09.4).

An app that needs data, files or processes may bring ``apps/<id>/backend/service.py`` with
``create_router(context) -> APIRouter`` and/or ``operations(context) -> list[AppOperation]``,
the actions Hermes may ask for (only its own, under ``apps.actions``, running in Core or on the
IRIS OS surface; see core/app_actions.py). Core loads it only when its id is listed in
``REVIEWED_APP_SERVICES`` here, in reviewed code: never because an ``app.json``, a folder in
the profile or a request says so, and nothing is installed when an app opens.

A mounted service lives under ``/api/apps/<id>/``, behind the same HTTP protections as every
Core router (Host, Origin, pairing), and gets its own data folder in the profile,
``<IRIS_HOME>/app-data/<id>``, which Core resolves: a request never chooses a path. The service
runs in Core's process with Core's rights; reviewing it is what makes it trusted.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from fastapi import APIRouter, FastAPI

from core.app_actions import APP_SURFACE_ROUTE, AppActionRegistry, AppOperation
from core.profile_paths import ProfilePathError, _plain_directory, validate_profile_home

logger = logging.getLogger(__name__)

# Apps whose backend Core mounts. Adding an id here is the review step; none ships yet.
REVIEWED_APP_SERVICES: tuple[str, ...] = ()

_APP_ID = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
# The capability every community app operation answers to in Capability Center (F09.4).
APP_OPERATION_CAPABILITIES = frozenset({"apps.actions"})


class AppServiceError(RuntimeError):
    """An app service that cannot be found, loaded or mounted."""


class AppDataError(RuntimeError):
    """A file in an app's data folder that is not a plain file of its own, or is too large."""


_DATA_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def read_app_file(folder: Path, name: str, *, max_bytes: int) -> bytes | None:
    """The bytes of ``folder/name``, or None when it does not exist. Only a regular file with a
    single name counts: a link, a hardlink to data elsewhere or a swapped file is refused, so an
    app never presents someone else's data as its own (Codex S5)."""
    if not _DATA_NAME.fullmatch(name) or set(name) == {"."}:
        raise AppDataError("Nombre de archivo de datos no válido.")
    path = folder / name
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise AppDataError(f"{name} no es un archivo propio de la app.")
    if before.st_size > max_bytes:
        raise AppDataError(f"{name} supera el tamaño admitido.")
    with open(path, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (opened.st_ino, opened.st_dev, opened.st_nlink) != (before.st_ino, before.st_dev, 1):
            raise AppDataError(f"{name} cambió mientras se abría.")
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise AppDataError(f"{name} supera el tamaño admitido.")
    return data


@dataclass(frozen=True)
class AppServiceContext:
    """What an app service receives from Core."""

    app_id: str
    home: Path

    @property
    def data_dir(self) -> Path:
        """The app's own folder in the profile, created on first use."""
        return app_data_dir(self.app_id, self.home)

    def read_file(self, name: str, *, max_bytes: int = 256 * 1024) -> bytes | None:
        """Read one of the app's own data files safely (see ``read_app_file``)."""
        return read_app_file(self.data_dir, name, max_bytes=max_bytes)


def _checked_id(app_id: str) -> str:
    if not isinstance(app_id, str) or not _APP_ID.fullmatch(app_id):
        raise AppServiceError("Identificador de app no válido.")
    return app_id


def app_data_dir(app_id: str, home: Path) -> Path:
    """``<home>/app-data/<id>``: a plain folder inside the profile, never a link out of it."""
    home = validate_profile_home(home)
    folder = home / "app-data" / _checked_id(app_id)
    try:
        _plain_directory(folder, home)
        folder.mkdir(parents=True, exist_ok=True)
        _plain_directory(folder, home)
    except (OSError, ProfilePathError) as exc:
        raise AppServiceError("No se pudo preparar la carpeta de datos de la app.") from exc
    return folder


def app_source_dirs(app_id: str) -> list[Path]:
    """Where an app's reviewed sources may be: the checkout, then the installed runtime."""
    app_id = _checked_id(app_id)
    return [
        Path(__file__).resolve().parents[1] / "apps" / app_id,
        Path(sys.prefix) / "share" / "iris-os" / "apps" / app_id,
    ]


@dataclass(frozen=True)
class AppBackend:
    """What a reviewed app's ``backend/service.py`` brings: a router, operations, or both."""

    app_id: str
    router: APIRouter | None
    operations: tuple[AppOperation, ...]


def _checked_operations(app_id: str, operations: object) -> tuple[AppOperation, ...]:
    """An app registers only its own operations, under ``apps.actions``, running in Core or on
    the IRIS OS surface: it cannot borrow another app's name, capability or route."""
    if not isinstance(operations, (list, tuple)):
        raise AppServiceError(f"operations() de {app_id} debe devolver una lista.")
    checked = []
    for operation in operations:
        if not isinstance(operation, AppOperation):
            raise AppServiceError(f"operations() de {app_id} devolvió algo que no es AppOperation.")
        if operation.app != app_id:
            raise AppServiceError(f"{app_id} no puede registrar operaciones de {operation.app}.")
        if operation.capability not in APP_OPERATION_CAPABILITIES:
            raise AppServiceError(f"{app_id}.{operation.action} debe usar la capacidad apps.actions.")
        if operation.surface is not None and operation.surface != APP_SURFACE_ROUTE:
            raise AppServiceError(f"{app_id}.{operation.action} solo puede ejecutarse en Core o en IRIS OS.")
        checked.append(operation)
    return tuple(checked)


def load_app_backend(app_id: str, *, home: Path, source_dir: Path | None = None) -> AppBackend:
    """Import ``backend/service.py`` of a reviewed app: ``create_router(context)`` and/or
    ``operations(context)``."""
    app_id = _checked_id(app_id)
    candidates = [source_dir] if source_dir is not None else app_source_dirs(app_id)
    service = next((folder / "backend" / "service.py" for folder in candidates
                    if (folder / "backend" / "service.py").is_file()), None)
    if service is None:
        raise AppServiceError(f"La app {app_id} no tiene backend/service.py.")
    module_name = f"iris_app_service_{app_id.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, service)
    if spec is None or spec.loader is None:
        raise AppServiceError(f"No se pudo cargar el servicio de {app_id}.")
    module = importlib.util.module_from_spec(spec)
    # A normal import: the module is in sys.modules while it runs (dataclasses and deferred
    # annotations look it up there), and a failed import leaves nothing behind.
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # the service's own import errors stay inside this app
        sys.modules.pop(module_name, None)
        raise AppServiceError(f"El servicio de {app_id} falló al importarse.") from exc
    create_router = getattr(module, "create_router", None)
    make_operations = getattr(module, "operations", None)
    if not callable(create_router) and not callable(make_operations):
        raise AppServiceError(f"El servicio de {app_id} no define create_router(context) ni operations(context).")
    context = AppServiceContext(app_id=app_id, home=Path(home))
    router = None
    try:
        if callable(create_router):
            router = create_router(context)
        raw_operations = make_operations(context) if callable(make_operations) else ()
    except Exception as exc:  # a failing factory stays inside this app, too
        raise AppServiceError(f"El servicio de {app_id} falló al iniciarse.") from exc
    if router is not None and not isinstance(router, APIRouter):
        raise AppServiceError(f"create_router de {app_id} no devolvió un APIRouter.")
    operations = _checked_operations(app_id, raw_operations) if callable(make_operations) else ()
    return AppBackend(app_id=app_id, router=router, operations=operations)


def load_app_router(app_id: str, *, home: Path, source_dir: Path | None = None) -> APIRouter:
    """The router of a reviewed app's service."""
    backend = load_app_backend(app_id, home=home, source_dir=source_dir)
    if backend.router is None:
        raise AppServiceError(f"El servicio de {app_id} no define create_router(context).")
    return backend.router


def include_app_services(app: FastAPI, *, home: Path | None = None,
                         ids: Iterable[str] = REVIEWED_APP_SERVICES,
                         operations: AppActionRegistry | None = None) -> list[str]:
    """Mount each reviewed app service under ``/api/apps/<id>`` and register its operations;
    one failure stays local to its app."""
    ids = list(ids)
    if not ids:
        return []
    if home is None:
        from core.config import get_iris_home

        home = get_iris_home()
    mounted = []
    for app_id in ids:
        added: tuple[tuple[str, str], ...] = ()
        try:
            backend = load_app_backend(app_id, home=home)
            if backend.operations and operations is None:
                raise AppServiceError(f"{app_id} trae operaciones y no hay registro de acciones.")
            # All of the app's operations or none: a duplicate, inside the app or with what is
            # registered, leaves the app out entirely.
            if operations is not None and backend.operations:
                try:
                    added = operations.register_all(backend.operations)
                except ValueError as exc:
                    raise AppServiceError(str(exc)) from exc
            if backend.router is not None:
                app.include_router(backend.router, prefix=f"/api/apps/{app_id}")
        except Exception as exc:  # Core must start whatever one app does
            if added:
                # Only what this attempt added goes: operations the app had before stay (Codex R05).
                operations.unregister(added)
            logger.warning("Servicio de app no montado (%s): %s", app_id, exc)
            continue
        mounted.append(app_id)
    return mounted
