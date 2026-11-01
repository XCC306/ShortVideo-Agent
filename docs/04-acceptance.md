# 04 验收与交付记录

本文件保留上一轮 MVP 验收快照。GitHub 交付规范补齐阶段的三组材料、15 项测试及失败 Trace 补齐，统一见 [03-evaluation.md](03-evaluation.md)。

收尾日期：2026-11-01（Asia/Shanghai）。两个真实场景运行于 2026-10-31。
范围：用户确认的 Windows 本地 CLI MVP。

## 1. 验收结论

功能 MVP 已真实跑通：`qwen-plus` 通过 Tool Calling 选择三个白名单工具，控制器真实执行并回传结果，最终按同一最新草稿的检查结果验收。
已验证正常生成与资料不足两个真实场景；其他边界由离线受控响应测试覆盖，不声称初始五类场景全部逐项做过真实联调。

本轮收尾只更新交付副本的文档与忽略规则，没有修改业务源码、测试、合成数据或依赖声明，没有新增模型请求，没有创建或推送 GitHub 仓库。

## 2. 环境与验证

本机原工程为 `D:\Projects\ShortVideo-Agent\work\ShortVideo-Agent`。
已验收解释器为其 `.venv\Scripts\python.exe`，Python 3.11.15，基础运行时为同一工程 `.python-runtime`。
旧环境保留在 `.venv-migrated-backup-20261030`；交付包不带环境或备份，新位置应重建独立 Python 3.11 环境。

收尾前独立复测为 **14 passed in 2.28s**；本轮又在干净交付副本中得到 **14 passed in 5.51s**，`pip check` 为 No broken requirements found。
本轮还核对了已保存 Trace 与 JSON 的契约、调用 ID 关联、最终草稿绑定和本地 Markdown 链接，没有发起新的模型请求。测试使用已验收的独立解释器，未在新建虚拟环境中重新安装依赖；不将交付副本复测误写为 ZIP 解压后的测试。
测试不请求模型 API，使用受控回复覆盖检索、中文复合查询、时长、引用与规则、草稿绑定、预算、CLI 和输出契约。
独立复测使用可写工作区内的新临时目录，业务代码与断言未变。

requirements.txt 未锁版本；本次版本与文件 SHA-256 记录在交付清单 `RELEASE-MANIFEST.json` 中。

## 3. 真实正常生成

输入：[normal.json](../examples/inputs/normal.json)。

| 项目 | 实际结果 |
| --- | --- |
| run_id | run-939b1ba4c0c3466c |
| 模型 / 状态 | qwen-plus / completed |
| 最终草稿 | draft-003 |
| 时长 | 15.00 秒，within_tolerance |
| 规则 | passed，0 findings |
| 模型请求 / 工具调用 | 10 / 9 |
| 三个工具次数 | search_materials 3、estimate_duration 3、check_rules 3 |
| 记录的运行耗时 | 67.969 秒，不作为重复运行时延保证 |

Trace 中 draft-001、draft-002 的规则结果为 needs_review，模型修改后，draft-003 的时长与规则检查均通过，随后明确提出完成。
两项最终检查与导出脚本的 draft_id 一致，引用来自本轮检索结果。

产物：[JSON](../examples/outputs/run-939b1ba4c0c3466c.json)、[Markdown](../examples/outputs/run-939b1ba4c0c3466c.md)、[Trace](../examples/traces/run-939b1ba4c0c3466c.trace.json)。

## 4. 真实资料不足

输入：[unsupported-feature.json](../examples/inputs/unsupported-feature.json)。

| 项目 | 实际结果 |
| --- | --- |
| run_id | run-43b3c2997f6240b4 |
| 模型 / 状态 | qwen-plus / needs_input |
| 检索 | “量子火箭”和“自动配音”两次查询均为空 |
| 模型请求 / 工具调用 | 3 / 2 |
| 最终脚本 | 无，final_draft_id=null |
| 记录的运行耗时 | 7.547 秒 |

产物：[Trace](../examples/traces/run-43b3c2997f6240b4.trace.json)。退出码 1 是该场景未交付脚本的预期业务结果，不是 Python 启动故障。

## 5. 离线受控示例

离线示例的 model 明确为 `offline-controlled-stub`，与真实验收分别保存：

- [JSON](../examples/outputs/run-26912cc433694dcf.json)
- [Markdown](../examples/outputs/run-26912cc433694dcf.md)
- [Trace](../examples/traces/run-26912cc433694dcf.trace.json)
- 复现：使用独立环境，在根目录执行 `python -m examples.offline_demo`。

真实模型存在输出变化，上述真实场景是验收快照，不保证重复调用的内容或次数相同。

## 6. 联调修正与当前预算

联调修正了中文复合查询的字面双字片段匹配，并收紧模型写作提示，使事实原文出现在对应脚本文本中。
默认模型请求上限从 8 调到 10，为检查通过后的完成响应留出一轮；最终草稿验收门槛未改变。

当前默认上限为 10 次模型请求、12 次工具调用、180 秒总预算；每种可重试模型错误最多 2 次重试，单次模型请求 timeout 不超过 30 秒及剩余预算。
本地工具是同步函数，未实现进程级单次硬超时；全局墙钟检查不等于可在任意指令处硬中断执行。

## 7. 明确限制与交付范围

- 成功示例仅一个分镜、一句旁白；15 秒含视觉停留，不等于实测配音长度或脚本质量已充分优化。
- 时长为字符数与停留时间的启发式估计，没有实际音视频或 TTS 生成。
- 事实校验是字面规则与精确原子事实引用，不能穷尽任意自然语言断言；素材全部为合成演示。
- 未做大规模重复真实调用或生产级稳定性测试，没有前端、云部署、数据库或多 Agent。
- 最初三份设计文档保留历史阶段描述，当前实现和验证范围以本记录及 README 为准。

交付包仅收录明确清单中的源码、测试、合成数据、设计与验收文档、三个输入样例、两个真实场景的选定证据和一组离线示例。
不收录 `.env`、密钥、环境、缓存、迁移备份、`.vscode`、本地临时脚本或 Git 元数据。
`.env.example` 只有三个空变量。原项目中的历史失败 Trace、本地配置和备份均保留，未因打包删除。

本轮冻结功能，后续脚本质量改进可作为独立任务，不改变本次真实链路验收结论。
