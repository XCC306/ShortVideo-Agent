"""CLI and safe export for synthetic scripts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from collections.abc import Sequence

from dotenv import load_dotenv
from pydantic import ValidationError

from .agent import Agent, QwenClient
from .models import AgentTrace, ScriptDraft, UserRequest
from .tools import ROOT


def render_markdown(draft: ScriptDraft, duration: dict, rules: dict) -> str:
    lines = [f"# {draft.title}", "", "> 合成演示素材生成；不代表小影科技或任何真实产品能力。", "",
             f"- 草稿 ID：`{draft.draft_id}`", f"- 估计时长：{duration['total_seconds']:.2f} 秒（{duration['status']}）",
             f"- 规则检查：{rules['status']}", "", "## 创意", "", draft.concept, "", "## 分镜", ""]
    scene_durations = {s["scene_id"]: s for s in duration["scenes"]}
    claims = {c.claim_id: c for c in draft.claims}
    for scene in draft.scenes:
        measured = scene_durations[scene.scene_id]
        lines.extend([f"### {scene.order}. {scene.scene_id}（{measured['effective_seconds']:.2f} 秒）", "",
                      f"- 画面：{scene.visual}", f"- 旁白：{scene.narration}", f"- 字幕：{scene.subtitle}",
                      f"- 计划停留：{scene.visual_hold_seconds:.2f} 秒", ""])
        for claim_id in scene.claim_ids:
            claim = claims[claim_id]
            refs = ", ".join(f"{r.dataset_id}/{r.material_id}/{r.fact_id}" for r in claim.references)
            lines.append(f"  - 事实 `{claim_id}`：{claim.text}（合成证据：{refs}）")
        if scene.claim_ids:
            lines.append("")
    document_claims = [c for c in draft.claims if c.scene_id is None]
    if document_claims:
        lines.extend(["## 标题与创意中的事实", ""])
        for claim in document_claims:
            refs = ", ".join(f"{r.dataset_id}/{r.material_id}/{r.fact_id}" for r in claim.references)
            lines.append(f"- `{claim.claim_id}` {claim.text}（合成证据：{refs}）")
        lines.append("")
    lines.extend(["## 检查说明", "", *[f"- {warning}" for warning in duration["warnings"] + rules["warnings"]], ""])
    return "\n".join(lines)


def _inside_project(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT):
        raise ValueError("输出目录必须位于当前项目内")
    return resolved


def export_run(trace: AgentTrace, draft: ScriptDraft | None, duration: dict | None,
               rules: dict | None, output_dir: Path, trace_dir: Path) -> list[Path]:
    output_dir = _inside_project(output_dir)
    trace_dir = _inside_project(trace_dir)
    paths = [trace_dir / f"{trace.run_id}.trace.json"]
    if trace.status == "completed":
        paths = [output_dir / f"{trace.run_id}.json", output_dir / f"{trace.run_id}.md", *paths]
    if any(path.exists() for path in paths):
        raise FileExistsError("输出文件名已存在，拒绝覆盖")
    for directory in {path.parent for path in paths}:
        directory.mkdir(parents=True, exist_ok=True)
    trace_payload = trace.model_dump(mode="json")
    for item in trace_payload["events"]:
        for key in ("model_request_id", "tool_call", "tool_result", "draft", "error"):
            if item.get(key) is None:
                item.pop(key, None)
    contents = [json.dumps(trace_payload, ensure_ascii=False, indent=2)]
    if trace.status == "completed":
        contents = [json.dumps({"run_id": trace.run_id, "draft": draft.model_dump(),
                                "duration_check": duration, "rule_check": rules}, ensure_ascii=False, indent=2),
                    render_markdown(draft, duration, rules), *contents]
    written = []
    try:
        for path, content in zip(paths, contents):
            with path.open("x", encoding="utf-8") as stream:
                stream.write(content + "\n")
            written.append(path)
    except OSError:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return paths


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="合成素材驱动的 Qwen 短视频脚本 Agent")
    parser.add_argument("--input", required=True, metavar="PATH", help="UTF-8 UserRequest JSON 文件")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "examples" / "outputs")
    parser.add_argument("--trace-dir", type=Path, default=ROOT / "examples" / "traces")
    args = parser.parse_args(argv)
    try:
        raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
        request = UserRequest.model_validate(raw)
    except ValidationError as exc:
        for error in exc.errors():
            print(f"输入字段 {'/'.join(map(str, error['loc']))}: {error['msg']}", file=sys.stderr)
        return 2
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"输入文件无效：{type(exc).__name__}", file=sys.stderr)
        return 2
    try:
        _inside_project(args.output_dir)
        _inside_project(args.trace_dir)
        load_dotenv(ROOT / ".env", override=False)
        client = QwenClient.from_environment()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    trace, draft, duration, rules = Agent(client).run(request)
    try:
        paths = export_run(trace, draft, duration, rules, args.output_dir, args.trace_dir)
    except OSError as exc:
        print(f"导出失败：{type(exc).__name__}", file=sys.stderr)
        return 1
    print(f"status={trace.status} run_id={trace.run_id} reason={trace.stop_reason}")
    for path in paths:
        print(path)
    return 0 if trace.status == "completed" else 1
