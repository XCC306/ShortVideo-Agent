"""Qwen-compatible Tool Calling transport and bounded single-agent controller."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import urlparse

from pydantic import ValidationError

from .models import AgentTrace, ScriptDraft, TraceEvent, ToolError, ToolResult, UserRequest
from .tools import ARG_MODELS, DataError, execute_tool, load_data, resolve_request


def now() -> datetime:
    return datetime.now(timezone.utc)


class ModelError(Exception):
    def __init__(self, code: str, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(code)


class ModelClient(Protocol):
    model: str

    def complete(self, messages: list[dict], tools: list[dict], timeout: float) -> dict: ...


class QwenClient:
    """Direct use of Alibaba Cloud's OpenAI-compatible chat completions endpoint."""

    def __init__(self, api_key: str, base_url: str, model: str):
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("QWEN_BASE_URL must be an HTTPS API base URL")
        if not model.strip():
            raise ValueError("QWEN_MODEL is required")
        self._key = api_key
        self._url = base_url.rstrip("/") + "/chat/completions"
        self.model = model

    @classmethod
    def from_environment(cls) -> "QwenClient":
        key = os.getenv("QWEN_API_KEY", "")
        url = os.getenv("QWEN_BASE_URL", "")
        model = os.getenv("QWEN_MODEL", "")
        if not all((key, url, model)):
            raise ValueError("请在 .env 配置 QWEN_API_KEY、QWEN_BASE_URL 和 QWEN_MODEL")
        return cls(key, url, model)

    def complete(self, messages: list[dict], tools: list[dict], timeout: float) -> dict:
        payload = json.dumps({"model": self.model, "messages": messages, "tools": tools,
                              "tool_choice": "auto", "stream": False}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self._url, data=payload, method="POST",
                                         headers={"Authorization": "Bearer " + self._key,
                                                  "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read(2_000_001)
            if len(body) > 2_000_000:
                raise ModelError("MODEL_RESPONSE_TOO_LARGE")
            result = json.loads(body)
            if not isinstance(result, dict) or not result.get("choices"):
                raise ModelError("MODEL_RESPONSE_INVALID")
            return result
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ModelError("MODEL_AUTH_ERROR") from None
            if exc.code == 429 or exc.code >= 500:
                raise ModelError("MODEL_TEMPORARY_ERROR", True) from None
            raise ModelError("MODEL_REQUEST_ERROR") from None
        except (TimeoutError, socket.timeout):
            raise ModelError("MODEL_TIMEOUT", True) from None
        except urllib.error.URLError:
            raise ModelError("MODEL_NETWORK_ERROR", True) from None
        except (json.JSONDecodeError, UnicodeError, KeyError, TypeError):
            raise ModelError("MODEL_RESPONSE_INVALID") from None


def _inline_schema(schema: dict) -> dict:
    """Expand local Pydantic refs for providers that reject $defs."""
    defs = schema.pop("$defs", {})

    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            return expand(defs[value["$ref"].split("/")[-1]])
        return {key: expand(item) for key, item in value.items() if key not in ("title", "default")}

    return expand(schema)


TOOL_DEFINITIONS = [
    {"type": "function", "function": {"name": name, "description": description,
                                     "parameters": _inline_schema(model.model_json_schema())}}
    for name, model, description in (
        ("search_materials", ARG_MODELS["search_materials"], "检索当前产品的本地合成素材，返回事实 ID 与完整原文。"),
        ("estimate_duration", ARG_MODELS["estimate_duration"], "对完整草稿计算旁白与画面时长；修改后须用新 draft_id 重估。"),
        ("check_rules", ARG_MODELS["check_rules"], "检查完整草稿的禁用词、引用和事实覆盖；修改后须用新 draft_id 重检。"),
    )
]


def _fingerprint(draft: ScriptDraft) -> str:
    payload = draft.model_dump_json()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Agent:
    def __init__(self, client: ModelClient, *, max_model_requests: int = 10,
                 max_tool_calls: int = 12, max_elapsed_seconds: float = 180,
                 max_retries_per_error: int = 2):
        self.client = client
        self.budget = {"max_model_requests": max_model_requests, "max_tool_calls": max_tool_calls,
                       "max_elapsed_seconds": max_elapsed_seconds,
                       "max_retries_per_error": max_retries_per_error}

    def run(self, request: UserRequest) -> tuple[AgentTrace, ScriptDraft | None, dict | None, dict | None]:
        started = time.monotonic()
        trace = AgentTrace(run_id="run-" + uuid.uuid4().hex[:16], request_id=request.request_id,
                           model=self.client.model, status="running", started_at=now(),
                           budget=self.budget.copy(), usage={"model_requests": 0, "tool_calls": 0, "elapsed_seconds": 0})

        def event(kind: str, summary: str, **fields) -> None:
            trace.events.append(TraceEvent(seq=len(trace.events) + 1, at=now(), kind=kind,
                                           summary=summary, **fields))

        def stop(status: str, reason: str, draft_id: str | None = None) -> None:
            trace.status = status
            trace.stop_reason = reason
            trace.final_draft_id = draft_id if status == "completed" else None
            trace.ended_at = now()
            trace.usage["elapsed_seconds"] = round(time.monotonic() - started, 3)
            event("finish", reason)

        try:
            materials, rules = load_data()
            rule_set = resolve_request(request, materials, rules)
        except DataError as exc:
            event("error", "可信数据不可用", error={"code": exc.code, "message": "可信数据不可用", "retryable": False})
            stop("needs_input" if exc.code == "DATA_NOT_FOUND" else "failed", exc.code)
            return trace, None, None, None

        system = ("你是短视频脚本 Agent。只使用本次提供的 synthetic 合成资料，不将其归于真实产品。"
                  "你自主选择 search_materials、estimate_duration、check_rules 的调用顺序与参数。"
                  "先用主题中的短关键词检索；已有匹配时不要重复检索同一素材。优先写最短且有依据的 1–2 镜脚本。"
                  "不要添加素材未写明的导入、自动排序、自动生成、同步、效果保证等产品能力。"
                  "标题、创意、画面、旁白、字幕中若出现产品相关事实，每个出现位置都要有独立 Claim；"
                  "Claim.surface_text 必须逐字出现在指定 scope 的实际脚本文本里，不能只写在 Claim 中。"
                  "Claim.surface_text == Claim.text == 被引用的 fact.text，evidence_quote 也须与该原文完全相同。"
                  "例如素材 fact.text 为 X 时，可令 narration 恰为 X，并建立 scope=narration、surface_text=X、text=X 的 Claim。"
                  "创意字段尽量不用产品名称或暗示未证明的能力；分镜画面可采用中性旅行素材，避免触发事实覆盖复核。"
                  "每个 Scene.claim_ids 精确列出该镜的 Claim，标题和创意的 Claim 不列入 Scene.claim_ids。"
                  "规划 visual_hold_seconds 时按目标时长 T 预估：一镜可设 T 秒；N 镜可先按 (T-(N-1)*0.5)/N 秒分配，仍须以 estimate_duration 实测结果为准。"
                  "每次改动脚本必须使用新 draft_id。时长和规则都须对同一最新草稿通过后才能结束。"
                  "一旦同一最新草稿的 estimate_duration 为 within_tolerance 且 check_rules 为 passed，请立即返回 complete JSON，不要继续修改。"
                  "工具失败、未检索到资料或需复核时请修正，不能编造事实。"
                  "结束时只回复 JSON：{\"action\":\"complete\",\"draft_id\":\"...\"} 或"
                  " {\"action\":\"needs_input\",\"reason\":\"...\"}。不要输出思维链。")
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": json.dumps({"request": request.model_dump(exclude_none=True),
                        "dataset_id": materials.dataset_id, "rule_set_id": rule_set.rule_set_id,
                        "notice": materials.notice}, ensure_ascii=False)}]
        drafts: dict[str, tuple[str, ScriptDraft]] = {}
        latest_id = None
        duration: dict[str, tuple[str, dict]] = {}
        checks: dict[str, tuple[str, dict]] = {}
        seen_material_ids: set[str] = set()
        retry_counts: dict[str, int] = {}
        final_draft = None
        final_duration = None
        final_check = None

        while trace.status == "running":
            remaining = self.budget["max_elapsed_seconds"] - (time.monotonic() - started)
            if remaining <= 0 or trace.usage["model_requests"] >= self.budget["max_model_requests"]:
                stop("limit_reached", "模型请求数或总耗时达到上限")
                break
            trace.usage["model_requests"] += 1
            event("llm_request", "请求模型选择下一动作")
            try:
                response = self.client.complete(messages, TOOL_DEFINITIONS, min(30.0, remaining))
                message = response["choices"][0]["message"]
                if not isinstance(message, dict):
                    raise ModelError("MODEL_RESPONSE_INVALID")
            except ModelError as exc:
                event("error", "模型请求失败", error={"code": exc.code, "message": "模型请求失败", "retryable": exc.retryable})
                retry_counts[exc.code] = retry_counts.get(exc.code, 0) + 1
                if not exc.retryable or retry_counts[exc.code] > self.budget["max_retries_per_error"]:
                    stop("failed", exc.code)
                continue
            except (KeyError, IndexError, TypeError):
                event("error", "模型响应结构无效", error={"code": "MODEL_RESPONSE_INVALID", "message": "模型响应结构无效", "retryable": False})
                stop("failed", "MODEL_RESPONSE_INVALID")
                break
            request_id = response.get("id") if isinstance(response.get("id"), str) else None
            event("llm_response", "收到模型响应", **({"model_request_id": request_id} if request_id else {}))
            # Keep the provider's assistant message in memory, including tool_calls.
            messages.append(message)
            calls = message.get("tool_calls") or []
            if calls:
                if not isinstance(calls, list):
                    stop("failed", "MODEL_RESPONSE_INVALID")
                    break
                for call in calls:
                    if trace.usage["tool_calls"] >= self.budget["max_tool_calls"] or time.monotonic() - started >= self.budget["max_elapsed_seconds"]:
                        stop("limit_reached", "工具调用数或总耗时达到上限")
                        break
                    name = call.get("function", {}).get("name", "") if isinstance(call, dict) else ""
                    call_id = call.get("id", "") if isinstance(call, dict) else ""
                    raw = call.get("function", {}).get("arguments", "") if isinstance(call, dict) else ""
                    if not isinstance(call_id, str) or not (1 <= len(call_id) <= 256):
                        stop("failed", "MODEL_TOOL_CALL_ID_INVALID")
                        break
                    trace.usage["tool_calls"] += 1
                    try:
                        args = json.loads(raw) if isinstance(raw, str) else raw
                    except (json.JSONDecodeError, TypeError):
                        args = None
                    if name in ("estimate_duration", "check_rules") and isinstance(args, dict) and isinstance(args.get("draft"), dict):
                        try:
                            draft = ScriptDraft.model_validate(args["draft"])
                            if any(scene.estimated_duration_seconds is not None for scene in draft.scenes):
                                raise ValueError("model must not provide measured duration")
                            digest = _fingerprint(draft)
                            previous = drafts.get(draft.draft_id)
                            if previous and previous[0] != digest:
                                raise ValueError("draft_id conflict")
                            if not previous:
                                drafts[draft.draft_id] = (digest, draft)
                                latest_id = draft.draft_id
                                event("draft", "记录不可变草稿快照", draft=draft)
                        except (ValidationError, ValueError):
                            args = None
                    if name in ARG_MODELS and isinstance(args, dict):
                        event("tool_call", f"模型调用 {name}", tool_call={"call_id": call_id, "tool": name, "arguments": args})
                    else:
                        event("decision", f"拒绝无效或未知工具调用：{name[:80]}")
                    result = execute_tool(call_id, name or "invalid", args, request, materials, rule_set)
                    if result.ok and name == "search_materials":
                        seen_material_ids.update(match["material"]["material_id"] for match in result.data["matches"])
                    if result.ok and name in ("estimate_duration", "check_rules"):
                        draft_id = result.data["draft_id"]
                        digest = drafts[draft_id][0]
                        if name == "estimate_duration":
                            duration[draft_id] = (digest, result.data)
                        else:
                            checks[draft_id] = (digest, result.data)
                    event("tool_result", f"{name[:80]}: {'ok' if result.ok else result.error.code}",
                          tool_result=result)
                    if not result.ok:
                        prefix = "未执行工具" if result.error.code in ("INVALID_ARGUMENT", "UNKNOWN_TOOL") else "工具执行失败"
                        event("decision", f"{prefix} {name[:80]}: {result.error.code}")
                    messages.append({"role": "tool", "tool_call_id": call_id,
                                     "content": result.model_dump_json()})
                continue

            content = message.get("content") or ""
            try:
                decision = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                decision = {}
            action = decision.get("action") if isinstance(decision, dict) else None
            if action == "needs_input":
                event("decision", "模型请求补充素材或输入")
                stop("needs_input", "模型判断资料不足，需要补充")
            elif action == "complete":
                wanted = decision.get("draft_id")
                valid = (isinstance(wanted, str) and wanted == latest_id and wanted in drafts
                         and wanted in duration and wanted in checks
                         and duration[wanted][0] == drafts[wanted][0] == checks[wanted][0]
                         and duration[wanted][1]["status"] == "within_tolerance"
                         and checks[wanted][1]["passed"] is True
                         and all(ref.material_id in seen_material_ids
                                 for claim in drafts[wanted][1].claims for ref in claim.references)
                         and checks[wanted][1]["checked_claim_ids"] == [c.claim_id for c in drafts[wanted][1].claims])
                if valid:
                    final_draft = drafts[wanted][1]
                    final_duration = duration[wanted][1]
                    final_check = checks[wanted][1]
                    event("decision", "同一最新草稿通过两项检查")
                    stop("completed", "验收通过", wanted)
                else:
                    event("decision", "拒绝完成：最新草稿缺少同版本通过的时长或规则结果")
                    messages.append({"role": "user", "content": "完成条件未满足。请检索所有引用的素材，并检查最新 draft_id 的时长状态、规则 findings 和事实覆盖；修改时使用新 draft_id，再调用相应工具。"})
            else:
                event("decision", "模型未给出有效终态，继续请求下一动作")
                messages.append({"role": "user", "content": "请调用工具，或仅以 JSON 返回 complete / needs_input。"})

        trace.usage["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return trace, final_draft, final_duration, final_check
