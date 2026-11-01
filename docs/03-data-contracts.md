# 03 数据与工具契约

版本：0.1；日期：2026-10-30；协议版本：`1.0`。
本文及 `schemas/contracts.schema.json` 是协议 1.0 的初始化设计基线。当前模型、工具与 Agent 已实现，验证结果见 [README](../README.md) 和 [验收记录](04-acceptance.md)；涉及“初始化阶段”的描述保留为历史上下文，本文不替代实际代码及验收边界。

## 1. 通用约定

所有持久化文件为 UTF-8 JSON；键名 snake_case；秒数使用有限的非负数。
实体 ID 是 1–64 位 ASCII 字母、数字、下划线或连字符，首位为字母或数字。
时间戳为带时区 RFC 3339，写入时统一 UTC `Z`。
缺少 required 字段为结构错误；显式 null 仅限 Schema 允许的位置，不能用空字符串替代未知值。
禁止未知键；Schema 的 default 仅是文档注释，后续程序必须显式填充默认值后再执行。
示例均是人工构造的设计样例，不是真实生成结果或真实调用记录。

Schema 使用 JSON Schema Draft 2020-12，所有定义位于 `$defs`。
指定单一实体时，使用同文件 URI 的 `#/$defs/UserRequest` 等 fragment；文件根允许主要实体的并集。
`format: date-time` 在运行时需要启用 format 校验。
JSON Schema 不验证引用存在、ID 唯一性、顺序连续、跨快照绑定和语义覆盖；这些是后续程序必须补充的验证。
未配置验证器时，不能宣称已经自动执行这些约束。

## 2. 核心实体

### UserRequest

| 字段 | 类型/约束 | 含义 |
| --- | --- | --- |
| schema_version / request_id | `1.0` / ID | 协议与请求标识 |
| topic / audience / platform / style | 非空字符串 | 主题、受众、平台、风格 |
| target_duration_seconds | 整数 5–180 | 必填，不由模型猜测 |
| product_id | ID | 必须存在于可信素材目录 |
| language | 常量 `zh-CN` | 首版仅中文 |
| brand_rule_set_id | 可选 ID | 缺省由本地产品映射解析；找不到则请求补充 |

### ScriptDraft 与 Scene

ScriptDraft 必含 schema_version、draft_id、request_id、product_id、title、concept、scenes、claims。
`scenes` 为 1–30 个 Scene；`claims` 为 0–200 个 Claim。
每次内容变化分配新 draft_id，request_id/product_id 必须与当前 UserRequest 一致。
同一 run 内 draft_id 对应唯一内容，控制器保存内容指纹以拒绝 ID 冲突。

Scene 必含 scene_id、order（从 1 连续递增）、visual、narration、subtitle、visual_hold_seconds、estimated_duration_seconds、claim_ids。
visual 非空；旁白和字幕可为空；视觉停留时间 0–180 秒。
`estimated_duration_seconds` 是工具产生的展示值或 null，不能把模型估计当成工具结果，也不能作为时长计算依据。
同一草稿内 scene_id 和 claim_id 分别唯一；scene.claim_ids 必须精确列出该镜引用的 Claim。
标题和整体创意中的 Claim 不绑定 scene_id，不出现在分镜 claim_ids 中。

### Claim / MaterialReference

Claim 必含 claim_id、scope、scene_id、surface_text、text、references。
scope 为 title / concept / visual / narration / subtitle；前两类 scene_id=null，其余必须指向存在的分镜。
`surface_text` 必须逐字出现在 scope 指定的字符串中，代表实际断言片段；相同事实出现在不同字段时分别建立 Claim。
`text` 是规范原子事实；首版 `surface_text == text`，并与被引用的 MaterialFact.text 完全一致。
不使用同义词推断或引用某条素材后任意扩大结论。

MaterialReference 必含 dataset_id、material_id、fact_id、evidence_quote、synthetic=true。
`evidence_quote` 必须与素材原子事实全文一致；ID 必须属于本次加载的数据集、当前产品和有效适用范围。
多条 references 都必须支持同一原子事实，不能将无关引用拼接成证据。
结构允许 references 为空，以便规则工具接收并报告 MISSING_REFERENCE；业务验收不允许此类断言通过。
仅仅声明 Claim 不能证明所有事实都已声明：对未覆盖的疑似产品断言必须产生 review finding。

## 3. ToolCall 与 ToolResult

模型看到的三个函数名固定；工具参数取 ToolCall 对应分支的 arguments 定义。
控制器将供应商 tool_call_id 原样映射为 call_id（1–256 字符，不套用业务 ID 正则）。
不把 call_id、可信数据路径、timeout 或执行耗时作为模型可选择的函数参数。
对供应商参数先做 JSON 解析和 Schema 校验；路径只能由控制器通过白名单映射。

ToolResult 所有分支都必含 schema_version、call_id、tool、ok、data、error、elapsed_ms。

| 情况 | ok | data | error |
| --- | --- | --- | --- |
| 执行成功 | true | 与工具名匹配的对象 | null |
| 执行失败 | false | null | ToolError |

耗时 elapsed_ms 为非负整数，包含执行与验证时间。
业务不通过仍为 ok=true，例如零匹配、too_long、规则 failed；只有执行/参数失败使用 ok=false。
失败时的 tool 可保留未知名称，配合 UNKNOWN_TOOL 回传；成功时只能是白名单中的三个名称。
tool_call_id、tool、结果关联关系必须由控制器校验，模型不能伪造执行结果。

ToolError 必含 code、message、retryable、field_errors；field_errors 为 `{path, message}` 数组，JSON Pointer 定位参数。
错误消息必须脱敏，不泄漏本机绝对路径、环境变量、堆栈或凭据。

| code | retryable | 语义/处理 |
| --- | --- | --- |
| INVALID_ARGUMENT | false | 参数、请求绑定或 draft_id 冲突；模型可改参数后发起新调用 |
| UNKNOWN_TOOL | false | 未知工具，未执行 |
| DATA_NOT_FOUND | false | 素材文件/产品/规则集缺失；不等于检索零结果 |
| DATA_INVALID | false | JSON、Schema、重复 ID 或引用数据不合法 |
| TIMEOUT | true | 工具执行超时，可在预算内由模型决定重试 |
| IO_ERROR | true 或 false | 临时 I/O 才为 true；永久权限错误为 false |
| INTERNAL_ERROR | false | 其他未预期错误，记录脱敏事件并停止或反馈 |

retryable=true 只表示允许尝试，不承诺自动重试或会成功。
达到全局预算为运行级 limit_reached，写 Trace，不伪造一次尚未执行的工具成功结果。

## 4. search_materials

参数：query 非空、最多 300 字；product_id 必填；top_k 可选整数 1–10，默认 3；tags 可选去重字符串数组，默认 []。
query 空白串也非法（Schema 之外的去空白校验）。product_id 必须等于请求的可信产品。
tags 非空时按 AND 过滤，空时不限制；query 用固定字面关键词规则匹配 title、summary、facts.text 和 tags。
具体分词实现后须固定在测试中；当前只约定稳定排序和响应结构。

成功 data 必含 dataset_id、query、matches、warnings。
每项 match 含 material（完整 Material 快照）和 0–1 的 score（相关度排序分，不是事实置信概率）。
按 score 降序，分数相同按 material_id 升序；matches 数量不超过 top_k。
没有相关资料时 matches=[]、warnings 可提示改写查询；执行仍成功。
主要错误为 INVALID_ARGUMENT / DATA_NOT_FOUND / DATA_INVALID / IO_ERROR / TIMEOUT。

## 5. estimate_duration

参数：draft（完整 ScriptDraft）、target_duration_seconds（必填，等于 UserRequest）、chars_per_second（可选 1–10，默认 4.5）、transition_seconds（可选 0–5，默认 0.5）、tolerance_ratio（可选 0–0.5，默认 0.1）。
计数仅依据每镜 narration；字幕不重复计数，画面与标题不计入语音。
可计数字符为 Python `str.isalnum()` 为真的 Unicode 字符；汉字、字母、数字各算一个，标点与空白不算。
每镜先计算 speech_seconds=char_count/chars_per_second，effective_seconds=max(speech_seconds, visual_hold_seconds)。
以十进制 ROUND_HALF_UP 保留两位小数；总秒数为各镜已舍入 effective_seconds 之和，加 `(镜数-1)*transition_seconds` 后再同样舍入。
status 以已舍入总秒数与闭区间 `target*(1-tolerance)` 至 `target*(1+tolerance)` 比较：低于为 too_short，高于为 too_long，否则 within_tolerance。

成功 data 含 draft_id、target_duration_seconds、chars_per_second、transition_seconds、tolerance_ratio、scenes、total_seconds、delta_seconds、status、warnings。
delta_seconds=total-target，允许负值。每镜结果含 scene_id、char_count、speech_seconds、effective_seconds。
total_seconds 不超过 10000，限制演示输入和数值范围；异常大输入应返回 INVALID_ARGUMENT。
warnings 必须声明这是字符/停留启发式，不等于实测配音时长；数字念法、语气和停顿未精确建模。
空旁白以 visual_hold_seconds 作为该镜时长；一镜不加转场；不相信草稿自填的 estimated_duration_seconds。
主要错误为 INVALID_ARGUMENT / TIMEOUT / INTERNAL_ERROR；too_long 不是 ToolError。

## 6. check_rules

参数：draft（完整 ScriptDraft）、rule_set_id（必填 ID）。
控制器核对规则集属于请求产品，且是 UserRequest 指定或可信默认映射的规则集。
规则来自本地快照，模型不能传入或修改规则内容。
检查范围为 title、concept 以及所有 Scene 的 visual、narration、subtitle。

成功 data 含 draft_id、rule_set_id、status、passed、findings、checked_claim_ids、warnings。
Finding 必含 finding_id、rule_id（内置检查可为 null）、code、severity、scope、scene_id、claim_id、message。
scope 允许 document（ID/覆盖问题）和五类文本字段；scene_id/claim_id 不适用时为 null。
code 预期包括 FORBIDDEN_PHRASE、REQUIRED_PHRASE_MISSING、MISSING_REFERENCE、UNKNOWN_REFERENCE、FACT_MISMATCH、SCOPE_MISMATCH、CLAIM_COVERAGE_REVIEW、INVALID_LINK。
severity 为 error / review / warning；error 优先得到 status=failed，否则有 review 得 needs_review，否则 passed。
passed 仅在 status=passed 时为 true；warning 不单独阻止通过。
checked_claim_ids 必须覆盖本次草稿全部 claim_id；未检查不能标记通过。

字面规则按原始 Unicode 字符串区分大小写进行子串匹配，不做模糊匹配。
required_phrase 只要在目标范围任一字段命中即满足；forbidden_phrase 在任一字段命中即产生 finding。
claim_references 是不可关闭的内置验收约束，检查 ID、产品、引用原文、事实文本和 scope 绑定。
规则集必须包含此类型规则；即使其他配置错误也不能跳过事实校验。
无法自动确认的事实覆盖产生 review，不能把“已填引用”当成“整篇事实核验通过”。
主要错误为 INVALID_ARGUMENT / DATA_NOT_FOUND / DATA_INVALID / IO_ERROR / TIMEOUT。
发现禁用词、未支持事实或需人工复核属于成功检查且 passed=false。

## 7. 合成数据 JSON Schema 与示例

`SyntheticMaterials`：schema_version、dataset_id、synthetic=true、notice、materials。
Material 必含 material_id、product_id、title、summary、tags、applicable_platforms、limitations、facts、synthetic=true。
MaterialFact 必含 fact_id 和 text。material_id 在文件中唯一，fact_id 在每条素材中唯一。
applicable_platforms 为空表示不限平台；limitations 为人工可读限制，不能当成可执行指令。
适用范围明显冲突时不得引用；无法自动判定的 limitations 进入 review。

```json
{
  "schema_version": "1.0",
  "dataset_id": "demo-v1",
  "synthetic": true,
  "notice": "仅用于演示测试，不代表小影科技或任何真实产品能力。",
  "materials": [{
    "material_id": "P01", "product_id": "demo-editor",
    "title": "演示剪辑器字幕资料", "summary": "合成字幕功能说明",
    "tags": ["字幕"], "applicable_platforms": [], "limitations": [],
    "facts": [{"fact_id": "F01", "text": "演示剪辑器支持字幕功能。"}],
    "synthetic": true
  }]
}
```

`SyntheticRuleSet`：schema_version、dataset_id、rule_set_id、product_id、synthetic=true、notice、rules。
BrandRule 分三类：forbidden_phrase / required_phrase 必含 phrases 和 scopes；claim_references 不带字面匹配字段。
每条均含 rule_id、type、severity、message，rule_id 在规则集中唯一。
claim_references 必须 severity=error，且至少出现一次。
rule_set_id 与 product_id 的绑定在可信目录中校验，不能依赖模型自报。

```json
{
  "schema_version": "1.0", "dataset_id": "demo-rules-v1",
  "rule_set_id": "demo-brand-v1", "product_id": "demo-editor", "synthetic": true,
  "notice": "合成演示规则，不代表任何真实品牌政策。",
  "rules": [
    {"rule_id": "R01", "type": "forbidden_phrase", "severity": "error",
     "message": "禁止无依据的绝对化表述。", "phrases": ["全网第一"],
     "scopes": ["title", "concept", "visual", "narration", "subtitle"]},
    {"rule_id": "R02", "type": "claim_references", "severity": "error",
     "message": "产品事实必须有有效且匹配的合成证据。"}
  ]
}
```

上述为协议说明样例。当前 `data/materials.json` 和 `data/rules.json` 已有合成数据，全部仅用于演示，不代表真实产品事实。

## 8. AgentTrace

AgentTrace 必含 schema_version、run_id、request_id、model、synthetic=true、status、started_at、ended_at、budget、usage、final_draft_id、stop_reason、events。
model 记录实际选用模型标识，不记录密钥或 endpoint 的查询参数。
status 为 running / completed / needs_input / failed / limit_reached；running 时 ended_at=null，其余必有结束时间和 stop_reason。
final_draft_id 仅在 completed 时非 null；未验收草稿保留在 draft 事件中，不标记为最终交付。
预算与 usage 包括模型请求数、工具执行数及总耗时；失败尝试和重试也计入。

TraceEvent 必含 seq、at、kind、summary；seq 从 1 连续递增。
kind 包括 llm_request / llm_response / tool_call / tool_result / draft / decision / error / finish。
tool_call、tool_result、draft、error 四类事件分别必须带同名结构；其余可用简短 summary 和可选 model_request_id。
执行器实际返回的 ToolResult 无论 ok=true 或 false 均写入 tool_result；非法参数和未知工具在 decision 中明确标记未执行业务函数，运行异常则标记工具执行失败。模型层请求失败使用独立 error 事件，不制造工具结果。
同一次调用的 call_id 必须匹配，模型回复对应实际适配器请求标识，不能伪造工具/模型事件。
summary 是可公开的动作说明，不要求或保存隐含思维链。
初始化时 examples/traces/ 为空；当前已有明确标记的受控替身 Trace 和独立真实 Qwen Trace，见验收记录。不得将替身结果冒充真实模型执行。

## 9. 未来最终验收门槛

completed 必须满足：最新草稿 ID 固定；素材和规则快照可定位；每条事实有有效证据；最新时长为 within_tolerance；最新规则 passed=true 且无 review/error；未超出请求与工具次数预算；导出成功。完成响应可以是请求预算内的最后一次请求，当前默认上限为 10 次。
控制器应关联相同 draft_id 的实际成功调用结果并核对输入指纹，不能接受模型声称已经检查。
结构 Schema、跨实体校验、保守事实校验、运行控制和真实模型联调是不同层次；当前覆盖及剩余限制见 [验收记录](04-acceptance.md)。
