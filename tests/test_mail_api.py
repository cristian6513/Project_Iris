"""Exercise real routes, peer guard, private error responses and read-only surface."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.mail_api import create_mail_router
from test_mail_service import ACCOUNT, Cache


def client(peer='127.0.0.1'):
    cache = Cache()
    app = FastAPI(); app.include_router(create_mail_router(lambda: cache))
    return TestClient(app, client=(peer, 43000), base_url='http://127.0.0.1'), cache


def test_open_and_select_are_private_cache_reads_with_no_side_effect():
    browser, cache = client()
    answer = browser.get('/api/mail')
    assert answer.status_code == 200 and 'no-store' in answer.headers['cache-control']
    row = answer.json()['messages'][0]
    detail = browser.get('/api/mail/messages/' + row['id'])
    assert detail.status_code == 200 and detail.json()['message']['unread']
    assert cache.syncs == [] and cache.reads == 2


@pytest.mark.parametrize('peer', ['192.168.1.8', '::ffff:192.168.1.8'])
@pytest.mark.parametrize('path,method,body', [('/api/mail', 'get', None), ('/api/mail/messages/mail_' + 'a'*40, 'get', None),
                                           ('/api/mail/sync', 'post', {'account_ids': [ACCOUNT]})])
def test_lan_cannot_read_detail_or_sync(path, method, body, peer):
    browser, cache = client(peer)
    answer = getattr(browser, method)(path, **({'json': body} if body else {}))
    assert answer.status_code == 403 and 'no-store' in answer.headers['cache-control']
    assert cache.reads == 0 and cache.syncs == []


@pytest.mark.parametrize('query', ['folder=destroy', 'limit=abc', 'limit=101', 'filter=archive', 'cursor=bad!!'])
def test_validation_is_private_and_cannot_trigger_sync(query):
    browser, cache = client()
    answer = browser.get('/api/mail?' + query)
    assert answer.status_code == 422 and 'no-store' in answer.headers['cache-control']
    assert cache.syncs == []


def test_sync_body_rejects_unknown_fields_and_never_reads_calendar():
    browser, cache = client()
    for body in [{'account_ids': [ACCOUNT], 'services': ['calendar.read']}, {'account_ids': []}, {'account_ids': [ACCOUNT, ACCOUNT]}]:
        answer = browser.post('/api/mail/sync', json=body)
        assert answer.status_code == 422 and 'no-store' in answer.headers['cache-control']
    assert cache.syncs == []
    answer = browser.post('/api/mail/sync', json={'account_ids': [ACCOUNT]})
    assert answer.status_code == 202 and answer.json()['accepted']
    assert cache.syncs == [(ACCOUNT, ['gmail.read'])]


def test_no_gmail_write_endpoints_exist():
    browser, cache = client()
    for action in ['send', 'archive', 'mark-read', 'attachments', 'delete']:
        assert browser.post('/api/mail/' + action).status_code == 404
    assert cache.reads == 0 and cache.syncs == []


def test_store_failure_is_private_and_never_includes_the_exception_text():
    browser, cache = client()
    async def failed():
        raise OSError('PRIVATE-FILE-AND-CREDENTIAL')
    cache.mail_cache = failed
    answer = browser.get('/api/mail')
    assert answer.status_code == 503 and 'no-store' in answer.headers['cache-control']
    assert 'PRIVATE' not in answer.text
