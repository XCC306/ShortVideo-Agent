"""Reproducible offline demonstration with scripted model replies; never calls Qwen."""
import json
from pathlib import Path

from src.agent import Agent
from src.cli import export_run
from src.models import UserRequest


ROOT = Path(__file__).resolve().parents[1]
request = UserRequest.model_validate_json((ROOT / "examples/inputs/normal.json").read_text(encoding="utf-8"))
fact = "演示剪辑器支持字幕功能。"
draft = {
    "schema_version": "1.0", "draft_id": "offline-demo-d1", "request_id": request.request_id,
    "product_id": request.product_id, "title": "旅行记忆，配上字幕", "concept": "一段轻快的旅行回忆。",
    "scenes": [{"scene_id": "s1", "order": 1, "visual": "旅途片段与轻快字幕",
                "narration": fact, "subtitle": "旅行记忆", "visual_hold_seconds": 15,
                "estimated_duration_seconds": None, "claim_ids": ["c1"]}],
    "claims": [{"claim_id": "c1", "scope": "narration", "scene_id": "s1",
                "surface_text": fact, "text": fact,
                "references": [{"dataset_id": "demo-v1", "material_id": "P01", "fact_id": "F01",
                                "evidence_quote": fact, "synthetic": True}]}],
}


def tool(name, args, call_id):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": name,
         "arguments": json.dumps(args, ensure_ascii=False)}}]}


class ScriptedOfflineClient:
    model = "offline-controlled-stub"

    def __init__(self):
        self.replies = iter([
            tool("search_materials", {"query": "字幕", "product_id": request.product_id}, "offline-search"),
            tool("estimate_duration", {"draft": draft, "target_duration_seconds": 15}, "offline-duration"),
            tool("check_rules", {"draft": draft, "rule_set_id": "demo-brand-v1"}, "offline-rules"),
            {"role": "assistant", "content": json.dumps({"action": "complete", "draft_id": draft["draft_id"]})},
        ])

    def complete(self, messages, tools, timeout):
        return {"id": "offline-scripted-response", "choices": [{"message": next(self.replies)}]}


if __name__ == "__main__":
    trace, final, duration, rules = Agent(ScriptedOfflineClient()).run(request)
    if trace.status != "completed":
        raise SystemExit(f"Offline demo failed: {trace.status}")
    for path in export_run(trace, final, duration, rules, ROOT / "examples/outputs", ROOT / "examples/traces"):
        print(path)
