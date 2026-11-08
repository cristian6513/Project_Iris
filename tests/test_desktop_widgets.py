"""F12a preferences: isolated profiles, real CAS and the existing SQLite boundary."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.desktop_widgets import DESKTOP_SPEC, DesktopStore, DesktopStoreError, create_desktop_widgets_router
from core.profile_sqlite import profile_store_path


def widget(n=1):
    return {'instance_id': 'wdg_' + f'{n:032x}', 'widget_id': 'calendar.month', 'config_version': 1,
            'size': 'compact', 'calendars': []}


def client(home, peer='127.0.0.1'):
    app = FastAPI()
    app.include_router(create_desktop_widgets_router(lambda: home))
    return TestClient(app, client=(peer, 1234))


def test_default_read_has_no_profile_writes(tmp_path):
    home = tmp_path / 'profile'; home.mkdir()
    response = client(home).get('/api/desktop/widgets')
    assert response.status_code == 200
    assert response.json() == {'schema': 'iris.desktop-widgets/v1', 'revision': 0, 'side': 'right', 'widgets': []}
    assert not (home / 'apps').exists()
    assert 'no-store' in response.headers['cache-control']


def test_player_widget_is_profile_preference_only_not_a_music_folder_grant(tmp_path):
    home = tmp_path / 'profile'; home.mkdir(); api = client(home)
    initial = api.get('/api/desktop/widgets').json()
    item = {**widget(), 'widget_id': 'player.transport'}
    response = api.put('/api/desktop/widgets', json={**initial, 'widgets': [item]})
    assert response.status_code == 200
    assert client(home).get('/api/desktop/widgets').json()['widgets'] == [item]
    assert not (home / 'player').exists()
    assert not (home / 'hermes').exists()
    # A manifest/preference cannot supply a service reference or folder path.
    assert api.put('/api/desktop/widgets', json={**response.json(), 'widgets': [{**item, 'services': {'player': 'folder'}}]}).status_code == 422


def test_add_reorder_remove_restart_and_profile_isolation(tmp_path):
    home = tmp_path / 'profile'; home.mkdir(); api = client(home)
    initial = api.get('/api/desktop/widgets').json()
    a = api.put('/api/desktop/widgets', json={**initial, 'widgets': [widget(), widget(2)]}).json()
    b = api.put('/api/desktop/widgets', json={**a, 'side': 'left', 'widgets': a['widgets'][::-1]}).json()
    assert b['revision'] == 2 and b['widgets'][0] == widget(2)
    assert client(home).get('/api/desktop/widgets').json() == b
    assert api.put('/api/desktop/widgets', json=a).status_code == 409
    c = api.put('/api/desktop/widgets', json={**b, 'widgets': [widget()]}).json()
    assert c['revision'] == 3
    other = tmp_path / 'other'; other.mkdir()
    assert client(other).get('/api/desktop/widgets').json() == initial


@pytest.mark.parametrize('change', [
    {'schema': 'iris.desktop-widgets/v9'}, {'revision': True}, {'revision': -1}, {'side': '../../outside'},
    {'widgets': [widget(), widget()]}, {'widgets': [widget(i) for i in range(5)]},
    {'widgets': [{**widget(), 'widget_id': 'unknown.widget'}]},
    {'widgets': [{**widget(), 'config_version': True}]}, {'widgets': [{**widget(), 'config_version': 9}]},
    {'widgets': [{**widget(), 'size': 'huge'}]}, {'widgets': [{**widget(), 'entry': 'https://bad.invalid/a.js'}]},
    {'widgets': [{**widget(), 'calendars': ['remote-id']}]}, {'extra': True},
])
def test_invalid_preferences_do_not_create_or_reset_anything(tmp_path, change):
    home = tmp_path / 'profile'; home.mkdir(); api = client(home)
    initial = api.get('/api/desktop/widgets').json()
    assert api.put('/api/desktop/widgets', json={**initial, **change}).status_code == 422
    assert not (home / 'apps').exists()


def test_stale_first_save_never_creates_store(tmp_path):
    home = tmp_path / 'profile'; home.mkdir(); api = client(home)
    layout = api.get('/api/desktop/widgets').json()
    assert api.put('/api/desktop/widgets', json={**layout, 'revision': 3}).status_code == 409
    assert not (home / 'apps').exists()


@pytest.mark.parametrize('method', ['get', 'put'])
def test_lan_refusal_applies_to_reads_and_writes(tmp_path, method):
    home = tmp_path / 'profile'; home.mkdir(); api = client(home, '192.168.1.10')
    response = getattr(api, method)('/api/desktop/widgets', **({'json': {}} if method == 'put' else {}))
    assert response.status_code == 403 and 'no-store' in response.headers['cache-control']
    assert list(home.iterdir()) == []


def test_corrupt_or_future_config_is_not_replaced_by_defaults(tmp_path):
    home = tmp_path / 'profile'; home.mkdir(); path = profile_store_path(DESKTOP_SPEC, home)
    store = DesktopStore(path, root=home)
    with store._transaction() as conn:
        conn.execute("INSERT INTO meta VALUES ('layout', ?)", (json.dumps({'schema': 'future/v2'}),))
    before = path.read_bytes()
    api = client(home)
    assert api.get('/api/desktop/widgets').status_code == 503
    assert api.put('/api/desktop/widgets', json={'schema': 'iris.desktop-widgets/v1', 'revision': 0, 'side': 'right', 'widgets': []}).status_code == 503
    assert path.read_bytes() == before


def test_removed_apps_are_readable_removable_but_cannot_be_reintroduced(tmp_path):
    home = tmp_path / 'profile'; home.mkdir(); path = profile_store_path(DESKTOP_SPEC, home)
    store = DesktopStore(path, root=home)
    unavailable = {**widget(), 'widget_id': 'removed.summary', 'config_version': 2}
    layout = {'schema': 'iris.desktop-widgets/v1', 'revision': 5, 'side': 'right', 'widgets': [unavailable]}
    with store._transaction() as conn:
        conn.execute("INSERT INTO meta VALUES ('layout', ?)", (json.dumps(layout),))
    api = client(home)
    assert api.get('/api/desktop/widgets').json() == layout
    removed = api.put('/api/desktop/widgets', json={**layout, 'widgets': []})
    assert removed.status_code == 200
    assert api.put('/api/desktop/widgets', json={**layout, 'revision': 6}).status_code == 422


def test_broken_sqlite_is_reported_without_replacement(tmp_path):
    home = tmp_path / 'profile'; home.mkdir(); path = profile_store_path(DESKTOP_SPEC, home)
    path.parent.mkdir(parents=True); payload = b'not a SQLite database'
    path.write_bytes(payload)
    response = client(home).get('/api/desktop/widgets')
    assert response.status_code == 503 and path.read_bytes() == payload


def test_two_windows_cannot_overwrite_the_same_revision(tmp_path):
    home = tmp_path / 'profile'; home.mkdir()
    api = client(home); initial = api.get('/api/desktop/widgets').json()
    api.put('/api/desktop/widgets', json=initial)
    layout = api.get('/api/desktop/widgets').json()
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda n: client(home).put('/api/desktop/widgets', json={**layout, 'widgets': [widget(n)]}).status_code, [1, 2]))
    assert sorted(results) == [200, 409]


def test_existing_profile_boundary_is_not_followed(tmp_path):
    import os
    import subprocess
    home = tmp_path / 'profile'; home.mkdir(); outside = tmp_path / 'outside'; outside.mkdir()
    (home / 'apps').mkdir()
    target = home / 'apps' / 'desktop'
    if os.name == 'nt':
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(target), str(outside)], capture_output=True)
        if result.returncode: pytest.skip('Junction unavailable in this sandbox')
    else:
        target.symlink_to(outside, target_is_directory=True)
    try:
        assert client(home).get('/api/desktop/widgets').status_code == 503
        assert list(outside.iterdir()) == []
    finally:
        if os.name == 'nt': target.rmdir()
        else: target.unlink()
