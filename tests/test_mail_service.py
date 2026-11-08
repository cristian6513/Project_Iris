"""F13 projection/pagination only; no network, credentials or personal profiles."""
import asyncio
import copy
import json

import pytest

from core.google_connections import GoogleServiceError
from core.mail_service import MailError, MailService

ACCOUNT = 'gacc_' + '1' * 32
SECOND = 'gacc_' + '2' * 32


def account(identity=ACCOUNT, **changes):
    result = dict(id=identity, email='one@example.test', auth_state='connected',
                services={'gmail.read': {'granted': True}}, sync={'gmail.read': {
                    'state': 'idle', 'last_success_at': '2026-10-17T10:00:00Z',
                    'coverage': {'window_days': 30, 'max_messages': 200, 'complete': False}}})
    return dict(result, **changes)


def message(identity='one', *, owner=ACCOUNT, date=1000, labels=None, **changes):
    return {'account_id': owner, 'message': dict(id=identity, internalDate=str(date), labelIds=labels or ['INBOX', 'UNREAD'],
        headers={'subject': 'Reunión de seguimiento', 'from': 'Cliente Orion <cliente@example.test>', 'to': 'one@example.test'},
        snippet='Hola &amp; adiós', **changes)}


class Cache:
    def __init__(self, *, rows=None, accounts=None):
        self.data = {'available': True, 'accounts': accounts or [account()], 'messages': rows if rows is not None else [message()], 'warnings': []}
        self.reads, self.syncs = 0, []

    async def mail_cache(self):
        self.reads += 1
        return copy.deepcopy(self.data)

    async def sync(self, identity, services):
        self.syncs.append((identity, services))


def run(coro):
    return asyncio.run(coro)


def test_projection_is_readonly_decodes_entities_and_whitelists_fields():
    cache = Cache(rows=[message(secret='PRIVATE', payload={'html': '<img src=https://remote.test/tracker>'})])
    result = run(MailService(cache).snapshot())
    assert result['messages'][0]['snippet'] == 'Hola & adiós'
    assert result['messages'][0]['unread'] and result['read_only']
    assert result['messages'][0]['content_kind'] == 'snippet'
    assert 'PRIVATE' not in json.dumps(result) and 'payload' not in json.dumps(result)
    assert cache.syncs == []


@pytest.mark.parametrize('enabled', [True, False])
def test_automatic_reading_status_comes_from_the_account_without_enabling_it(enabled):
    cache = Cache(accounts=[account(services={'gmail.read': {'granted': True, 'enabled': enabled}})])
    result = run(MailService(cache).snapshot())
    assert result['accounts'][0]['sync_enabled'] is enabled
    assert cache.syncs == []


def test_same_gmail_id_in_two_accounts_is_not_one_message():
    cache = Cache(rows=[message(), message(owner=SECOND)], accounts=[account(), account(SECOND)])
    rows = run(MailService(cache).snapshot())['messages']
    assert len(rows) == 2 and len({row['id'] for row in rows}) == 2
    selected = run(MailService(cache).snapshot(account_id=SECOND))
    assert [row['account_id'] for row in selected['messages']] == [SECOND]


def test_sorting_and_all_pages_are_stable_with_equal_dates():
    cache = Cache(rows=[message(str(i), date=i // 3) for i in range(105)])
    service = MailService(cache)
    async def scenario():
        rows, cursor = [], ''
        while True:
            page = await service.snapshot(limit=17, cursor=cursor)
            rows.extend(page['messages'])
            if not page['next_cursor']:
                break
            cursor = page['next_cursor']
        assert len(rows) == len({row['id'] for row in rows}) == 105
        assert [row['date_ms'] for row in rows] == sorted([row['date_ms'] for row in rows], reverse=True)
    run(scenario())


@pytest.mark.parametrize('change', ['data', 'query', 'folder', 'limit'])
def test_cursors_cannot_silently_cross_a_sync_or_query_change(change):
    async def scenario():
        cache = Cache(rows=[message(str(i), date=i) for i in range(10)])
        service = MailService(cache)
        cursor = (await service.snapshot(limit=2))['next_cursor']
        params = {'cursor': cursor, 'limit': 2}
        if change == 'data':
            cache.data['messages'][0]['message']['snippet'] = 'Un cambio'
        else:
            params[change] = {'query': 'Hola', 'folder': 'all', 'limit': 3}[change]
        with pytest.raises(MailError) as error:
            await service.snapshot(**params)
        assert error.value.code == 'page_changed'
    run(scenario())


@pytest.mark.parametrize('params', [{'folder': 'trash'}, {'filter': 'delete'}, {'query': 'x' * 201},
    {'limit': 0}, {'limit': 101}, {'limit': True}, {'cursor': 'x' * 257}])
def test_invalid_filters_fail_before_reading(params):
    cache = Cache()
    with pytest.raises(MailError):
        run(MailService(cache).snapshot(**params))
    assert cache.reads == 0


@pytest.mark.parametrize('cursor', ['bad!!', 'e30', 'bnVsbA', 'WzEsZmFsc2Vd'])
def test_malformed_cursors_are_controlled_errors(cursor):
    with pytest.raises(MailError) as error:
        run(MailService(Cache()).snapshot(cursor=cursor))
    assert error.value.code == 'invalid_cursor'


@pytest.mark.parametrize('folder,filter,expected', [('inbox', 'all', 2), ('starred', 'all', 1), ('sent', 'all', 1), ('drafts', 'all', 1),
    ('all', 'all', 4), ('inbox', 'unread', 1), ('inbox', 'important', 1)])
def test_folder_and_status_filters_use_actual_gmail_labels(folder, filter, expected):
    rows = [message('a', labels=['INBOX', 'UNREAD']), message('b', labels=['INBOX', 'STARRED', 'IMPORTANT']),
            message('c', labels=['SENT']), message('d', labels=['DRAFT'])]
    result = run(MailService(Cache(rows=rows)).snapshot(folder=folder, filter=filter))
    assert result['total'] == expected


def test_search_is_literal_case_insensitive_local_and_matches_sender_and_subject():
    cache = Cache()
    assert run(MailService(cache).snapshot(query='ORION'))['total'] == 1
    assert run(MailService(cache).snapshot(query='seguimiento'))['total'] == 1
    assert run(MailService(cache).snapshot(query='.*'))['total'] == 0
    assert cache.syncs == []


@pytest.mark.parametrize('state', ['disconnected', 'reauth_required'])
def test_retained_cache_of_unavailable_account_is_never_returned(state):
    cache = Cache(accounts=[account(auth_state=state)])
    result = run(MailService(cache).snapshot())
    assert result['accounts'][0]['auth_state'] == state and result['messages'] == []
    with pytest.raises(GoogleServiceError):
        run(MailService(cache).sync([ACCOUNT]))
    assert cache.syncs == []


def test_detail_rechecks_current_account_and_never_marks_read():
    async def scenario():
        cache = Cache(); service = MailService(cache)
        row = (await service.snapshot())['messages'][0]
        assert (await service.detail(row['id']))['message']['unread']
        cache.data['accounts'][0]['auth_state'] = 'disconnected'
        with pytest.raises(MailError) as error:
            await service.detail(row['id'])
        assert error.value.status == 404 and cache.syncs == []
    run(scenario())


def test_malformed_dates_and_missing_headers_are_honest():
    rows = [message('one', date='not-a-date'), {'account_id': ACCOUNT, 'message': {'id': 'two'}}]
    result = run(MailService(Cache(rows=rows)).snapshot(folder='all'))
    assert len(result['messages']) == 2
    assert all(row['date_ms'] is None for row in result['messages'])
    assert result['messages'][1]['subject'] or result['messages'][0]['subject']


def test_sync_is_gmail_only_validates_all_accounts_before_any_dispatch_and_reports_partial_failure():
    async def scenario():
        cache = Cache(accounts=[account(), account(SECOND)])
        service = MailService(cache)
        with pytest.raises(GoogleServiceError):
            await service.sync([ACCOUNT, 'unknown'])
        assert cache.syncs == []
        async def sync(identity, services):
            cache.syncs.append((identity, services))
            if identity == SECOND:
                raise GoogleServiceError('rate_limited')
        cache.sync = sync
        result = await service.sync([ACCOUNT, SECOND])
        assert result == {'accepted': True, 'account_ids': [ACCOUNT], 'errors': [{'account_id': SECOND, 'code': 'rate_limited'}]}
        assert cache.syncs == [(ACCOUNT, ['gmail.read']), (SECOND, ['gmail.read'])]
    run(scenario())


def test_unconfigured_projection_is_unavailable_instead_of_successful_empty_mailbox():
    result = run(MailService(None).snapshot())
    assert result['available'] is False and result['accounts'] == []


def test_subjects_and_senders_drop_bidi_overrides_like_bodies():
    """Review 2026-10-19: a U+202E in a subject could show «factura<RLO>fdp.exe» as «factura exe.pdf»."""
    from core.mail_service import project_message
    rlo, isolate = chr(0x202E), chr(0x2066)
    message = project_message("gacc_" + "a" * 32, {"id": "m1", "labelIds": ["INBOX"], "internalDate": "1",
        "headers": {"subject": f"factura{rlo}fdp.exe", "from": f"Banco{isolate} <x@example.test>"}, "snippet": f"ver{rlo}adjunto"})
    assert message["subject"] == "facturafdp.exe"
    assert message["sender"] == "Banco <x@example.test>"
    assert message["snippet"] == "veradjunto"
