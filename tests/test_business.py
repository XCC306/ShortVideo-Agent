"""Offline tool and controller tests. No model API is contacted."""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from src.agent import Agent, ModelError
from src.cli import export_run, main
from src.models import UserRequest
from src.tools import execute_tool, load_data, resolve_request


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def context():
    request = UserRequest.model_validate(json.loads((ROOT / "examples/inputs/normal.json").read_text(encoding="utf-8")))
    materials, rules = load_data()
    return request, materials, resolve_request(request, materials, rules)


def draft(draft_id="d1", hold=15.0):
    fact = "演示剪辑器支持字幕功能。"
    return {"schema_version": "1.0", "draft_id": draft_id, "request_id": "demo-request-01",
            "product_id": "demo-editor", "title": "旅行记忆，配上字幕", "concept": "用一句短旁白带出画面。",
            "scenes": [{"scene_id": "s1", "order": 1, "visual": "旅行画面与字幕", "narration": fact,
                        "subtitle": "旅行记忆", "visual_hold_seconds": hold,
                        "estimated_duration_seconds": None, "claim_ids": ["c1"]}],
            "claims": [{"claim_id": "c1", "scope": "narration", "scene_id": "s1", "surface_text": fact,
                        "text": fact, "references": [{"dataset_id": "demo-v1", "material_id": "P01",
                                                       "fact_id": "F01", "evidence_quote": fact, "synthetic": True}]}]}


def call(name, args, context, call_id="call-1"):
    return execute_tool(call_id, name, args, *context)


def test_search_and_empty_results(context):
    found = call("search_materials", {"query": "字幕", "product_id": "demo-editor"}, context)
    assert found.ok and found.data["matches"][0]["material"]["material_id"] == "P01"
    empty = call("search_materials", {"query": "量子火箭", "product_id": "demo-editor"}, context)
    assert empty.ok and empty.data["matches"] == []
    bad = call("search_materials", {"query": "字幕", "product_id": "other"}, context)
    assert bad.error.code == "INVALID_ARGUMENT"


def test_chinese_compound_query_finds_literal_subphrase(context):
    result = call("search_materials", {"query": "旅行视频字幕", "product_id": "demo-editor"}, context)
    assert result.ok
    assert any(match["material"]["material_id"] == "P01" for match in result.data["matches"])


def test_duration_rounding_and_ignores_self_reported_estimate(context):
    item = draft(hold=0)
    item["scenes"][0]["narration"] = "你好！1 A"
    item["scenes"][0]["estimated_duration_seconds"] = 999
    result = call("estimate_duration", {"draft": item, "target_duration_seconds": 15}, context)
    assert result.ok
    assert result.data["scenes"][0]["char_count"] == 4
    assert result.data["scenes"][0]["speech_seconds"] == 0.89
    assert result.data["total_seconds"] == 0.89
    assert result.data["status"] == "too_short"


def test_rule_references_coverage_and_forbidden_phrase(context):
    clean = call("check_rules", {"draft": draft(), "rule_set_id": "demo-brand-v1"}, context)
    assert clean.ok and clean.data["passed"]
    missing = draft()
    missing["claims"][0]["references"] = []
    failed = call("check_rules", {"draft": missing, "rule_set_id": "demo-brand-v1"}, context)
    assert failed.ok and {f["code"] for f in failed.data["findings"]} == {"MISSING_REFERENCE"}
    uncovered = draft()
    uncovered["title"] = "演示剪辑器：旅行记忆"
    review = call("check_rules", {"draft": uncovered, "rule_set_id": "demo-brand-v1"}, context)
    assert review.data["status"] == "needs_review"
    forbidden = draft()
    forbidden["title"] = "全网第一旅行记忆"
    result = call("check_rules", {"draft": forbidden, "rule_set_id": "demo-brand-v1"}, context)
    assert result.data["status"] == "failed"


class FakeClient:
    model = "offline-controlled-stub"

    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = 0

    def complete(self, messages, tools, timeout):
        self.calls += 1
        assert {tool["function"]["name"] for tool in tools} == {"search_materials", "estimate_duration", "check_rules"}
        return {"id": f"fake-{self.calls}", "choices": [{"message": next(self.replies)}]}


def tool_reply(name, args, call_id):
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": call_id, "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}


def decision(action, draft_id=None):
    return {"role": "assistant", "content": json.dumps({"action": action, "draft_id": draft_id})}


def test_controller_completes_only_after_same_draft_checks(context, tmp_path, monkeypatch):
    first = draft(hold=30)
    revised = draft("d2", hold=15)
    fake = FakeClient([
        tool_reply("search_materials", {"query": "字幕", "product_id": "demo-editor"}, "c-search"),
        tool_reply("estimate_duration", {"draft": first, "target_duration_seconds": 15}, "c-long"),
        tool_reply("check_rules", {"draft": first, "rule_set_id": "demo-brand-v1"}, "c-rules-old"),
        decision("complete", "d1"),  # refused because duration is too long
        tool_reply("estimate_duration", {"draft": revised, "target_duration_seconds": 15}, "c-duration-new"),
        decision("complete", "d2"),  # refused because old rules check is stale
        tool_reply("check_rules", {"draft": revised, "rule_set_id": "demo-brand-v1"}, "c-rules-new"),
        decision("complete", "d2"),
    ])
    trace, final, duration, rules = Agent(fake).run(context[0])
    assert trace.status == "completed" and final.draft_id == "d2"
    assert duration["status"] == "within_tolerance" and rules["passed"]
    assert sum(e.kind == "decision" and "拒绝完成" in e.summary for e in trace.events) == 2
    monkeypatch.setattr("src.cli.ROOT", tmp_path)
    paths = export_run(trace, final, duration, rules, tmp_path / "outputs", tmp_path / "traces")
    assert len(paths) == 3
    assert json.loads(paths[0].read_text(encoding="utf-8"))["draft"]["draft_id"] == "d2"
    assert "合成证据" in paths[1].read_text(encoding="utf-8")
    assert json.loads(paths[2].read_text(encoding="utf-8"))["status"] == "completed"
    schema = json.loads((ROOT / "docs/schemas/contracts.schema.json").read_text(encoding="utf-8"))
    def validate(name, value):
        Draft202012Validator({"$defs": schema["$defs"], "$ref": f"#/$defs/{name}"},
                             format_checker=FormatChecker()).validate(value)
    validate("SyntheticMaterials", context[1].model_dump())
    validate("SyntheticRuleSet", context[2].model_dump(exclude_none=True))
    validate("ScriptDraft", json.loads(paths[0].read_text(encoding="utf-8"))["draft"])
    trace_payload = json.loads(paths[2].read_text(encoding="utf-8"))
    validate("AgentTrace", trace_payload)
    for item in trace_payload["events"]:
        if item["kind"] == "tool_result":
            validate("ToolResult", item["tool_result"])
    with pytest.raises(FileExistsError):
        export_run(trace, final, duration, rules, tmp_path / "outputs", tmp_path / "traces")


def test_invalid_draft_id_reuse_and_budget(context):
    changed = draft(hold=12)
    fake = FakeClient([
        tool_reply("search_materials", {"query": "字幕", "product_id": "demo-editor"}, "c-search"),
        tool_reply("estimate_duration", {"draft": draft(), "target_duration_seconds": 15}, "c1"),
        tool_reply("estimate_duration", {"draft": changed, "target_duration_seconds": 15}, "c2"),
    ])
    trace, final, _, _ = Agent(fake, max_model_requests=3).run(context[0])
    assert trace.status == "limit_reached" and final is None
    assert any(e.kind == "decision" and "未执行工具" in e.summary and "INVALID_ARGUMENT" in e.summary
               for e in trace.events)


def test_unknown_tool_and_invalid_arguments_are_not_executed(context):
    unknown = call("delete_files", {}, context)
    assert not unknown.ok and unknown.error.code == "UNKNOWN_TOOL"
    malformed = call("search_materials", {"query": "  ", "product_id": "demo-editor"}, context)
    assert not malformed.ok and malformed.error.code == "INVALID_ARGUMENT"


def test_model_failure_creates_trace_error_without_tool_result(context):
    class FailingClient:
        model = "offline-failing-stub"

        def complete(self, messages, tools, timeout):
            raise ModelError("MODEL_AUTH_ERROR")

    trace, final, _, _ = Agent(FailingClient()).run(context[0])
    assert trace.status == "failed" and final is None
    assert any(e.kind == "error" for e in trace.events)
    assert not any(e.kind == "tool_result" for e in trace.events)


def test_completion_requires_material_actually_returned_by_search(context):
    fake = FakeClient([
        tool_reply("estimate_duration", {"draft": draft(), "target_duration_seconds": 15}, "c-duration"),
        tool_reply("check_rules", {"draft": draft(), "rule_set_id": "demo-brand-v1"}, "c-rules"),
        decision("complete", "d1"),
    ])
    trace, final, _, _ = Agent(fake, max_model_requests=3).run(context[0])
    assert trace.status == "limit_reached" and final is None
    assert any(e.kind == "decision" and "拒绝完成" in e.summary for e in trace.events)


def test_cli_missing_field_without_credentials(monkeypatch, capsys):
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    code = main(["--input", str(ROOT / "examples/inputs/missing-duration.json")])
    assert code == 2
    assert "target_duration_seconds" in capsys.readouterr().err


def test_cli_valid_input_requires_local_credentials(monkeypatch, capsys):
    monkeypatch.setattr("src.cli.load_dotenv", lambda *args, **kwargs: False)
    for name in ("QWEN_API_KEY", "QWEN_BASE_URL", "QWEN_MODEL"):
        monkeypatch.delenv(name, raising=False)
    code = main(["--input", str(ROOT / "examples/inputs/normal.json")])
    assert code == 2
    assert "请在 .env 配置" in capsys.readouterr().err
