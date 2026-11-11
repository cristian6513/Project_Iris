"""Persisted avatar changes must invalidate Companion's local model selection."""
import asyncio
import io
import json
import os
import struct
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import UploadFile
from starlette.requests import Request

from core import identity_api
from core import websocket_server as ws
from core.config import IRISConfig


def vrm_bytes():
    data = json.dumps({'asset': {'version': '2.0'}, 'nodes': [{}],
        'extensions': {'VRMC_vrm': {'humanoid': {'humanBones': {'hips': {'node': 0}}}}}}).encode()
    data += b' ' * (-len(data) % 4)
    return b'glTF' + (2).to_bytes(4, 'little') + (20 + len(data)).to_bytes(4, 'little') + len(data).to_bytes(4, 'little') + b'JSON' + data


@pytest.fixture
def configured(monkeypatch, tmp_path):
    monkeypatch.setattr('core.config.get_iris_home', lambda: tmp_path)
    monkeypatch.setattr(identity_api, 'get_iris_home', lambda: tmp_path)
    IRISConfig().save()
    client = SimpleNamespace(send_text=AsyncMock())
    monkeypatch.setattr(ws, '_connected_clients', [client])
    return tmp_path, client


def test_upload_notifies_connected_companion_after_config_is_saved(configured):
    _, client = configured
    async def receive(message):
        assert json.loads(message) == {'type': 'avatar_config_changed', 'payload': {}}
        assert IRISConfig.load().avatar_vrm_path.endswith('os-new.vrm')
    client.send_text.side_effect = receive
    response = asyncio.run(ws.upload_os_avatar(UploadFile(filename='new.vrm', file=io.BytesIO(vrm_bytes()))))
    assert response.status_code == 200
    client.send_text.assert_awaited_once()
    public = json.loads(asyncio.run(ws.get_config()).body)
    assert public['avatar_vrm_path'] == '/avatars/os-new.vrm'
    assert len(public['avatar_revision']) == 20


def test_rejected_upload_does_not_announce_a_change(configured):
    _, client = configured
    with pytest.raises(ws.HTTPException):
        asyncio.run(ws.upload_os_avatar(UploadFile(filename='broken.vrm', file=io.BytesIO(b'broken'))))
    client.send_text.assert_not_awaited()
    assert IRISConfig.load().avatar_vrm_path == '', 'IRIS stays'


def test_pmx_upload_keeps_the_original_archive_and_rejects_unsafe_replacements(configured):
    home, client = configured
    header = b"PMX " + struct.pack("<fB", 2.0, 8) + bytes((1, 0, 4, 4, 4, 4, 4, 4))
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as package:
        package.writestr('model/avatar.pmx', header)
    original = archive.getvalue()
    upload = UploadFile(filename='model.zip', file=io.BytesIO(original))
    response = asyncio.run(ws.upload_os_avatar(upload))
    assert json.loads(response.body)['avatar_url'] == '/avatars/os-model.zip'
    assert (home / 'avatars' / 'os-model.zip').read_bytes() == original
    client.send_text.assert_awaited_once()

    client.send_text.reset_mock()
    with zipfile.ZipFile(archive, 'a') as package:
        package.writestr('../outside.png', b'texture')
    archive.seek(0)
    with pytest.raises(ws.HTTPException, match='ruta inválida'):
        asyncio.run(ws.upload_os_avatar(UploadFile(filename='model.zip', file=archive)))
    assert (home / 'avatars' / 'os-model.zip').read_bytes() == original
    assert IRISConfig.load().avatar_vrm_path == str(home / 'avatars' / 'os-model.zip')
    client.send_text.assert_not_awaited()


def test_same_name_replacement_and_external_current_route_change_revision(configured):
    home, _ = configured
    path = home / 'avatars' / 'same.vrm'
    path.parent.mkdir(); path.write_bytes(b'old')
    first = ws._avatar_revision(str(path))
    assert first == ws._avatar_revision('/avatars/same.vrm')
    before = path.stat()
    path.write_bytes(b'new')
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
    assert first != ws._avatar_revision(str(path))
    other = home / 'external.vrm'; other.write_bytes(b'new')
    assert ws._avatar_revision(str(other)) != ws._avatar_revision(str(path))
    assert str(home) not in first
    assert ws._avatar_revision('https://example.com/avatar.vrm') == ''


def test_identity_avatar_change_notifies_the_runtime(configured, monkeypatch):
    home, client = configured
    IRISConfig(avatar_vrm_path=str(home / 'avatars' / 'os-mine.vrm')).save()
    monkeypatch.setattr(identity_api, 'sync_hermes_identity_from_config', lambda cfg: {})
    request = Request({'type': 'http', 'method': 'POST', 'path': '/api/update-identity', 'headers': [], 'app': ws.app})
    response = asyncio.run(identity_api.update_identity(identity_api.IdentityUpdate(avatar_vrm_path=''), request))  # «Volver a IRIS»
    assert response.status_code == 200
    client.send_text.assert_awaited_once()
    assert IRISConfig.load().avatar_vrm_path == ''
