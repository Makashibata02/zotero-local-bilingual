import json
from concurrent.futures import ThreadPoolExecutor
import httpx
import pytest
from zotero_local_bilingual.guarded_translator import GuardedTranslator
from zotero_local_bilingual.translation_policy import thinking_setting, TranslationOptions
from zotero_local_bilingual.quality import audit_tracking


def make_translator(tmp_path, handler, **options):
    t = GuardedTranslator({"model": "deepseek-flash", "base_url": "https://api.deepseek.com",
                           "engine_working_dir": str(tmp_path), "ignore_cache": True, **options}, "fake-key")
    t.client = t.client.with_options(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return t


def response(content="译文", finish="stop", usage=True):
    return httpx.Response(200, json={"id":"test", "object":"chat.completion", "created":0,
        "model":"deepseek-flash", "choices":[{"index":0,"finish_reason":finish,
        "message":{"role":"assistant","content":content}}],
        "usage": {"prompt_tokens":20,"completion_tokens":10,"total_tokens":30} if usage else None})


def test_official_deepseek_disabled_and_custom_prompt_sent(tmp_path):
    seen = []
    def handler(request):
        seen.append(json.loads(request.content)); return response()
    t = make_translator(tmp_path, handler, custom_system_prompt="signal space = 信号空间")
    assert t.do_translate("signal space") == "译文"
    assert seen[0]["thinking"] == {"type":"disabled"}
    assert seen[0]["max_tokens"] == 4096
    assert "信号空间" in seen[0]["messages"][0]["content"]
    report=json.loads(t.audit_path.read_text("utf8"))
    assert report["total_tokens"] == 30
    assert "fake-key" not in t.audit_path.read_text("utf8")
    assert report["request_count"] == 1


@pytest.mark.parametrize("content,finish,usage", [("","stop",True),("部分译文","length",True),("译文","stop",False)])
def test_invalid_response_stops_subsequent_requests(tmp_path, content, finish, usage):
    seen=[]
    def handler(request):
        seen.append(request); return response(content,finish,usage)
    t=make_translator(tmp_path,handler)
    for _ in range(2):
        with pytest.raises(RuntimeError): t.do_llm_translate("translate")
    assert len(seen)==1
    assert t.snapshot()["failure"]


def test_invalid_json_fields_never_enter_cache(tmp_path, monkeypatch):
    t=make_translator(tmp_path,lambda request: response('[{"id":0,"output":""}]'),ignore_cache=False)
    saved=[]
    monkeypatch.setattr(t.cache,"get",lambda text:None)
    monkeypatch.setattr(t.cache,"set",lambda *args:saved.append(args))
    with pytest.raises(RuntimeError):
        t.llm_translate("json",rate_limit_params={"request_json_mode":True})
    assert not saved


def test_request_limit_under_concurrency(tmp_path):
    seen=[]
    def handler(request):
        seen.append(request); return response()
    t=make_translator(tmp_path,handler,max_requests=1)
    def call():
        try:t.do_translate("hello")
        except RuntimeError:pass
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _:call(),range(4)))
    assert len(seen)==1


def test_budget_preflight_blocks_without_charging(tmp_path):
    seen=[]
    def handler(request):
        seen.append(request);return response()
    t=make_translator(tmp_path,handler,max_total_tokens=5000)
    with pytest.raises(RuntimeError):t.do_translate("hello"*2000)
    assert not seen


def test_http_error_does_not_retry(tmp_path):
    seen=[]
    def handler(request):
        seen.append(request);return httpx.Response(429,json={"error":{"message":"limited"}})
    t=make_translator(tmp_path,handler)
    with pytest.raises(RuntimeError):t.do_translate("hello")
    assert len(seen)==1


def test_host_matching_and_explicit_modes():
    assert thinking_setting("https://api.deepseek.com/v1")=="disabled"
    assert thinking_setting("https://api.deepseek.com.evil.invalid") is None
    assert thinking_setting("https://other.invalid") is None
    assert thinking_setting("https://other.invalid","disabled")=="disabled"
    assert thinking_setting("https://api.deepseek.com","provider") is None
    assert not TranslationOptions().auto_extract_glossary


def test_tracking_audit_detects_known_blank_paragraph(tmp_path):
    (tmp_path/"translate_tracking.json").write_text(json.dumps({"page":[{"paragraph":[
        {"pdf_unicode":"First paragraph","input":"First paragraph","output":""},
        {"pdf_unicode":"Second paragraph","input":"Second paragraph","output":"第二段"}]}]}),"utf8")
    report=audit_tracking(tmp_path)
    assert report["paragraphs"]==2
    assert report["empty_paragraphs"]==1


@pytest.mark.parametrize("content", ['[]', '[{"src":"signal","tgt":"信号"}]'])
def test_glossary_schema_allows_empty_list(tmp_path, content):
    t=make_translator(tmp_path,lambda request:response(content))
    assert t.do_llm_translate("You are an expert multilingual terminologist. Extract terms", {"request_json_mode":True})==content


def test_empty_translation_array_is_rejected(tmp_path):
    t=make_translator(tmp_path,lambda request:response('[]'))
    with pytest.raises(RuntimeError):t.do_llm_translate("Translate these paragraphs", {"request_json_mode":True})


@pytest.mark.parametrize("rich", [False, True])
def test_reference_policy_is_system_constraint_in_both_translation_paths(tmp_path, rich):
    from zotero_local_bilingual.translation_policy import REFERENCE_POLICY
    seen=[]
    def handler(request):
        seen.append(json.loads(request.content)); return response()
    t=make_translator(tmp_path, handler, custom_system_prompt="Translate every bibliography title")
    if rich:
        t.do_llm_translate("Translate ALL human-readable content")
    else:
        t.do_translate("[1] Smith. Original paper title. 2025.")
    system=seen[0]["messages"][0]
    assert system["role"]=="system"
    assert REFERENCE_POLICY in system["content"]
    assert "surrounding body prose normally" in system["content"]
    assert "Translate appendices normally" in system["content"]
    # Policy changes invalidate earlier cached translations through the role key.
    assert REFERENCE_POLICY in t.cache.params["role"]
