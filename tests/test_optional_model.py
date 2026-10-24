import io
import json
import pytest
from regulatory_agent.requests import ClarificationNeeded, parse_request


def response(matter,category):
    return io.BytesIO(json.dumps({'message':{'content':json.dumps({'matter':matter,'category':category})}}).encode())


def test_optional_local_model_for_unusual_phrasing(monkeypatch):
    monkeypatch.setenv('OLLAMA_MODEL','test-model')
    def fake_urlopen(request,timeout):
        payload=json.loads(request.data)
        assert payload['model']=='test-model'
        assert payload['stream'] is False
        assert request.full_url=='http://localhost:11434/api/chat'
        return response('M12205','Key Documents')
    monkeypatch.setattr('urllib.request.urlopen',fake_urlopen)
    assert parse_request('M12205: please get the most important filings').category=='Key Documents'


@pytest.mark.parametrize('matter,category',[('M99999','Exhibits'),('M12205','Shell commands'),('M12205','unclear')])
def test_model_cannot_change_matter_or_invent_type(monkeypatch,matter,category):
    monkeypatch.setenv('OLLAMA_MODEL','test-model')
    monkeypatch.setattr('urllib.request.urlopen',lambda *a,**k:response(matter,category))
    with pytest.raises(ClarificationNeeded):
        parse_request('M12205 most important filings')
