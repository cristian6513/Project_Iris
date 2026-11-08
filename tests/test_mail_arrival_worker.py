"""The actual isolated Google worker distinguishes messageAdded from label changes."""
import copy
from tests.test_google_worker import pytestmark  # noqa: F401
from tests.test_google_worker_reads import GMAIL,_google,_credential,read,records


def test_only_message_added_is_reported_as_a_new_delivery(tmp_path):
    gmail=copy.deepcopy(GMAIL)
    gmail['history']['1000']['pages'][0]['history'].append({'id':'1002','labelsAdded':[{'message':{'id':'m1'}}]})
    google=_google(tmp_path,gmail=gmail)
    lines=read(google,_credential(google),'sync.gmail',checkpoint='1000',window_days=30,max_messages=200)
    assert {row['id'] for row in records(lines)[0]}=={'m1','m4'}
    assert lines[-1]['public']['arrivals']==['m4']
