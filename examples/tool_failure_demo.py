"""Offline fault-injection evidence. Does not load credentials or call Qwen."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

from src.agent import Agent
from src.cli import export_run
from src.models import UserRequest

ROOT = Path(__file__).resolve().parents[1]


class ControlledFailureClient:
    model = "offline-tool-failure-injection"

    def __init__(self, product_id: str):
        self.product_id = product_id
        self.calls = 0
        self.observed_failure = False

    def complete(self, messages, tools, timeout):
        self.calls += 1
        if self.calls == 1:
            message = {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "offline-fault-search-01", "type": "function", "function": {
                    "name": "search_materials",
                    "arguments": json.dumps({"query": "字幕", "product_id": self.product_id}, ensure_ascii=False),
                }}],
            }
        elif self.calls == 2:
            result = json.loads(next(m["content"] for m in reversed(messages) if m["role"] == "tool"))
            assert result["ok"] is False and result["error"]["code"] == "INTERNAL_ERROR"
            self.observed_failure = True
            message = {"role": "assistant", "content": json.dumps({
                "action": "needs_input", "reason": "本地检索工具失败，需要修复后再继续。"
            }, ensure_ascii=False)}
        else:
            raise AssertionError("Unexpected extra call in controlled scenario")
        return {"id": f"offline-fault-response-{self.calls}", "choices": [{"message": message}]}


def run_controlled_failure(request: UserRequest):
    client = ControlledFailureClient(request.product_id)
    # Inject an actual exception at the tool function boundary. The production
    # executor and controller perform their real error handling in this process.
    with patch("src.tools.search_materials", side_effect=OSError("controlled local read fault")):
        trace, draft, duration, rules = Agent(client).run(request)
    errors = [event.tool_result for event in trace.events
              if event.kind == "tool_result" and event.tool_result and not event.tool_result.ok]
    assert trace.status == "needs_input" and draft is None and duration is None and rules is None
    assert client.observed_failure and len(errors) == 1
    return trace, {
        "record_kind": "executed_offline_fault_injection",
        "request_id": request.request_id,
        "model": client.model,
        "fault": "OSError injected at src.tools.search_materials; production handler returned INTERNAL_ERROR",
        "status": trace.status,
        "script": None,
        "model_observed_error": client.observed_failure,
        "tool_results": [error.model_dump(mode="json") for error in errors],
        "fresh_qwen_requests": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "examples/inputs/tool-failure.json")
    args = parser.parse_args()
    request = UserRequest.model_validate_json(args.input.read_text(encoding="utf-8"))
    trace, result = run_controlled_failure(request)
    trace_path = export_run(trace, None, None, None, ROOT / "examples/outputs", ROOT / "examples/traces")[0]
    result["source_trace"] = trace_path.relative_to(ROOT).as_posix()
    result_path = ROOT / "examples/outputs" / f"{trace.run_id}.tool-failure.result.json"
    with result_path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": trace.status, "result": result_path.relative_to(ROOT).as_posix(),
                      "trace": trace_path.relative_to(ROOT).as_posix(), "fresh_qwen_requests": 0}))
    return 0  # Expected fault handling was verified; not a completed script.


if __name__ == "__main__":
    raise SystemExit(main())
