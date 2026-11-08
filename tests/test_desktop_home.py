"""Profile home: empty is intentional, widget clients and other profiles cannot reset it."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.desktop_widgets import DESKTOP_SPEC, DesktopStore
from core.profile_sqlite import profile_store_path
from tests.test_desktop_widgets import client, widget

URL = '/api/desktop/home'


def test_default_home_read_is_passive_and_ids_come_from_bundled_catalog(tmp_path):
    api = client(tmp_path)
    reply = api.get(URL)
    assert reply.status_code == 200
    assert reply.json()['shortcuts'] == ['theme', 'camera', 'player', 'settings']
    assert reply.json()['hidden_apps'] == []
    assert not (tmp_path / 'apps').exists()
    assert 'no-store' in reply.headers['cache-control']


def test_empty_home_survives_old_widget_write_restart_and_profile_change(tmp_path):
    home = tmp_path / 'one'; home.mkdir(); api = client(home)
    current = api.get(URL).json()
    saved = api.put(URL, json={**current, 'shortcuts': [], 'hidden_apps': ['calendar', 'mail'], 'order': []})
    assert saved.status_code == 200
    legacy = api.get('/api/desktop/widgets').json()
    assert api.put('/api/desktop/widgets', json={**legacy, 'widgets': [widget()]}).status_code == 200
    assert client(home).get(URL).json() == saved.json()
    assert client(home).get('/api/desktop/widgets').json()['widgets'] == [widget()]
    assert client(tmp_path).get(URL).json() == current


def test_home_mixed_order_and_hidden_app_keep_shortcut_and_widget(tmp_path):
    api = client(tmp_path); initial = api.get(URL).json()
    widgets = api.get('/api/desktop/widgets').json()
    api.put('/api/desktop/widgets', json={**widgets, 'widgets': [widget()]})
    order = ['app:calendar', f"widget:{widget()['instance_id']}", 'app:player']
    saved = api.put(URL, json={**initial, 'shortcuts': ['calendar', 'player'], 'hidden_apps': ['calendar'], 'order': order})
    assert saved.status_code == 200
    assert saved.json()['order'] == order
    assert client(tmp_path).get(URL).json()['shortcuts'] == ['calendar', 'player']
    assert len(api.get('/api/desktop/widgets').json()['widgets']) == 1


@pytest.mark.parametrize('change', [
    {'shortcuts': ['calendar', 'calendar']}, {'shortcuts': ['../../outside']},
    {'shortcuts': ['external-uninstalled']}, {'hidden_apps': ['external-uninstalled']},
    {'hidden_apps': ['mail', 'mail']}, {'revision': True}, {'schema': 'future/v9'},
    {'official': True}, {'disabled_apps': ['mail']}, {'uninstall': 'calendar'},
    {'order': ['app:mail']}, {'order': ['script:run']},
])
def test_invalid_home_never_creates_store(tmp_path, change):
    api = client(tmp_path); initial = api.get(URL).json()
    assert api.put(URL, json={**initial, **change}).status_code == 422
    assert not (tmp_path / 'apps').exists()


def test_stale_first_home_save_cannot_create_store(tmp_path):
    api = client(tmp_path); initial = api.get(URL).json()
    assert api.put(URL, json={**initial, 'revision': 3}).status_code == 409
    assert not (tmp_path / 'apps').exists()


@pytest.mark.parametrize('field', ['schema', 'revision', 'shortcuts', 'hidden_apps', 'order'])
def test_incomplete_put_does_not_restore_or_erase_saved_home(tmp_path, field):
    api = client(tmp_path); initial = api.get(URL).json()
    saved = api.put(URL, json={**initial, 'shortcuts': [], 'hidden_apps': ['mail']}).json()
    incomplete = {key: value for key, value in saved.items() if key != field}
    assert api.put(URL, json=incomplete).status_code == 422
    assert api.get(URL).json() == saved


@pytest.mark.parametrize('field', ['schema', 'revision', 'shortcuts', 'hidden_apps', 'order'])
def test_incomplete_stored_home_is_an_error_never_factory_defaults(tmp_path, field):
    initial = client(tmp_path).get(URL).json()
    path = profile_store_path(DESKTOP_SPEC, tmp_path)
    store = DesktopStore(path, root=tmp_path)
    with store._transaction() as conn:
        conn.execute("INSERT INTO meta VALUES ('home', ?)", (json.dumps({key: value for key, value in initial.items() if key != field}),))
    before = path.read_bytes()
    assert client(tmp_path).get(URL).status_code == 503
    assert client(tmp_path).put(URL, json=initial).status_code == 503
    assert path.read_bytes() == before


def test_parallel_home_edits_are_not_last_writer_wins(tmp_path):
    api = client(tmp_path); initial = api.get(URL).json()
    api.put(URL, json=initial); current = api.get(URL).json()
    with ThreadPoolExecutor(2) as pool:
        statuses = list(pool.map(lambda name: client(tmp_path).put(URL, json={**current, 'hidden_apps': [name]}).status_code, ['mail', 'calendar']))
    assert sorted(statuses) == [200, 409]


def test_future_or_corrupt_home_is_not_reset_by_save(tmp_path):
    path = profile_store_path(DESKTOP_SPEC, tmp_path)
    store = DesktopStore(path, root=tmp_path)
    with store._transaction() as conn:
        conn.execute("INSERT INTO meta VALUES ('home', ?)", (json.dumps({'schema': 'future/v4'}),))
    before = path.read_bytes()
    api = client(tmp_path)
    assert api.get(URL).status_code == 503
    assert api.put(URL, json={'schema': 'iris.desktop-home/v1', 'revision': 0, 'shortcuts': [], 'hidden_apps': [], 'order': []}).status_code == 503
    assert path.read_bytes() == before


def test_removed_app_can_be_removed_but_not_added_again(tmp_path):
    path = profile_store_path(DESKTOP_SPEC, tmp_path)
    store = DesktopStore(path, root=tmp_path)
    data = {'schema': 'iris.desktop-home/v1', 'revision': 2, 'shortcuts': ['removed'], 'hidden_apps': [], 'order': ['app:removed']}
    with store._transaction() as conn:
        conn.execute("INSERT INTO meta VALUES ('home', ?)", (json.dumps(data),))
    api = client(tmp_path)
    assert api.get(URL).json() == data
    assert api.put(URL, json={**data, 'shortcuts': [], 'order': []}).status_code == 200
    assert api.put(URL, json={**data, 'revision': 3}).status_code == 422


@pytest.mark.parametrize('method', ['get', 'put'])
def test_home_cannot_be_changed_or_read_from_lan(tmp_path, method):
    reply = getattr(client(tmp_path, '192.168.0.2'), method)(URL, **({'json': {}} if method == 'put' else {}))
    assert reply.status_code == 403
    assert list(tmp_path.iterdir()) == []
