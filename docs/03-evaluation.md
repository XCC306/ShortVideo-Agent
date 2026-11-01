# 03 评估：预期、实际结果、失败与限制

日期：2026-11-01。评估覆盖当前会话确认的短视频脚本 MVP 和提供的 GitHub 交付规范；第 4 题完整原文尚未提供，不声称逐字核对过未知验收条款。

## 1. 三组输入、实际输出与记录

| 组别 | 输入 | 实际输出 | 运行记录 | 模式 |
| --- | --- | --- | --- | --- |
| 正常生成 | [normal.json](../examples/inputs/normal.json) | [JSON](../examples/outputs/run-939b1ba4c0c3466c.json)、[Markdown](../examples/outputs/run-939b1ba4c0c3466c.md) | [Trace](../examples/traces/run-939b1ba4c0c3466c.trace.json) | 真实 qwen-plus，2026-10-31 |
| 资料不足 | [unsupported-feature.json](../examples/inputs/unsupported-feature.json) | [结果摘要，script=null](../examples/outputs/unsupported-feature.result.json) | [Trace](../examples/traces/run-43b3c2997f6240b4.trace.json) | 真实 qwen-plus，2026-10-31；摘要从原 Trace 派生 |
| 工具内部异常 | [tool-failure.json](../examples/inputs/tool-failure.json) | [实际故障结果](../examples/outputs/run-fc87dd0c12dd43c8.tool-failure.result.json) | [Trace](../examples/traces/run-fc87dd0c12dd43c8.trace.json) | 离线、实际执行的故障注入，2026-11-01 |

第三组以 unittest.mock 在本地检索函数边界注入 OSError，生产执行器实际捕获并返回 INTERNAL_ERROR，完整错误回传给受控模型；模型观察错误后选择 needs_input，系统未交付脚本。它证明本地错误传播、记录与停止路径，**不证明真实 Qwen 在工具故障时会作同样决定**。
故障演示的 model 为 offline-tool-failure-injection，响应和 call ID 带 offline 前缀；没有伪装成供应商 ID，也不读取 .env。

## 2. 预期与实际

| 验收项 | 预期 | 已观察结果 |
| --- | --- | --- |
| 正常交付 | 同一最新草稿通过时长和规则检查后完成 | draft-003；15.00 秒；within_tolerance；passed，0 findings |
| 动态决策 | 根据工具反馈调整，不由程序硬编码三个工具顺序 | 真实正常 Trace 中 draft-001、draft-002 为 needs_review，模型修改后再次检查 |
| 工具调用 | 模型提供工具名、参数，执行器返回可关联结果 | 正常场景三个工具各 3 次，共 9 次；10 次模型请求 |
| 资料不足 | 检索仍无依据时请求补充，不编造 | 两次检索均为空，3 次模型请求后 needs_input，无最终草稿 |
| 工具失败 | 错误可追踪且反馈给模型，不当成成功 | 注入异常后 ok=false、INTERNAL_ERROR；受控模型读取错误后停止 |
| 时长超限与旧检查失效 | 修改后重估，旧版本检查不能验收新草稿 | 受控响应回归覆盖 too_long、修改和旧规则结果失效；不将其称为独立真实时长场景 |
| 非法调用、请求失败和预算 | 拒绝执行、记录错误或停止 | 离线测试覆盖未知工具、非法参数、模型鉴权失败和耗尽预算 |
| 输入与导出 | 缺字段在模型请求前退出；不覆盖既有输出 | CLI 与契约测试覆盖，输出文件已保存 |

上述后五类的覆盖基于已确认的工程需求。正常示例为一个分镜、一句旁白；15 秒主要包含视觉停留时间，不能解读为配音实测长度或成熟脚本质量。

## 3. 测试与复现

新增故障回归后，实际结果为 **15 passed in 4.32s**；pip check 无冲突。原始实际测试日志保存在 [pytest.json](../examples/test-results/pytest.json) 和 [pytest.txt](../examples/test-results/pytest.txt)，不是手工编写的通过声明。

在根目录使用独立 Python 3.11 环境：

```powershell
python -m pytest -q
python -m examples.tool_failure_demo --input examples/inputs/tool-failure.json
```

第一条运行离线测试，第二条注入可复现的本地故障并生成新结果与 Trace，均不请求 Qwen。
真实 CLI 用法和本地 .env 配置见 README。needs_input 的业务 CLI 退出码为 1；故障演示命令的退出码 0 表示预期的故障处理断言通过，不表示生成成功。

## 4. 实际发现的问题及最小修正

1. 中文整句当成单词导致“旅行视频字幕”未检出已有字幕资料。修正为字面双字片段匹配，补充回归测试。
2. 模型将事实仅写在 Claim 而没有写进对应脚本文本。收紧提示，保持原文与范围检查门槛。
3. 第 8 次请求刚完成检查，无余量提出完成。将有界请求预算从 8 调到 10，其他上限保持 12 次工具和 180 秒。
4. 交付故障场景时发现失败 ToolResult 只记摘要。提交版补充完整错误结果，并区分“参数拒绝未执行”和“运行异常执行失败”；没有改变工具选择或完成条件。

两个真实场景的记录产生于第 4 项日志修正之前；提交版保留其真实内容，并用新增离线故障测试验证日志修正，没有再请求 Qwen。文本文件采用 LF，JSON 数据未重写为新的运行。

## 5. 已知限制

- 初始五类场景没有全部逐项做独立真实联调；第三组为明确标注的离线异常注入。
- 事实检查基于字面规则、精确原子事实和保守的产品名检测，不能穷尽自然语言中的未声明事实。
- 单分镜示例偏重工程链路。没有实测配音或视频生成。
- 工具异常目前可能归为 INTERNAL_ERROR；模型 needs_input 的通用结束理由不一定保留具体异常原因，需结合 ToolResult 阅读。
- 本地工具为同步执行，没有进程级单次硬超时。真实模型一次成功使用了 10 次请求预算，尚未验证重复运行的稳定成功率。
- requirements.txt 未锁版本，验收环境的精确依赖版本在 RELEASE-MANIFEST.json 中；新环境安装后应重跑测试。

Python 3.11.15、Pydantic 2.14.2 用于数据验证，Pytest 9.2.0 用于离线测试，jsonschema 4.26.0 用于输出契约验证。没有采用 Agent 框架，Qwen 接入由标准库 HTTP 客户端实现；AI 辅助范围另见 [04-ai-usage.md](04-ai-usage.md)。
