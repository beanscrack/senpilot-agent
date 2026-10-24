import io
import json
import urllib.error
import pytest
from regulatory_agent.llm import LLMUnavailable, interpret_request
from regulatory_agent.requests import ClarificationNeeded, parse_request


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER','openai-compatible')
    monkeypatch.setenv('LLM_BASE_URL','http://127.0.0.1:8000/v1')
    monkeypatch.setenv('LLM_MODEL','swift')
    monkeypatch.setenv('LLM_RESPONSE_FORMAT','json_schema')
    monkeypatch.delenv('LLM_API_KEY',raising=False)


def completion(obj,finish_reason='stop'):
    return io.BytesIO(json.dumps({'choices':[{'finish_reason':finish_reason,'message':{'content':json.dumps(obj)}}]}).encode())


def result(matter='M12205',category='Other Documents',action='fetch_documents'):
    return {'matter':matter,'category':category,'action':action}


def test_model_is_called_for_normal_requests_and_provenance_is_recorded(configured,monkeypatch):
    calls=[]
    def urlopen(request,timeout):
        payload=json.loads(request.data)
        calls.append(payload)
        assert request.full_url=='http://127.0.0.1:8000/v1/chat/completions'
        assert payload['response_format']['json_schema']['strict'] is True
        assert payload['model']=='swift' and payload['temperature']==0
        assert json.loads(payload['messages'][1]['content'])['expected_matter']=='M12205'
        return completion(result())
    monkeypatch.setattr('urllib.request.urlopen',urlopen)
    request=parse_request('M12205 Other Documents',log=lambda _:None)
    assert (request.parser,request.model)==('openai-compatible','swift')
    assert len(calls)==1


def test_model_supports_semantic_category_phrasing(configured,monkeypatch):
    monkeypatch.setattr('urllib.request.urlopen',lambda *a,**k:completion(result(category='Transcripts')))
    assert parse_request('M12205 written hearing records',log=lambda _:None).category=='Transcripts'


@pytest.mark.parametrize('obj',[result(matter='M99999'),result(category='Recordings'),
    result(category='Shell commands'),result(action='run_shell'),result(action='clarify',category='unclear'),
    {**result(),'url':'https://example.com'},result(matter=12205)])
def test_invalid_or_conflicting_model_output_never_triggers_download(configured,monkeypatch,obj):
    monkeypatch.setattr('urllib.request.urlopen',lambda *a,**k:completion(obj))
    with pytest.raises(ClarificationNeeded):
        parse_request('M12205 Other Documents',log=lambda _:None)


def test_truncated_model_output_is_rejected(configured,monkeypatch):
    monkeypatch.setattr('urllib.request.urlopen',lambda *a,**k:completion(result(),finish_reason='length'))
    with pytest.raises(ClarificationNeeded):
        parse_request('M12205 Other Documents',log=lambda _:None)


def test_tunnel_failure_can_fall_back_only_to_an_unambiguous_exact_match(configured,monkeypatch):
    def fail(*a,**kwargs): raise urllib.error.URLError('private exception details')
    monkeypatch.setattr('urllib.request.urlopen',fail)
    logs=[]
    parsed=parse_request('M12205 Other Documents',log=logs.append)
    assert parsed.parser=='rules-fallback' and parsed.model==''
    assert 'Using the exact validated request pattern' in logs[-1]
    assert 'private exception details' not in logs[-1]
    with pytest.raises(ClarificationNeeded):
        parse_request('M12205 miscellaneous filings',log=lambda _:None)
    with pytest.raises(LLMUnavailable):
        parse_request('M12205 Other Documents',allow_fallback=False,log=lambda _:None)


def test_missing_or_multiple_matters_do_not_call_model(configured,monkeypatch):
    def unexpected(*a,**k): pytest.fail('Ambiguous matters must be rejected before inference')
    monkeypatch.setattr('urllib.request.urlopen',unexpected)
    for text in ['Other Documents','M12205 M12383 Exhibits']:
        with pytest.raises(ClarificationNeeded): parse_request(text,log=lambda _:None)


def test_api_key_header_and_json_object_compatibility(configured,monkeypatch):
    monkeypatch.setenv('LLM_API_KEY','test-placeholder-only')
    monkeypatch.setenv('LLM_RESPONSE_FORMAT','json_object')
    def urlopen(request,**kwargs):
        assert request.get_header('Authorization')=='Bearer test-placeholder-only'
        assert json.loads(request.data)['response_format']=={'type':'json_object'}
        return completion(result())
    monkeypatch.setattr('urllib.request.urlopen',urlopen)
    assert interpret_request('Other Documents M12205','M12205')[1]=='swift'
