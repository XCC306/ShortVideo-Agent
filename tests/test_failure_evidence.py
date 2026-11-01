"""Verify the third evidence scenario without model requests or disk exports."""
from pathlib import Path

from examples.tool_failure_demo import run_controlled_failure
from src.models import UserRequest


def test_runtime_tool_failure_is_recorded_and_observed():
    root = Path(__file__).resolve().parents[1]
    request = UserRequest.model_validate_json((root / "examples/inputs/tool-failure.json").read_text(encoding="utf-8"))
    trace, output = run_controlled_failure(request)
    assert trace.model == "offline-tool-failure-injection"
    assert trace.status == "needs_input" and trace.final_draft_id is None
    assert trace.usage["model_requests"] == 2 and trace.usage["tool_calls"] == 1
    assert output["model_observed_error"] and output["fresh_qwen_requests"] == 0
    assert output["tool_results"][0]["error"]["code"] == "INTERNAL_ERROR"
    assert any(event.kind == "decision" and "工具执行失败" in event.summary for event in trace.events)
