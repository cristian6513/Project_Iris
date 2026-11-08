"""F17 Iris Store: installing apps from a file. Synthetic packages and throwaway profiles only."""
import base64
import io
import json
import stat
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from core.app_packages import (MAX_ENTRIES, MAX_UI_FILE_BYTES, PACKAGE_SPEC, AppPackageStore, PackageError, check_package,
                               create_app_packages_router, installed_catalog, read_folder, read_zip)
from core.desktop_widgets import create_desktop_widgets_router, forget_app
from core.profile_sqlite import profile_store_path
from core.widget_catalog import APP_IDS, ICON_NAMES

ORIGIN = {'origin': 'http://127.0.0.1'}
MANIFEST = {
    'schema': 'iris.app-manifest/v1', 'sdkVersion': 1, 'id': 'demo', 'name': 'Demo', 'version': '0.1.0',
    'description': 'App de prueba', 'icon': 'sparkle', 'accent': 'magenta', 'presentation': 'window',
    'window': {'width': 420, 'height': 520}, 'launcher': {'visible': True, 'order': 910},
    'ui': {'entry': 'main.js', 'style': 'style.css'},
    'widgets': [{'id': 'demo.timer', 'name': 'Demo', 'entry': 'widget.js', 'style': 'widget.css', 'size': 'compact', 'configVersion': 1}],
}
MAIN = "import { tick } from './timer.js';\nexport function createApp(context) { return { mount(root) { root.textContent = tick(); } }; }\n"


def files(manifest=None, **extra):
    data = {'app.json': json.dumps(manifest or MANIFEST).encode(), 'ui/main.js': MAIN.encode(),
            'ui/timer.js': b"export const tick = () => '25:00';\n", 'ui/style.css': b'.demo-root { color: red; }\n',
            'ui/widget.js': b'export function createWidget({ root }) { return { setVisibility() {}, configure() {}, dispose() {} }; }\n',
            'ui/widget.css': b'.demo-widget-root { padding: 4px; }\n', 'README.md': b'# Demo\n', 'tests/main.test.mjs': b'// test\n'}
    data.update(extra)
    return {name: value for name, value in data.items() if value is not None}


def make_zip(entries, folder='demo/'):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(folder + name, data)
    return buffer.getvalue()


def app(home):
    server = FastAPI()
    server.include_router(create_desktop_widgets_router(lambda: home, installed=lambda: installed_catalog(home)))
    server.include_router(create_app_packages_router(lambda: home, bundled_ids=APP_IDS, icon_names=ICON_NAMES, forget_app=forget_app))
    return server


def client(home, peer='127.0.0.1'):
    return TestClient(app(home), base_url='http://127.0.0.1', client=(peer, 12345))


def install(api, archive):
    review = api.post('/api/app-packages/inspect', content=archive, headers={**ORIGIN, 'content-type': 'application/zip'})
    assert review.status_code == 200, review.text
    done = api.post('/api/app-packages/install', json={'token': review.json()['token']}, headers=ORIGIN)
    assert done.status_code == 200, done.text
    return review.json(), done.json()['installed']


def test_reading_is_passive_then_a_zip_is_reviewed_installed_served_and_uninstalled(tmp_path):
    api = client(tmp_path)
    assert api.get('/api/app-packages').json() == {'apps': []}
    assert list(tmp_path.iterdir()) == [], 'reading the list creates nothing in the profile'
    review, installed = install(api, make_zip(files()))
    assert review['app'] | {'risks': [], 'current': None} == review['app']
    assert review['app']['ignored'] == ['README.md', 'tests/main.test.mjs'] and review['app']['widgets'] == [{'id': 'demo.timer', 'name': 'Demo'}]
    assert installed['entry'].startswith('/api/app-packages/files/ins_') and installed['entry'].endswith('/main.js')
    assert set(installed['widgets']) == {'demo.timer'} and installed['style'].endswith('/style.css.js')
    [listed] = client(tmp_path).get('/api/app-packages').json()['apps']
    assert listed['manifest']['id'] == 'demo' and listed['version'] == '0.1.0'
    main = api.get(installed['entry'])
    assert main.text == MAIN and main.headers['content-type'].startswith('text/javascript')
    assert main.headers['x-content-type-options'] == 'nosniff'
    timer = api.get(installed['entry'].replace('main.js', 'timer.js'))
    assert timer.status_code == 200, 'relative imports of the app resolve inside its installation'
    assert api.get(installed['style']).text == 'export default ".demo-root { color: red; }\\n";\n'
    assert api.get(installed['entry'].replace('main.js', 'README.md')).status_code == 404, 'documentation is not served'
    gone = api.delete('/api/app-packages/demo', headers=ORIGIN)
    assert gone.status_code == 200 and gone.json()['removed'] == {'id': 'demo', 'version': '0.1.0'}
    assert api.get('/api/app-packages').json() == {'apps': []}
    assert api.get(installed['entry']).status_code == 404
    assert api.delete('/api/app-packages/demo', headers=ORIGIN).status_code == 404


def test_builder_prepares_an_app_and_its_update_for_store_review(tmp_path):
    from core.app_builder import create_draft, pack_draft

    draft = create_draft(tmp_path)
    app_id = draft["id"]
    manifest_path = Path(draft["source"]) / "app.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["name"] = "Sesiones de estudio"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    first = pack_draft(tmp_path, app_id)
    api = client(tmp_path)
    assert api.get('/api/app-packages').json() == {'apps': []}
    review, installed = install(api, Path(first["package"]).read_bytes())
    assert review["app"]["name"] == manifest["name"]
    assert set(installed["widgets"]) == {f"{app_id}.resumen"}

    manifest["version"] = "0.2.0"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    update = pack_draft(tmp_path, app_id)
    assert update["package"] != first["package"]
    assert api.get('/api/app-packages').json()["apps"][0]["version"] == "0.1.0"
    _, updated = install(api, Path(update["package"]).read_bytes())
    assert updated["manifest"]["id"] == app_id
    assert updated["version"] == "0.2.0"
    assert Path(first["package"]).is_file()


def test_a_folder_sent_by_the_store_installs_exactly_like_its_zip(tmp_path):
    payload = {'files': [{'path': f'demo/{name}', 'data': base64.b64encode(data).decode()} for name, data in files().items()]}
    payload['files'].append({'path': 'demo/.git/config', 'data': base64.b64encode(b'x').decode()})
    from_folder = check_package(*read_folder(payload), bundled_ids=APP_IDS, icon_names=ICON_NAMES)
    from_zip = check_package(*read_zip(make_zip(files())), bundled_ids=APP_IDS, icon_names=ICON_NAMES)
    assert from_folder['digest'] == from_zip['digest'] and from_folder['ignored'] == from_zip['ignored']
    api = client(tmp_path)
    review = api.post('/api/app-packages/inspect', json=payload, headers=ORIGIN)
    assert review.status_code == 200 and review.json()['app']['id'] == 'demo'


def test_a_token_installs_once_and_a_newer_review_wins_over_a_stale_one(tmp_path):
    api = client(tmp_path)
    install(api, make_zip(files()))
    newer = {**MANIFEST, 'version': '0.2.0'}
    stale = api.post('/api/app-packages/inspect', content=make_zip(files(newer)), headers={**ORIGIN, 'content-type': 'application/zip'}).json()
    assert stale['app']['current'] == {'version': '0.1.0'}
    review, installed = install(api, make_zip(files({**MANIFEST, 'version': '0.3.0'})))
    late = api.post('/api/app-packages/install', json={'token': stale['token']}, headers=ORIGIN)
    assert late.status_code == 409 and late.json()['error']['code'] == 'changed'
    assert api.get('/api/app-packages').json()['apps'][0]['version'] == '0.3.0'
    again = api.post('/api/app-packages/install', json={'token': review['token']}, headers=ORIGIN)
    assert again.status_code == 409 and again.json()['error']['code'] == 'expired', 'a review installs once'


def test_an_update_replaces_every_file_of_the_previous_installation(tmp_path):
    api = client(tmp_path)
    _, first = install(api, make_zip(files()))
    _, second = install(api, make_zip(files({**MANIFEST, 'version': '0.2.0'}, **{'ui/timer.js': b"export const tick = () => '05:00';\n"})))
    assert first['entry'] != second['entry']
    assert api.get(first['entry']).status_code == 404
    assert "'05:00'" in api.get(second['entry'].replace('main.js', 'timer.js')).text


def zinfo(name, *, mode=None, flags=0):
    info = zipfile.ZipInfo(name)
    if mode is not None:
        info.external_attr = mode << 16
    info.flag_bits |= flags
    return info


def archive_with(*items):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for info, data in items:
            archive.writestr(info, data)
    return buffer.getvalue()


def encrypted(archive: bytes) -> bytes:
    """zipfile clears the encryption bit when writing; set it in both headers by hand."""
    data = bytearray(archive)
    for signature, offset in ((b'PK\x03\x04', 6), (b'PK\x01\x02', 8)):
        at = data.find(signature)
        data[at + offset] |= 0x1
    return bytes(data)


@pytest.mark.parametrize('archive,message', [
    (archive_with((zinfo('../evil.js'), b'x')), 'rutas no válidas'),
    (archive_with((zinfo('/abs/app.json'), b'x')), 'no válid'),
    # zipfile turns «\\» into «/» on Windows; either way the climb out is refused.
    (archive_with((zinfo('demo\\..\\..\\evil.js'), b'x')), 'no válid'),
    (archive_with((zinfo('C:/app.json'), b'x')), 'nombre de archivo no válido'),
    (archive_with((zinfo('ui/link.js', mode=stat.S_IFLNK | 0o777), b'/etc/passwd')), 'enlaces'),
    (encrypted(archive_with((zinfo('ui/main.js'), b'x'))), 'cifrado'),
    (archive_with(*[(zinfo(f'ui/f{n}.js'), b'') for n in range(MAX_ENTRIES + 1)]), 'más de'),
    (archive_with((zinfo('app.json'), b'{}'), (zinfo('APP.json'), b'{}')), 'repite'),
    (b'not a zip', 'no es un .zip'),
], ids=['climb', 'absolute', 'backslash-climb', 'drive', 'symlink', 'encrypted', 'too-many', 'case-duplicate', 'not-zip'])
def test_unsafe_or_broken_archives_are_refused_before_anything_is_kept(archive, message):
    with pytest.raises(PackageError, match=message):
        read_zip(archive)


def test_sizes_are_bounded_before_reading(tmp_path):
    with pytest.raises(PackageError, match='supera'):
        read_zip(make_zip(files(**{'ui/big.js': b'x' * (MAX_UI_FILE_BYTES + 1)})))
    api = client(tmp_path)
    huge = api.post('/api/app-packages/inspect', content=b'x' * (6 * 1024 * 1024 + 1), headers={**ORIGIN, 'content-type': 'application/zip'})
    assert huge.status_code == 413


@pytest.mark.parametrize('change,message,code', [
    ({'backend/service.py': b'print(1)'}, 'servicio de Python', 'needs_review'),
    ({'skill/SKILL.md': b'# x'}, 'skill de Hermes', 'needs_review'),
    ({'app.json': json.dumps({**MANIFEST, 'id': 'calendar', 'widgets': []}).encode()}, 'incluida con Iris', 'reserved_id'),
    ({'app.json': json.dumps({**MANIFEST, 'id': 'store', 'widgets': []}).encode()}, 'incluida con Iris', 'reserved_id'),
    ({'app.json': json.dumps({**MANIFEST, 'ui': {'module': 'automation_app.js'}, 'widgets': []}).encode()}, 'ui.entry', 'invalid_package'),
    ({'app.json': json.dumps({**MANIFEST, 'requestedCapabilities': ['apps.actions']}).encode()}, 'sin permisos', 'needs_review'),
    ({'app.json': json.dumps({**MANIFEST, 'sdkVersion': 3}).encode()}, 'no es compatible', 'invalid_package'),
    ({'app.json': json.dumps({**MANIFEST, 'extra': 1}).encode()}, 'campo desconocido', 'invalid_package'),
    ({'app.json': b'{no json'}, 'JSON válido', 'invalid_package'),
    ({'app.json': None}, 'Falta app.json', 'invalid_package'),
    ({'ui/main.js': b'export default function () {}'}, 'createApp', 'invalid_package'),
    ({'ui/widget.js': None}, 'createWidget', 'invalid_package'),
    ({'ui/style.css': None}, 'falta la hoja', 'invalid_package'),
    ({'ui/other.css': b'a{}'}, 'una sola hoja', 'invalid_package'),
    ({'ui/page.html': b'<script>'}, 'solo se instalan', 'invalid_package'),
    ({'ui/run.exe': b'MZ'}, 'solo se instalan', 'invalid_package'),
    ({'ui/Main Copy.js': b'x'}, 'minúsculas', 'invalid_package'),
    ({'ui/latin.js': 'á'.encode('latin-1')}, 'UTF-8', 'invalid_package'),
    ({'ui/style.css.js': b'export default "";'}, 'se publicaría', 'invalid_package'),
])
def test_the_package_follows_the_same_rules_as_the_build(change, message, code):
    with pytest.raises(PackageError, match=message) as caught:
        check_package(*read_zip(make_zip(files(**change))), bundled_ids=APP_IDS, icon_names=ICON_NAMES)
    assert caught.value.code == code


def test_the_review_lists_what_the_code_appears_to_do():
    risky = (b"export function createApp() { fetch('/api/mail/messages'); eval('1');"
             b" document.body.innerHTML = x; localStorage.x = 1; window.open('https://evil.example'); return {}; }\n")
    package = check_package(*read_zip(make_zip(files(**{'ui/main.js': risky, 'ui/blob.js': b'const a = "' + b'QUJD' * 200 + b'";'}))),
                            bundled_ids=APP_IDS, icon_names=ICON_NAMES)
    assert [risk['code'] for risk in package['risks']] == ['network', 'iris', 'links', 'dynamic', 'html', 'storage', 'navigation', 'obfuscated']
    assert package['risks'][0]['files'] == ['main.js'] and package['risks'][-1]['files'] == ['blob.js']
    clean = check_package(*read_zip(make_zip(files())), bundled_ids=APP_IDS, icon_names=ICON_NAMES)
    assert clean['risks'] == [], 'relative imports and textContent are not flagged'


def test_a_long_stylesheet_is_not_hidden_code_but_a_remote_font_or_image_is_a_connection():
    """Found installing Grok's «enfoque»: the stylesheet becomes one long module line at install."""
    long_css = ('.demo-root { color: red; }\n' * 120).encode()
    package = check_package(*read_zip(make_zip(files(**{'ui/style.css': long_css}))), bundled_ids=APP_IDS, icon_names=ICON_NAMES)
    assert package['risks'] == []
    remote = b'@font-face { src: url("https://fonts.example/x.woff2"); }\n.demo-root { background: url(//cdn.example/a.png); }\n'
    package = check_package(*read_zip(make_zip(files(**{'ui/style.css': remote}))), bundled_ids=APP_IDS, icon_names=ICON_NAMES)
    assert [(risk['code'], risk['files']) for risk in package['risks']] == [('network', ['style.css.js']), ('links', ['style.css.js'])]


@pytest.mark.parametrize('source,expected', [
    (b"fetch('/api/config');", ['network', 'iris']),
    (b"fetch('https://example.invalid/');", ['network', 'links']),
    (b"new Function('return 1')();", ['dynamic']),
    (b'const value = "' + b'a' * 1600 + b'";', ['obfuscated']),
], ids=['iris-api', 'network', 'dynamic', 'long-javascript'])
def test_executable_js_with_a_css_js_name_keeps_the_same_review_signals(source, expected):
    """Only CSS actually converted by Core gets the stylesheet-specific review."""
    for name in ('helper.js', 'helper.css.js'):
        contents = files(**{'ui/main.js': f"import './{name}';\n{MAIN}".encode(), f'ui/{name}': source})
        package = check_package(*read_zip(make_zip(contents)), bundled_ids=APP_IDS, icon_names=ICON_NAMES)
        assert package['files'][name] == source, 'this is supplied executable JavaScript, not converted CSS'
        assert [(risk['code'], risk['files']) for risk in package['risks']] == [(code, [name]) for code in expected]


@pytest.mark.parametrize('method,path', [('post', '/api/app-packages/inspect'), ('post', '/api/app-packages/install'),
                                         ('delete', '/api/app-packages/demo')])
@pytest.mark.parametrize('headers', [{}, {'origin': 'http://evil.example'}, {'origin': 'null'}])
def test_changes_need_the_local_iris_window(tmp_path, method, path, headers):
    reply = getattr(client(tmp_path), method)(path, headers=headers)
    assert reply.status_code == 403
    assert list(tmp_path.iterdir()) == []


def test_another_machine_cannot_read_or_install(tmp_path):
    lan = client(tmp_path, peer='192.168.1.20')
    assert lan.get('/api/app-packages').status_code == 403
    assert lan.post('/api/app-packages/inspect', content=make_zip(files()), headers={**ORIGIN, 'content-type': 'application/zip'}).status_code == 403


def test_installed_widgets_and_shortcuts_are_allowed_then_cleaned_on_uninstall(tmp_path):
    api = client(tmp_path)
    widget = {'instance_id': 'wdg_' + 'a' * 32, 'widget_id': 'demo.timer', 'config_version': 1, 'size': 'compact', 'calendars': []}
    layout = {'schema': 'iris.desktop-widgets/v1', 'revision': 0, 'side': 'right', 'widgets': [widget]}
    assert api.put('/api/desktop/widgets', json=layout).status_code == 422, 'not installed yet: not loadable'
    install(api, make_zip(files()))
    assert api.put('/api/desktop/widgets', json=layout).status_code == 200
    home = api.get('/api/desktop/home').json()
    home = {**home, 'shortcuts': [*home['shortcuts'], 'demo'], 'order': ['app:demo', f"widget:{widget['instance_id']}", 'app:theme']}
    assert api.put('/api/desktop/home', json=home).status_code == 200
    gone = api.delete('/api/app-packages/demo', headers=ORIGIN).json()
    assert gone['desktop_cleaned'] is True
    assert api.get('/api/desktop/widgets').json()['widgets'] == []
    after = api.get('/api/desktop/home').json()
    assert 'demo' not in after['shortcuts'] and after['order'] == ['app:theme']
    assert after['shortcuts'] == [key for key in home['shortcuts'] if key != 'demo'], 'other shortcuts stay'


def test_a_changed_stored_file_is_never_served(tmp_path):
    api = client(tmp_path)
    _, installed = install(api, make_zip(files()))
    # Through the store's own connection, so only the bytes differ from what was checked and hashed.
    store = AppPackageStore(profile_store_path(PACKAGE_SPEC, tmp_path), root=tmp_path)
    with store._transaction() as conn:
        assert conn.execute("UPDATE files SET content=? WHERE path='main.js'", (b'export function createApp() { steal(); }',)).rowcount == 1
    assert api.get(installed['entry'].replace('main.js', 'timer.js')).status_code == 200, 'control: the other files still serve'
    assert api.get(installed['entry']).status_code == 503


@pytest.mark.parametrize('path', ['../packages.sqlite3', 'ui/../../x.js', 'MAIN.js', 'main.exe', 'a/b/c/d.js'])
def test_the_file_route_only_serves_names_of_an_installation(tmp_path, path):
    api = client(tmp_path)
    _, installed = install(api, make_zip(files()))
    base = installed['entry'].rsplit('/', 1)[0]
    assert api.get(f'{base}/{path}').status_code == 404
    assert api.get('/api/app-packages/files/ins_nope/main.js').status_code == 404
