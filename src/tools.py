"""Deterministic local tools. Model arguments never become file paths."""
from __future__ import annotations

import json
import re
import time
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from pydantic import ValidationError

from .models import (
    CheckRulesArgs, EstimateDurationArgs, ScriptDraft, SearchMaterialsArgs,
    SyntheticMaterials, SyntheticRuleSet, ToolError, ToolResult, UserRequest,
)

ROOT = Path(__file__).resolve().parents[1]
MATERIALS_PATH = ROOT / "data" / "materials.json"
RULES_PATH = ROOT / "data" / "rules.json"
ARG_MODELS = {
    "search_materials": SearchMaterialsArgs,
    "estimate_duration": EstimateDurationArgs,
    "check_rules": CheckRulesArgs,
}


class DataError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def load_data() -> tuple[SyntheticMaterials, list[SyntheticRuleSet]]:
    try:
        materials = SyntheticMaterials.model_validate(json.loads(MATERIALS_PATH.read_text(encoding="utf-8")))
        raw_rules = json.loads(RULES_PATH.read_text(encoding="utf-8"))
        rules = [SyntheticRuleSet.model_validate(raw_rules)]
        ids = [rule.rule_set_id for rule in rules]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate rule_set_id")
        return materials, rules
    except FileNotFoundError as exc:
        raise DataError("DATA_NOT_FOUND") from exc
    except (json.JSONDecodeError, UnicodeError, ValidationError, ValueError) as exc:
        raise DataError("DATA_INVALID") from exc
    except OSError as exc:
        raise DataError("IO_ERROR") from exc


def resolve_request(request: UserRequest, materials: SyntheticMaterials, rules: list[SyntheticRuleSet]) -> SyntheticRuleSet:
    if not any(m.product_id == request.product_id for m in materials.materials):
        raise DataError("DATA_NOT_FOUND")
    candidates = [r for r in rules if r.product_id == request.product_id]
    if request.brand_rule_set_id:
        candidates = [r for r in candidates if r.rule_set_id == request.brand_rule_set_id]
    if len(candidates) != 1:
        raise DataError("DATA_NOT_FOUND")
    return candidates[0]


def search_materials(args: SearchMaterialsArgs, request: UserRequest, materials: SyntheticMaterials) -> dict:
    query = args.query.strip().casefold()
    chunks = re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", query)
    tokens = []
    for chunk in chunks:
        if '\u3400' <= chunk[0] <= '\u9fff' and len(chunk) > 2:
            tokens.extend(chunk[i:i + 2] for i in range(len(chunk) - 1))
        else:
            tokens.append(chunk)
    scored = []
    for material in materials.materials:
        if material.product_id != args.product_id:
            continue
        if material.applicable_platforms and request.platform not in material.applicable_platforms:
            continue
        if not all(tag in material.tags for tag in args.tags):
            continue
        haystack = " ".join([material.title, material.summary, *material.tags, *(f.text for f in material.facts)]).casefold()
        hits = sum(token in haystack for token in tokens)
        if not hits and query not in haystack:
            continue
        score = min(1.0, round((hits / max(len(tokens), 1)) * 0.8 + (0.2 if query in haystack else 0), 4))
        scored.append({"material": material.model_dump(), "score": score})
    scored.sort(key=lambda match: (-match["score"], match["material"]["material_id"]))
    matches = scored[:args.top_k]
    return {"dataset_id": materials.dataset_id, "query": args.query, "matches": matches,
            "warnings": [] if matches else ["未检索到匹配的合成素材；可改写查询，不能编造事实。"]}


def _round(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def estimate_duration(args: EstimateDurationArgs) -> dict:
    cps = Decimal(str(args.chars_per_second))
    transition = Decimal(str(args.transition_seconds))
    scenes = []
    total = Decimal(0)
    for scene in args.draft.scenes:
        count = sum(ch.isalnum() for ch in scene.narration)
        speech = _round(Decimal(count) / cps)
        effective = _round(max(Decimal(count) / cps, Decimal(str(scene.visual_hold_seconds))))
        total += effective
        scenes.append({"scene_id": scene.scene_id, "char_count": count,
                       "speech_seconds": float(speech), "effective_seconds": float(effective)})
    total = _round(total + (len(scenes) - 1) * transition)
    if total > 10000:
        raise ValueError("estimated duration exceeds 10000 seconds")
    lower = Decimal(args.target_duration_seconds) * (1 - Decimal(str(args.tolerance_ratio)))
    upper = Decimal(args.target_duration_seconds) * (1 + Decimal(str(args.tolerance_ratio)))
    status = "too_short" if total < lower else "too_long" if total > upper else "within_tolerance"
    return {"draft_id": args.draft.draft_id, "target_duration_seconds": args.target_duration_seconds,
            "chars_per_second": args.chars_per_second, "transition_seconds": args.transition_seconds,
            "tolerance_ratio": args.tolerance_ratio, "scenes": scenes, "total_seconds": float(total),
            "delta_seconds": float(_round(total - Decimal(args.target_duration_seconds))), "status": status,
            "warnings": ["字符数和画面停留时长仅为启发式估算，不等于实测配音时长；数字念法、语气和停顿未精确建模。"]}


def check_rules(args: CheckRulesArgs, request: UserRequest, materials: SyntheticMaterials,
                rule_set: SyntheticRuleSet) -> dict:
    draft = args.draft
    fields = [("title", None, draft.title), ("concept", None, draft.concept)]
    for scene in draft.scenes:
        fields.extend((scope, scene.scene_id, getattr(scene, scope)) for scope in ("visual", "narration", "subtitle"))
    findings = []

    def add(code: str, severity: str, scope: str, scene_id: str | None = None,
            claim_id: str | None = None, rule_id: str | None = None, message: str = "需修正或复核") -> None:
        findings.append({"finding_id": f"finding-{len(findings)+1}", "rule_id": rule_id,
                         "code": code, "severity": severity, "scope": scope,
                         "scene_id": scene_id, "claim_id": claim_id, "message": message})

    for rule in rule_set.rules:
        if rule.type == "forbidden_phrase":
            for scope, scene_id, value in fields:
                if scope in rule.scopes:
                    for phrase in rule.phrases:
                        if phrase in value:
                            add("FORBIDDEN_PHRASE", rule.severity, scope, scene_id, rule_id=rule.rule_id,
                                message=rule.message)
        elif rule.type == "required_phrase":
            for phrase in rule.phrases:
                if not any(scope in rule.scopes and phrase in value for scope, _, value in fields):
                    add("REQUIRED_PHRASE_MISSING", rule.severity, "document", rule_id=rule.rule_id,
                        message=rule.message)

    claim_map = {claim.claim_id: claim for claim in draft.claims}
    for scene in draft.scenes:
        expected = {c.claim_id for c in draft.claims if c.scene_id == scene.scene_id}
        if set(scene.claim_ids) != expected:
            add("INVALID_LINK", "error", "document", scene.scene_id, message="分镜 claim_ids 与关联声明不一致")
    fact_index = {(m.material_id, f.fact_id): (m, f) for m in materials.materials for f in m.facts}
    covered: dict[tuple[str, str | None], list[str]] = {}
    for claim in draft.claims:
        location = (claim.scope, claim.scene_id)
        value = next((text for scope, scene_id, text in fields if (scope, scene_id) == location), None)
        if value is None or claim.surface_text not in value:
            add("SCOPE_MISMATCH", "error", claim.scope, claim.scene_id, claim.claim_id,
                message="声明片段未出现在指定字段")
        else:
            covered.setdefault(location, []).append(claim.surface_text)
        if claim.surface_text != claim.text:
            add("FACT_MISMATCH", "error", claim.scope, claim.scene_id, claim.claim_id,
                message="声明片段与原子事实不一致")
        if not claim.references:
            add("MISSING_REFERENCE", "error", claim.scope, claim.scene_id, claim.claim_id,
                message="事实声明缺少引用")
        for ref in claim.references:
            item = fact_index.get((ref.material_id, ref.fact_id))
            if ref.dataset_id != materials.dataset_id or item is None:
                add("UNKNOWN_REFERENCE", "error", claim.scope, claim.scene_id, claim.claim_id,
                    message="引用 ID 不在本次素材快照中")
                continue
            material, fact = item
            if material.product_id != request.product_id or (material.applicable_platforms and request.platform not in material.applicable_platforms):
                add("INVALID_LINK", "error", claim.scope, claim.scene_id, claim.claim_id,
                    message="引用产品或平台不适用")
            if claim.text != fact.text or ref.evidence_quote != fact.text:
                add("FACT_MISMATCH", "error", claim.scope, claim.scene_id, claim.claim_id,
                    message="声明或引用原文与素材事实不一致")
            if material.limitations:
                add("CLAIM_COVERAGE_REVIEW", "review", claim.scope, claim.scene_id, claim.claim_id,
                    message="引用素材有人工限制说明，需复核适用性")

    # Literal coverage is conservative: remove exact declared spans, then flag remaining
    # appearances of the demo product name or known fact text for human review.
    product_names = {m.title.split("·")[0] for m in materials.materials if m.product_id == request.product_id}
    product_names = {name for name in product_names if "演示剪辑器" in name}
    product_names.add("演示剪辑器")
    known_texts = {f.text for m in materials.materials if m.product_id == request.product_id for f in m.facts}
    for scope, scene_id, value in fields:
        remainder = value
        for span in covered.get((scope, scene_id), []):
            remainder = remainder.replace(span, "", 1)
        if any(text in remainder for text in known_texts) or any(name in remainder for name in product_names):
            add("CLAIM_COVERAGE_REVIEW", "review", scope, scene_id,
                message="发现未由 Claim 覆盖的疑似产品事实，需人工复核")
    status = "failed" if any(f["severity"] == "error" for f in findings) else "needs_review" if any(f["severity"] == "review" for f in findings) else "passed"
    return {"draft_id": draft.draft_id, "rule_set_id": rule_set.rule_set_id, "status": status,
            "passed": status == "passed", "findings": findings,
            "checked_claim_ids": list(claim_map),
            "warnings": ["自动检查只覆盖字面规则与保守原子事实；无法证明任意自然语言断言均被识别。"]}


def execute_tool(call_id: str, name: str, raw_args: object, request: UserRequest,
                 materials: SyntheticMaterials, rule_set: SyntheticRuleSet) -> ToolResult:
    start = time.monotonic()
    data = None
    error = None
    try:
        model = ARG_MODELS.get(name)
        if model is None:
            raise DataError("UNKNOWN_TOOL")
        args = model.model_validate(raw_args)
        if name == "search_materials":
            if args.product_id != request.product_id:
                raise ValueError("product_id must match request")
            data = search_materials(args, request, materials)
        else:
            draft: ScriptDraft = args.draft
            if draft.request_id != request.request_id or draft.product_id != request.product_id:
                raise ValueError("draft request/product mismatch")
            if name == "estimate_duration":
                if args.target_duration_seconds != request.target_duration_seconds:
                    raise ValueError("target duration must match request")
                data = estimate_duration(args)
            else:
                if args.rule_set_id != rule_set.rule_set_id:
                    raise ValueError("rule_set_id must match trusted rule set")
                data = check_rules(args, request, materials, rule_set)
    except ValidationError as exc:
        error = ToolError(code="INVALID_ARGUMENT", message="工具参数不符合契约", retryable=False,
                          field_errors=[{"path": "/" + "/".join(map(str, item["loc"])), "message": item["type"]} for item in exc.errors()])
    except ValueError:
        error = ToolError(code="INVALID_ARGUMENT", message="工具参数或草稿绑定无效", retryable=False)
    except DataError as exc:
        error = ToolError(code=exc.code, message="工具或可信数据不可用", retryable=exc.code == "IO_ERROR")
    except Exception:
        error = ToolError(code="INTERNAL_ERROR", message="本地工具内部错误", retryable=False)
    return ToolResult(call_id=call_id, tool=name, ok=error is None, data=data, error=error,
                      elapsed_ms=max(0, int((time.monotonic() - start) * 1000)))
