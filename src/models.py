"""Version 1.0 runtime contracts and cross-field validation."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
Id = str
Scope = Literal["title", "concept", "visual", "narration", "subtitle"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class UserRequest(StrictModel):
    schema_version: Literal["1.0"]
    request_id: str = Field(pattern=ID)
    topic: str = Field(min_length=1, max_length=300)
    audience: str = Field(min_length=1, max_length=300)
    platform: str = Field(min_length=1, max_length=80)
    target_duration_seconds: int = Field(ge=5, le=180, strict=True)
    style: str = Field(min_length=1, max_length=300)
    product_id: str = Field(pattern=ID)
    language: Literal["zh-CN"]
    brand_rule_set_id: str | None = Field(default=None, pattern=ID)

    @field_validator("topic", "audience", "platform", "style")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class MaterialReference(StrictModel):
    dataset_id: str = Field(pattern=ID)
    material_id: str = Field(pattern=ID)
    fact_id: str = Field(pattern=ID)
    evidence_quote: str = Field(min_length=1, max_length=1000)
    synthetic: Literal[True]


class Claim(StrictModel):
    claim_id: str = Field(pattern=ID)
    scope: Scope
    scene_id: str | None = Field(pattern=ID)
    surface_text: str = Field(min_length=1, max_length=1000)
    text: str = Field(min_length=1, max_length=1000)
    references: list[MaterialReference] = Field(max_length=5)

    @model_validator(mode="after")
    def validate_scope(self) -> "Claim":
        if (self.scope in ("title", "concept")) != (self.scene_id is None):
            raise ValueError("scene_id must be null exactly for title/concept")
        return self


class Scene(StrictModel):
    scene_id: str = Field(pattern=ID)
    order: int = Field(ge=1, le=30, strict=True)
    visual: str = Field(min_length=1, max_length=2000)
    narration: str = Field(max_length=3000)
    subtitle: str = Field(max_length=3000)
    visual_hold_seconds: float = Field(ge=0, le=180)
    estimated_duration_seconds: float | None = Field(ge=0, le=10000)
    claim_ids: list[str] = Field(max_length=200)

    @field_validator("claim_ids")
    @classmethod
    def unique_claim_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate claim_ids")
        return value


class ScriptDraft(StrictModel):
    schema_version: Literal["1.0"]
    draft_id: str = Field(pattern=ID)
    request_id: str = Field(pattern=ID)
    product_id: str = Field(pattern=ID)
    title: str = Field(min_length=1, max_length=300)
    concept: str = Field(min_length=1, max_length=2000)
    scenes: list[Scene] = Field(min_length=1, max_length=30)
    claims: list[Claim] = Field(max_length=200)

    @model_validator(mode="after")
    def unique_and_ordered(self) -> "ScriptDraft":
        scene_ids = [s.scene_id for s in self.scenes]
        claim_ids = [c.claim_id for c in self.claims]
        if len(scene_ids) != len(set(scene_ids)) or len(claim_ids) != len(set(claim_ids)):
            raise ValueError("duplicate scene_id or claim_id")
        if [s.order for s in self.scenes] != list(range(1, len(self.scenes) + 1)):
            raise ValueError("scene order must be continuous starting at 1")
        return self


class MaterialFact(StrictModel):
    fact_id: str = Field(pattern=ID)
    text: str = Field(min_length=1, max_length=1000)


class Material(StrictModel):
    material_id: str = Field(pattern=ID)
    product_id: str = Field(pattern=ID)
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=2000)
    tags: list[str] = Field(max_length=30)
    applicable_platforms: list[str] = Field(max_length=30)
    limitations: list[str] = Field(max_length=30)
    facts: list[MaterialFact] = Field(min_length=1, max_length=50)
    synthetic: Literal[True]

    @model_validator(mode="after")
    def unique_facts(self) -> "Material":
        ids = [f.fact_id for f in self.facts]
        if len(ids) != len(set(ids)) or len(self.tags) != len(set(self.tags)):
            raise ValueError("duplicate fact_id or tag")
        return self


class SyntheticMaterials(StrictModel):
    schema_version: Literal["1.0"]
    dataset_id: str = Field(pattern=ID)
    synthetic: Literal[True]
    notice: str = Field(min_length=1, max_length=1000)
    materials: list[Material] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_materials(self) -> "SyntheticMaterials":
        ids = [m.material_id for m in self.materials]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate material_id")
        return self


class BrandRule(StrictModel):
    rule_id: str = Field(pattern=ID)
    type: Literal["forbidden_phrase", "required_phrase", "claim_references"]
    severity: Literal["error", "review", "warning"]
    message: str = Field(min_length=1, max_length=1000)
    phrases: list[str] | None = None
    scopes: list[Scope] | None = None

    @model_validator(mode="after")
    def validate_type(self) -> "BrandRule":
        if self.type == "claim_references":
            if self.severity != "error" or self.phrases is not None or self.scopes is not None:
                raise ValueError("invalid claim_references rule")
        elif not self.phrases or not self.scopes or len(set(self.phrases)) != len(self.phrases):
            raise ValueError("phrase rule requires unique phrases and scopes")
        return self


class SyntheticRuleSet(StrictModel):
    schema_version: Literal["1.0"]
    dataset_id: str = Field(pattern=ID)
    rule_set_id: str = Field(pattern=ID)
    product_id: str = Field(pattern=ID)
    synthetic: Literal[True]
    notice: str = Field(min_length=1, max_length=1000)
    rules: list[BrandRule] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_rules(self) -> "SyntheticRuleSet":
        ids = [r.rule_id for r in self.rules]
        if len(ids) != len(set(ids)) or not any(r.type == "claim_references" for r in self.rules):
            raise ValueError("duplicate rule_id or missing claim_references")
        return self


class SearchMaterialsArgs(StrictModel):
    query: str = Field(min_length=1, max_length=300)
    product_id: str = Field(pattern=ID)
    top_k: int = Field(default=3, ge=1, le=10, strict=True)
    tags: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_search(self) -> "SearchMaterialsArgs":
        if not self.query.strip() or len(self.tags) != len(set(self.tags)):
            raise ValueError("blank query or duplicate tags")
        return self


class EstimateDurationArgs(StrictModel):
    draft: ScriptDraft
    target_duration_seconds: int = Field(ge=5, le=180, strict=True)
    chars_per_second: float = Field(default=4.5, ge=1, le=10)
    transition_seconds: float = Field(default=0.5, ge=0, le=5)
    tolerance_ratio: float = Field(default=0.1, ge=0, le=0.5)


class CheckRulesArgs(StrictModel):
    draft: ScriptDraft
    rule_set_id: str = Field(pattern=ID)


class ToolError(StrictModel):
    code: Literal["INVALID_ARGUMENT", "UNKNOWN_TOOL", "DATA_NOT_FOUND", "DATA_INVALID", "TIMEOUT", "IO_ERROR", "INTERNAL_ERROR"]
    message: str = Field(min_length=1, max_length=1000)
    retryable: bool
    field_errors: list[dict[str, str]] = Field(default_factory=list)


class ToolResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    call_id: str = Field(min_length=1, max_length=256)
    tool: str = Field(min_length=1, max_length=256)
    ok: bool
    data: dict | None
    error: ToolError | None
    elapsed_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def result_shape(self) -> "ToolResult":
        if self.ok != (self.data is not None) or self.ok == (self.error is not None):
            raise ValueError("result data/error mismatch")
        return self


class TraceEvent(StrictModel):
    seq: int = Field(ge=1)
    at: datetime
    kind: Literal["llm_request", "llm_response", "tool_call", "tool_result", "draft", "decision", "error", "finish"]
    summary: str = Field(min_length=1, max_length=2000)
    model_request_id: str | None = None
    tool_call: dict | None = None
    tool_result: ToolResult | None = None
    draft: ScriptDraft | None = None
    error: dict | None = None


class AgentTrace(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(pattern=ID)
    request_id: str = Field(pattern=ID)
    model: str = Field(min_length=1, max_length=200)
    synthetic: Literal[True] = True
    status: Literal["running", "completed", "needs_input", "failed", "limit_reached"]
    started_at: datetime
    ended_at: datetime | None = None
    budget: dict
    usage: dict
    final_draft_id: str | None = None
    stop_reason: str | None = None
    events: list[TraceEvent] = Field(default_factory=list)
