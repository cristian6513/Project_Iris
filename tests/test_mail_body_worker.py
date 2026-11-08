"""Actual isolated worker/RPC/Google client, with synthetic HTTP and DPAPI credentials."""
import pytest

from tests.test_google_worker_reads import _credential, read
from tests.test_google_worker import pytestmark  # noqa: F401
from tests.test_mail_body import part


def test_isolated_worker_reads_full_body_without_marking_read_or_fetching_file_attachments(tmp_path):
    from tests.test_google_worker_reads import GMAIL
    import copy
    gmail = copy.deepcopy(GMAIL)
    gmail['messages']['m1']['payload'] = {'mimeType': 'multipart/mixed', 'parts': [
        part('Cuerpo completo.\n' + 'Prueba de lectura ' * 100),
        {'mimeType': 'text/plain', 'filename': 'document.txt', 'body': {'attachmentId': 'never', 'size': 5}}]}
    from tests.test_google_worker import Google
    from core.google_contracts import requested_scopes
    google = Google(tmp_path, token={'scope': ' '.join(requested_scopes(['gmail.read'])), 'id_token': {}}, api={'gmail': gmail})
    credential = _credential(google)
    lines = read(google, credential, 'read.gmail_body', message_id='m1')
    assert lines[-1]['event'] == 'result', lines
    body = lines[-1]['public']['body']
    assert body['text'].startswith('Cuerpo completo.') and len(body['text']) > 500 and body['attachments_omitted']
    calls = google.log('api')
    assert {call['method'] for call in calls} == {'GET'}
    assert [call['query'].get('format') for call in calls] == ['full', 'full']
    assert calls[0]['query']['fields'] == 'id,sizeEstimate'
    assert calls[1]['query']['fields'] == 'id,payload'
    assert all('/attachments/' not in call['path'] for call in calls)


def test_worker_fetches_only_the_unnamed_text_part_when_gmail_uses_attachment_id(tmp_path):
    from tests.test_google_worker import Google
    from core.google_contracts import requested_scopes
    gmail = {'messages': {'m': {'id': 'm', 'sizeEstimate': 100, 'payload': {
        'mimeType': 'text/plain', 'body': {'attachmentId': 'text-body', 'size': 4}}}},
        'bodies': {'text-body': {'data': 'SG9sYQ', 'size': 4}}}
    google = Google(tmp_path, token={'scope': ' '.join(requested_scopes(['gmail.read'])), 'id_token': {}}, api={'gmail': gmail})
    lines = read(google, _credential(google), 'read.gmail_body', message_id='m')
    assert lines[-1]['public']['body']['text'] == 'Hola'
    assert google.log('api')[-1]['path'].endswith('/attachments/text-body')


@pytest.mark.parametrize('message_id,size,code', [('missing', 10, 'not_found'), ('m1', 20*1024*1024, 'body_too_large')])
def test_worker_refuses_absent_or_oversize_messages_before_full_get(tmp_path, message_id, size, code):
    import copy
    from tests.test_google_worker import Google
    from tests.test_google_worker_reads import GMAIL
    from core.google_contracts import requested_scopes
    gmail = copy.deepcopy(GMAIL); gmail['messages']['m1']['sizeEstimate'] = size
    google = Google(tmp_path, token={'scope': ' '.join(requested_scopes(['gmail.read'])), 'id_token': {}}, api={'gmail': gmail})
    lines = read(google, _credential(google), 'read.gmail_body', message_id=message_id)
    assert lines[-1]['public']['code'] == code, lines
    assert len(google.log('api')) == 1
