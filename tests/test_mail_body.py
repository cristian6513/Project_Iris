"""Real text extraction, owner and HTTP route. All messages/accounts invented."""
import asyncio
import base64
import json

import pytest
from fastapi import FastAPI

from core.google_connections import GoogleServiceError
from core.google_sync import MAILBOX, SUMMARY
from core.mail_api import create_mail_router
from core.mail_body import BodyError, MAX_BODY_CHARS, extract_body, validate_body
from test_calendar_google_cache import seeded


def part(text='', mime='text/plain', charset='utf-8', **extra):
    raw = text.encode(charset)
    return {'mimeType': mime, 'headers': [{'name': 'Content-Type', 'value': f'{mime}; charset={charset}'}],
            'body': {'size': len(raw), 'data': base64.urlsafe_b64encode(raw).decode().rstrip('=')}, **extra}


def test_long_plain_unicode_and_literal_entities_are_preserved():
    content = 'Hola 👋\n' + ('Texto &lt;literal&gt;.\n' * 200)
    body = extract_body(part(content))
    assert body['text'] == content and len(body['text']) > 500
    assert body['source_format'] == 'plain' and not body['lossy']
    assert extract_body(part('Español: piñata', charset='iso-8859-1'))['text'] == 'Español: piñata'


def test_alternative_prefers_plain_and_mixed_does_not_read_file_or_attached_message():
    calls = []
    payload = {'mimeType': 'multipart/mixed', 'parts': [
        {'mimeType': 'multipart/alternative', 'parts': [part('<p>HTML duplicate</p>', 'text/html'), part('Real text')]},
        {'mimeType': 'text/plain', 'filename': 'secret.txt', 'body': {'attachmentId': 'file', 'size': 10}},
        {'mimeType': 'message/rfc822', 'parts': [part('ATTACHED MESSAGE')]},
        {'mimeType': 'image/png', 'body': {'data': 'BINARY'}}]}
    body = extract_body(payload, lambda *args: calls.append(args))
    assert body['text'] == 'Real text' and body['attachments_omitted'] and calls == []


def test_html_is_inert_text_without_scripts_styles_urls_or_trackers():
    payload = part('<head><title>hidden</title><style>CSS</style></head><p>Hola &amp; gracias</p>'
                   '<script>EVIL()</script><img src="https://tracker.test/p">'
                   '<a href="javascript:evil()">Texto del enlace</a><p>Segunda línea</p>', 'text/html')
    body = extract_body(payload)
    assert body['source_format'] == 'html_text'
    assert 'Hola & gracias' in body['text'] and 'Texto del enlace' in body['text']
    for forbidden in ['hidden', 'CSS', 'EVIL', 'tracker', 'javascript', '<img']:
        assert forbidden not in body['text']


def test_text_body_stored_as_attachment_id_is_loaded_but_files_never_are():
    calls = []
    def load(identity, size):
        calls.append((identity, size))
        return base64.urlsafe_b64encode(b'Full text').decode()
    body = extract_body({'mimeType': 'text/plain', 'body': {'size': 9, 'attachmentId': 'body'}}, load)
    assert body['text'] == 'Full text' and calls == [('body', 9)]
    with pytest.raises(BodyError):
        extract_body({'mimeType': 'text/plain', 'headers': [{'name': 'Content-Disposition', 'value': 'attachment'}],
                      'body': {'size': 2, 'attachmentId': 'file'}}, load)
    assert len(calls) == 1


@pytest.mark.parametrize('payload,code', [
    ({'mimeType': 'text/plain', 'body': {'data': '%%%'}}, 'body_unavailable'),
    ({'mimeType': 'text/plain', 'body': {'size': 2}}, 'body_unavailable'),
    ({'mimeType': 'application/pkcs7-mime', 'body': {}}, 'body_unsupported'),
    (part('x' * (MAX_BODY_CHARS + 1)), 'body_too_large'),
    ({'mimeType': 'multipart/mixed', 'parts': [part('')] * 201}, 'body_too_large'),
])
def test_invalid_unsupported_and_large_bodies_are_not_misrepresented_as_empty(payload, code):
    with pytest.raises(BodyError) as caught:
        extract_body(payload)
    assert caught.value.code == code


def test_nested_payload_limit_empty_and_lossy_text_have_distinct_results():
    assert extract_body(part(''))['text'] == ''
    data = part(''); data['body']['data'] = '_w'
    assert extract_body(data)['lossy'] is True
    assert extract_body(part('a\x00b\u202ec'))['text'] == 'abc'
    payload = part('deep')
    for _ in range(22):
        payload = {'mimeType': 'multipart/mixed', 'parts': [payload]}
    with pytest.raises(BodyError, match='body_too_large'):
        extract_body(payload)
    body = extract_body(part('safe'))
    assert 'html' not in validate_body({**body, 'html': '<script>evil</script>'})
    with pytest.raises(BodyError):
        validate_body({**body, 'text': 7})


class Worker:
    available = True
    def __init__(self, *, error=None, waiting=False, malformed=False):
        self.calls = []; self.error = error; self.malformed = malformed
        self.entered = asyncio.Event(); self.release = asyncio.Event(); self.collected = asyncio.Event()
        if not waiting:
            self.release.set()

    async def read_round(self, method, credential, **kwargs):
        self.calls.append((method, credential, kwargs['params']))
        self.entered.set()
        try:
            await self.release.wait()
            if self.error:
                yield {'event': 'error', 'public': {'code': self.error, 'retry_after': 120}}
            else:
                yield {'event': 'result', 'public': {'message_id': kwargs['params']['message_id'],
                    'body': {'text': 7} if self.malformed else extract_body(part('Full message\n' + 'test ' * 300))}}
        finally:
            self.collected.set()


def test_owner_uses_same_credential_and_keeps_full_content_out_of_metadata_store(tmp_path):
    async def run():
        service, store, account = await seeded(tmp_path/'profile')
        worker = Worker(); service._worker = worker
        try:
            before = store.collection(account.id, 'gmail.read', MAILBOX)
            body = await service.mail_body(account.id, 'mail')
            assert body['text'].startswith('Full message')
            assert worker.calls == [('read.gmail_body', b'SECRET-CREDENTIAL', {'message_id': 'mail'})]
            assert store.collection(account.id, 'gmail.read', MAILBOX) == before
            assert 'Full message' not in json.dumps(store.items(account.id, 'gmail.read', MAILBOX))
            assert not service._mail_tasks
        finally:
            await service.close()
    asyncio.run(run())


@pytest.mark.parametrize('action', ['disconnect', 'close', 'cancel'])
def test_cancellation_disconnect_and_shutdown_collect_inflight_body_without_delivering_it(tmp_path, action):
    async def run():
        service, _, account = await seeded(tmp_path/'profile')
        worker = Worker(waiting=True); service._worker = worker
        task = asyncio.create_task(service.mail_body(account.id, 'mail'))
        try:
            await asyncio.wait_for(worker.entered.wait(), 3)
            if action == 'disconnect':
                await asyncio.wait_for(service.disconnect(account.id), 3)
            elif action == 'close':
                await asyncio.wait_for(service.close(), 3)
            else:
                task.cancel()
            answer = (await asyncio.gather(task, return_exceptions=True))[0]
            assert isinstance(answer, (GoogleServiceError, asyncio.CancelledError))
            assert worker.collected.is_set() and not service._mail_tasks
        finally:
            task.cancel(); await asyncio.gather(task, return_exceptions=True); await service.close()
    asyncio.run(run())


@pytest.mark.parametrize('change', ['missing', 'deleted_during_read', 'new_generation', 'wrong_account', 'malformed'])
def test_owner_checks_account_message_and_generation_again_before_delivery(tmp_path, change):
    async def run():
        service, store, account = await seeded(tmp_path/'profile')
        worker = Worker(waiting=True, malformed=change == 'malformed'); service._worker = worker
        task = asyncio.create_task(service.mail_body('gacc_' + 'f'*32 if change == 'wrong_account' else account.id,
                                                     'absent' if change == 'missing' else 'mail'))
        try:
            if change not in ('wrong_account', 'missing'):
                await asyncio.wait_for(worker.entered.wait(), 3)
                if change == 'deleted_during_read':
                    store.clear_cache(account.id)
                if change == 'new_generation':
                    store.disconnect(account.id)
                worker.release.set()
            with pytest.raises(GoogleServiceError):
                await asyncio.wait_for(task, 3)
            if change in ('wrong_account', 'missing'):
                assert not worker.calls
        finally:
            task.cancel(); await asyncio.gather(task, return_exceptions=True); await service.close()
    asyncio.run(run())


def test_rate_limit_is_persisted_and_not_bypassed_by_reading_another_body(tmp_path):
    async def run():
        service, store, account = await seeded(tmp_path/'profile')
        worker = Worker(error='rate_limited'); service._worker = worker
        try:
            for _ in range(2):
                with pytest.raises(GoogleServiceError, match='Google|peticiones|espera|límite'):
                    await service.mail_body(account.id, 'mail')
            assert len(worker.calls) == 1
            assert store.collection(account.id, 'gmail.read', 'mail_body').next_attempt_at > service._clock()
            assert store.collection(account.id, 'gmail.read', SUMMARY) is None
        finally:
            await service.close()
    asyncio.run(run())


def test_body_http_is_explicit_private_account_scoped_and_preserves_gmail_state(tmp_path):
    async def run():
        service, _, account = await seeded(tmp_path/'profile')
        worker = Worker(); service._worker = worker
        app = FastAPI(); app.include_router(create_mail_router(lambda: service))
        # ASGI transports share this event loop; TestClient's separate loop would not.
        import httpx
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 22)), base_url='http://127.0.0.1') as client:
                rows = (await client.get('/api/mail?folder=all')).json()['messages']
                key = rows[0]['id']
                await client.get('/api/mail/messages/' + key)
                assert not worker.calls
                response = await client.post('/api/mail/messages/' + key + '/body')
                assert response.status_code == 200 and 'no-store' in response.headers['cache-control']
                assert response.json()['message_id'] == key and response.json()['account_id'] == account.id
                assert 'Full message' in response.json()['body']['text']
                assert b'SECRET-CREDENTIAL' not in response.content and b'message_id": "mail"' not in response.content
                assert (await client.get('/api/mail/messages/' + key + '/body')).status_code == 405
                assert (await client.post('/api/mail/messages/mail_' + '0'*40 + '/body')).status_code == 404
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('192.168.1.4', 22)), base_url='http://local') as client:
                assert (await client.post('/api/mail/messages/' + key + '/body')).status_code == 403
            assert len(worker.calls) == 1
        finally:
            await service.close()
    asyncio.run(run())


def test_nested_hidden_tags_are_linear_and_keep_their_meaning():
    """Review 2026-10-19: each closing tag scanned and copied the stack of open hidden tags."""
    import time
    from core.mail_body import _HTMLText

    def text(html):
        parser = _HTMLText(); parser.feed(html); parser.close()
        return "".join(parser.output)

    assert text("<svg><g><svg>in</svg>still</svg>out") == "out"
    assert text("<div>a<svg>b</div>c</svg>d") == "\nad"
    started = time.perf_counter()
    text("<svg>" * 90_000 + "</svg>" * 90_000)  # about 1 MB, the per-part limit
    assert time.perf_counter() - started < 3.0, "was about 10 s when quadratic"
