"""F12 profile-scoped layout. Preferences never grant account/tool permissions."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from core.calendar_api import _PrivateRoute
from core.profile_sqlite import ProfileSqliteStore, StoreSpec, profile_store_path
from core.request_security import is_loopback_host
from core.widget_catalog import WIDGETS, APP_IDS


class DesktopStoreError(RuntimeError):
    pass


class DesktopRevisionConflict(DesktopStoreError):
    pass


class DesktopWidgetRequestError(ValueError):
    pass


class WidgetInstance(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    instance_id: StrictStr = Field(pattern=r'^wdg_[0-9a-f]{32}$')
    widget_id: StrictStr = Field(pattern=r'^[a-z][a-z0-9-]{1,31}\.[a-z][a-z0-9-]{0,31}$')
    config_version: StrictInt = Field(default=1, ge=1, le=1_000_000)
    size: StrictStr = Field(default='compact', pattern=r'^[a-z][a-z0-9-]{0,23}$')
    calendars: list[StrictStr] = Field(default_factory=list, max_length=50)

    @model_validator(mode='after')
    def valid_calendars(self):
        import re
        if len(set(self.calendars)) != len(self.calendars) or any(
                not re.fullmatch(r'cal_[0-9a-f]{32}', key) for key in self.calendars):
            raise ValueError('Calendarios del widget inválidos.')
        return self


class DesktopLayout(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_id: Literal['iris.desktop-widgets/v1'] = Field(default='iris.desktop-widgets/v1', alias='schema')
    revision: StrictInt = Field(default=0, ge=0, le=1_000_000_000)
    side: Literal['left', 'right'] = 'right'
    widgets: list[WidgetInstance] = Field(default_factory=list, max_length=4)

    @model_validator(mode='after')
    def unique_instances(self):
        if len({item.instance_id for item in self.widgets}) != len(self.widgets):
            raise ValueError('El widget ya está en el escritorio.')
        return self


class DesktopHome(BaseModel):
    """Separate preference key: old widget clients cannot erase a customized home."""
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_id: Literal['iris.desktop-home/v1'] = Field(alias='schema')
    revision: StrictInt = Field(ge=0, le=1_000_000_000)
    shortcuts: list[StrictStr] = Field(max_length=64)
    hidden_apps: list[StrictStr] = Field(max_length=64)
    order: list[StrictStr] = Field(max_length=128)

    @model_validator(mode='after')
    def valid_home(self):
        import re
        for values in (self.shortcuts, self.hidden_apps):
            if len(set(values)) != len(values) or any(not re.fullmatch(r'[a-z][a-z0-9-]{1,31}', item) for item in values):
                raise ValueError('Aplicaciones del inicio inválidas.')
        if len(set(self.order)) != len(self.order) or any(
                not re.fullmatch(r'(?:app:[a-z][a-z0-9-]{1,31}|widget:wdg_[0-9a-f]{32})', item) for item in self.order):
            raise ValueError('Orden del inicio inválido.')
        return self


def default_home():
    # Only a genuinely absent preference row receives factory shortcuts.
    return DesktopHome(schema='iris.desktop-home/v1', revision=0,
                       shortcuts=['theme', 'camera', 'player', 'settings'], hidden_apps=[], order=[])


DESKTOP_SPEC = StoreSpec(
    folder=('apps', 'desktop'), filename='widgets.sqlite3', lock_name='widgets.lock',
    noun='almacén del escritorio', schema=('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)',),
    schema_version=1, store_name='iris.desktop-widgets-store/v1',
    path_error=DesktopStoreError, version_error=DesktopStoreError, busy_error=DesktopStoreError,
)


class DesktopStore(ProfileSqliteStore):
    SPEC = DESKTOP_SPEC

    @staticmethod
    def layout(conn):
        row = conn.execute("SELECT value FROM meta WHERE key='layout'").fetchone()
        return DesktopLayout.model_validate(json.loads(row[0])) if row else DesktopLayout()

    def read(self):
        with self._read() as conn:
            return self.layout(conn)

    @staticmethod
    def home_layout(conn):
        row = conn.execute("SELECT value FROM meta WHERE key='home'").fetchone()
        return DesktopHome.model_validate(json.loads(row[0])) if row else default_home()

    def read_home(self):
        with self._read() as conn:
            return self.home_layout(conn)

    def save_home(self, candidate: DesktopHome, known=APP_IDS):
        with self._transaction() as conn:
            current = self.home_layout(conn)
            if candidate.revision != current.revision:
                raise DesktopRevisionConflict('El inicio cambió en otra ventana. Vuelve a cargarlo.')
            validate_home(candidate, current, known)
            updated = DesktopHome.model_validate({**candidate.model_dump(by_alias=True), 'revision': current.revision + 1})
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('home', ?)", (updated.model_dump_json(by_alias=True),))
            return updated

    def save(self, candidate: DesktopLayout, catalog=WIDGETS):
        with self._transaction() as conn:
            current = self.layout(conn)
            if candidate.revision != current.revision:
                raise DesktopRevisionConflict('El escritorio cambió en otra ventana. Vuelve a cargarlo.')
            validate_widgets(candidate, current, catalog)
            updated = DesktopLayout.model_validate({**candidate.model_dump(by_alias=True), 'revision': current.revision + 1})
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('layout', ?)", (updated.model_dump_json(by_alias=True),))
            return updated

    def forget_app(self, app_id: str) -> None:
        """F17 uninstall: drop the app's widget instances and home entries in one transaction."""
        with self._transaction() as conn:
            layout = self.layout(conn)
            kept = [item for item in layout.widgets if not item.widget_id.startswith(f'{app_id}.')]
            gone = {f'widget:{item.instance_id}' for item in layout.widgets} - {f'widget:{item.instance_id}' for item in kept}
            if len(kept) != len(layout.widgets):
                layout = DesktopLayout.model_validate({**layout.model_dump(by_alias=True), 'widgets': [item.model_dump() for item in kept],
                                                       'revision': layout.revision + 1})
                conn.execute("INSERT OR REPLACE INTO meta VALUES ('layout', ?)", (layout.model_dump_json(by_alias=True),))
            home = self.home_layout(conn)
            data = home.model_dump(by_alias=True)
            cleaned = {**data, 'shortcuts': [key for key in home.shortcuts if key != app_id],
                       'hidden_apps': [key for key in home.hidden_apps if key != app_id],
                       'order': [key for key in home.order if key != f'app:{app_id}' and key not in gone]}
            if cleaned != data:
                updated = DesktopHome.model_validate({**cleaned, 'revision': home.revision + 1})
                conn.execute("INSERT OR REPLACE INTO meta VALUES ('home', ?)", (updated.model_dump_json(by_alias=True),))


def forget_app(home: Path, app_id: str) -> bool:
    """Clean the desktop of an uninstalled app; True when there was nothing left or it is clean."""
    path = profile_store_path(DESKTOP_SPEC, Path(home))
    if path.exists():
        DesktopStore(path, root=Path(home)).forget_app(app_id)
    return True


def validate_widgets(candidate: DesktopLayout, current: DesktopLayout, catalog=WIDGETS):
    previous = {item.instance_id: item.model_dump() for item in current.widgets}
    for item in candidate.widgets:
        definition = catalog.get(item.widget_id)
        compatible = definition is not None and item.config_version == definition['configVersion'] and item.size == definition['size']
        # An unavailable saved widget stays removable/reorderable, never loadable.
        if not compatible and previous.get(item.instance_id) != item.model_dump():
            raise DesktopWidgetRequestError('Widget no disponible o configuración incompatible.')


def validate_home(candidate: DesktopHome, current: DesktopHome, known=APP_IDS):
    # Unavailable saved IDs remain removable; no new arbitrary app becomes runnable.
    known = set(known)
    for name in ('shortcuts', 'hidden_apps'):
        if set(getattr(candidate, name)) - known - set(getattr(current, name)):
            raise DesktopWidgetRequestError('Aplicación no disponible en esta versión.')
    if any(item.startswith('app:') and item[4:] not in candidate.shortcuts for item in candidate.order):
        raise DesktopWidgetRequestError('El orden contiene un acceso que no está en el inicio.')


def create_desktop_widgets_router(home: Callable[[], Path], installed: Callable[[], tuple[dict, set]] = lambda: ({}, set())):
    """`installed()` adds the widgets and app IDs of apps installed from the Store (F17)."""
    def catalogs():
        try:
            widgets, app_ids = installed()
        except Exception:  # an unreadable package store must not block the bundled apps' desktop
            widgets, app_ids = {}, set()
        return {**WIDGETS, **widgets}, set(APP_IDS) | set(app_ids)

    def local(request: Request):
        if not request.client or not is_loopback_host(request.client.host):
            raise HTTPException(403, 'El escritorio solo se configura desde este equipo.')

    router = APIRouter(prefix='/api/desktop', dependencies=[Depends(local)], route_class=_PrivateRoute)

    def store(create=False):
        root = Path(home())
        path = profile_store_path(DESKTOP_SPEC, root)
        return DesktopStore(path, root=root) if create or path.exists() else None

    def error(exc):
        conflict = isinstance(exc, DesktopRevisionConflict)
        invalid = isinstance(exc, DesktopWidgetRequestError)
        return JSONResponse({'error': {'code': 'revision_conflict' if conflict else 'invalid_widget' if invalid else 'desktop_unavailable',
                             'message': str(exc) if conflict or invalid else 'No se pudo leer o guardar el escritorio. No se restablecieron tus preferencias.'}},
                            status_code=409 if conflict else 422 if invalid else 503)

    @router.get('/widgets')
    def read():
        try:
            current = store()
            return JSONResponse((current.read() if current else DesktopLayout()).model_dump(by_alias=True))
        except (DesktopStoreError, ValueError, OSError, sqlite3.Error) as exc:
            return error(exc)

    @router.put('/widgets')
    def save(body: DesktopLayout):
        try:
            # Reject a stale first write before creating even the preference folder.
            current = store()
            if current is None and body.revision != 0:
                raise DesktopRevisionConflict('El escritorio cambió. Vuelve a cargarlo.')
            widgets, _ = catalogs()
            if current is None:
                validate_widgets(body, DesktopLayout(), widgets)
            return JSONResponse((current or store(create=True)).save(body, widgets).model_dump(by_alias=True))
        except (DesktopStoreError, ValueError, OSError, sqlite3.Error) as exc:
            return error(exc)

    @router.get('/home')
    def read_home():
        try:
            current = store()
            return JSONResponse((current.read_home() if current else default_home()).model_dump(by_alias=True))
        except (DesktopStoreError, ValueError, OSError, sqlite3.Error) as exc:
            return error(exc)

    @router.put('/home')
    def save_home(body: DesktopHome):
        try:
            current = store()
            _, known = catalogs()
            if current is None:
                if body.revision != 0:
                    raise DesktopRevisionConflict('El inicio cambió. Vuelve a cargarlo.')
                validate_home(body, default_home(), known)
            return JSONResponse((current or store(create=True)).save_home(body, known).model_dump(by_alias=True))
        except (DesktopStoreError, ValueError, OSError, sqlite3.Error) as exc:
            return error(exc)

    return router
