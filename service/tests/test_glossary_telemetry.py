import json
import pytest
from pydantic import ValidationError
from zotero_local_bilingual.glossary_support import parse_glossary, build_glossaries
from zotero_local_bilingual.translation_policy import TranslationOptions
from zotero_local_bilingual.telemetry import job_metrics


def test_glossary_native_match_only_relevant_terms():
    glossary=build_glossaries("spectral range=频谱范围\noptical interrogation=光学探测")[0]
    active=glossary.get_active_entries_for_text("We consider optical interrogation of atoms.")
    assert ("optical interrogation","光学探测") in active
    assert ("spectral range","频谱范围") not in active
    assert parse_glossary("# note\n\nSignal=信号\nsignal=信号")==[("signal","信号")]


@pytest.mark.parametrize("text",["no separator","x=","=空","X=一\nx=二","x|y=公式"])
def test_bad_glossary_rejected_before_model(text):
    with pytest.raises(ValidationError):TranslationOptions(glossary_text=text)


def test_live_metrics_allowlist_and_terminal_time(tmp_path):
    working=tmp_path/"engine-working";working.mkdir()
    (working/"api-usage.json").write_text(json.dumps({"total_tokens":123,"request_count":2,"prompt_tokens":100,"completion_tokens":23,"secret":"never return", "requests":[{"content":"private"}]}),"utf8")
    row={"source_path":str(tmp_path/"source.pdf"),"status":"completed","created_at":"2026-10-11T01:00:00+00:00","updated_at":"2026-10-11T01:00:30+00:00"}
    metrics=job_metrics(row)
    assert metrics=={"usage_kind":"tokens","characters":None,"total_tokens":123,"request_count":2,"prompt_tokens":100,"completion_tokens":23,"budget_total_tokens":None,"unconfirmed_tokens":None,"elapsed_seconds":30,"required_budget_tokens":None}
    (working/"api-usage.json").write_text("{", "utf8")
    assert job_metrics(row)["total_tokens"] is None
