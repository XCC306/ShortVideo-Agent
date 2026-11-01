# ShortVideo-Agent

小影科技 AI Agent 笔试第 4 题：基于事实约束的短视频脚本 Agent。

## 评审入口

本目录为按 GitHub 交付规范补齐的提交副本，代码、文档和验证材料均在仓库根目录下。
建议依次阅读 [需求](docs/01-requirements.md)、[方案](docs/02-solution.md)、[评估](docs/03-evaluation.md)、[AI 使用说明](docs/04-ai-usage.md)、[复盘](docs/05-retrospective.md)。
附加的数据契约和上一轮 MVP 验收记录保留，当前提交验证状态以评估文档为准。

## 当前状态

截至 2026-11-01，本地 MVP 已完成真实 Qwen 正常生成与资料不足两个场景验收，实际模型为 `qwen-plus`。已实现数据模型、合成素材、三个工具、真实 Tool Calling 控制器、CLI 和 JSON/Markdown/Trace 导出。

原 MVP 离线回归为 14 passed；当前 GitHub 提交版的最新结果为 **15 passed**，`pip check` 通过。受控替身测试与真实模型验收分别保存，当前验证以 [评估文档](docs/03-evaluation.md) 为准；上一轮快照见 [验收记录](docs/04-acceptance.md)。

GitHub 提交补齐阶段新增了可执行工具故障证据及一项回归测试，最新为 **15 passed in 4.32s**，实际日志见 [pytest.json](examples/test-results/pytest.json)。仅将失败的 ToolResult 完整写入 Trace，并区分未执行与执行异常的摘要；模型决策、预算、工具和完成门槛未变。本阶段没有新增 Qwen 请求。

当前项目目录为 `D:\Projects\ShortVideo-Agent\work\ShortVideo-Agent`，使用独立 Python 3.11.15 环境。旧迁移环境保留在 `.venv-migrated-backup-20261030`。

## 已确定的方案

Python 3.11；Qwen API；CLI；单 Agent + 真实 LLM Tool Calling；本地 JSON；合成素材；Pytest；Windows + VS Code。
不开发前端、视频生成、云部署、多 Agent 或数据库。

## 文档

- [需求规格](docs/01-requirements.md)
- [系统方案](docs/02-solution.md)
- [数据与工具契约](docs/03-data-contracts.md)
- [机器可读契约 Schema](docs/schemas/contracts.schema.json)
- [验收与交付记录](docs/04-acceptance.md)
- [评估与三组证据](docs/03-evaluation.md)
- [AI 辅助范围、人工操作及验证](docs/04-ai-usage.md)
- [工程复盘](docs/05-retrospective.md)

运行时代码使用 Pydantic 校验结构与跨字段约束；机器可读 Schema 保留为契约基线。

## 目录

```text
ShortVideo-Agent/
  src/                  # 数据模型、三个工具、Agent、CLI
  data/                 # 合成素材和规则
  tests/                # 离线工具、Agent、CLI 测试
  examples/
    inputs/             # 输入样例
    outputs/            # 成功运行的 JSON / Markdown
    traces/             # 运行 Trace
  docs/
    schemas/            # JSON Schema Draft 2020-12
  .env.example          # 空占位变量，不含凭据
  .gitignore
  requirements.txt
```

## 环境与离线验证

本机继续使用已验收的项目 `.venv`。源码交付包不含虚拟环境或 `.python-runtime`；在新位置运行时需独立安装 Python 3.11 并重建环境，不能复制旧虚拟环境。已安装 Windows Python Launcher 和 Python 3.11 时，在新工程根目录执行：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

若没有 `py`，用已核实版本的独立 Python 3.11 解释器绝对路径替换 `py -3.11`。不要复用 voicetalk 或其他项目环境。依赖声明未锁版本，验收环境的版本记录在交付清单中。

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m src.main --help
```

测试使用受控模型响应替身，不请求 Qwen API。VS Code 使用 `Python: Select Interpreter` 选择本项目的 `.venv\Scripts\python.exe`。

离线演示可执行 `python -m examples.offline_demo`，它会生成一组新产物，但不请求 Qwen，Trace 的 model 明确为 `offline-controlled-stub`。

工具异常场景可离线复现（该命令返回 0 表示预期异常处理已验证，不表示生成了脚本）：

```powershell
.\.venv\Scripts\python.exe -m examples.tool_failure_demo --input examples/inputs/tool-failure.json
```

## 真实 Qwen 运行

新使用者在 VS Code 中根据 `.env.example` 创建项目根目录的 `.env`，填写 `QWEN_API_KEY`、`QWEN_BASE_URL` 和 `QWEN_MODEL`；本次验收使用 `qwen-plus`。本机已配置的 `.env` 保持原样，交付包仅带三个空变量的模板。不要把密钥提交到版本库、聊天或 Trace。

`QWEN_BASE_URL` 必须是与账号地域、计费方案及业务空间匹配的 HTTPS OpenAI 兼容基地址，末尾为 `/compatible-mode/v1`；代码会追加 `/chat/completions`，不要重复填写。项目使用标准库直接发送请求，不依赖供应商 SDK。真实运行会请求外部模型并消耗账号额度。

```powershell
.\.venv\Scripts\python.exe -m src.main --input examples/inputs/normal.json
.\.venv\Scripts\python.exe -m src.main --input examples/inputs/unsupported-feature.json
```

输入缺失或非法时，CLI 给出字段错误并在模型调用前退出。成功时输出 `examples/outputs/<run_id>.json`、`.md` 及 `examples/traces/<run_id>.trace.json`；未完成时仅输出 Trace。`completed` 必须是同一最新草稿的时长及规则检查均通过。默认预算为 10 次模型请求、12 次工具调用、180 秒。输出目录只能位于当前项目内，已有文件不会被覆盖。合成素材仅用于演示，不代表真实小影产品事实。

CLI 退出码：`0` 为 completed；`1` 为 needs_input、failed、limit_reached 或导出失败；`2` 为参数、输入或配置错误。资料不足的预期状态是 needs_input 和退出码 1，VS Code 任务的红叉不代表 Python 环境损坏。

## 已保留的验收证据

| 场景 | 实际结果 | 证据 |
| --- | --- | --- |
| 真实正常生成 | completed；draft-003；估计 15.00 秒；规则 passed，0 findings | [脚本](examples/outputs/run-939b1ba4c0c3466c.md)、[JSON](examples/outputs/run-939b1ba4c0c3466c.json)、[Trace](examples/traces/run-939b1ba4c0c3466c.trace.json) |
| 真实资料不足 | 两次检索为空后 needs_input，无最终脚本 | [Trace](examples/traces/run-43b3c2997f6240b4.trace.json) |
| 工具内部异常（离线故障注入） | 执行器实际捕获异常并返回 INTERNAL_ERROR，受控模型观察后停止，无脚本 | [输入](examples/inputs/tool-failure.json)、[结果](examples/outputs/run-fc87dd0c12dd43c8.tool-failure.result.json)、[Trace](examples/traces/run-fc87dd0c12dd43c8.trace.json) |
| 离线受控替身 | completed；仅用于可复现的本地演示 | [脚本](examples/outputs/run-26912cc433694dcf.md)、[Trace](examples/traces/run-26912cc433694dcf.trace.json) |

正常场景记录 10 次模型请求、9 次工具调用，经历规则反馈和草稿修改后完成；资料不足场景为 3 次请求、2 次检索。它们是已保存的验收快照，后续生成内容和调用次数可能变化。

三组验证材料分别覆盖正常生成、资料不足和工具失败。资料不足的 [结果摘要](examples/outputs/unsupported-feature.result.json) 由原真实 Trace 派生，不是新的模型运行。第三组故障在真实本地执行器边界注入，模型回复是明确标记的受控替身，不能视作真实 Qwen 故障决策验证。全部材料对应关系见 [评估文档](docs/03-evaluation.md)。

当前成功示例只有一个分镜、一句旁白，15 秒包含视觉停留；它用于链路验证，不等于实测配音时长或脚本质量已充分优化。事实检查是字面规则与精确原子事实引用，不能穷尽任意自然语言断言。其他边界由离线测试覆盖，不声称五类场景全部逐项做过真实联调。

## 交付范围

提交内容包含源码、合成数据、测试、题目规定的五份文档、附加契约、三组验证材料及一组离线正常示例；不含 `.env`、Python 环境、缓存、迁移备份、`.vscode` 或临时配置脚本。完整文件清单与 SHA-256 见 `RELEASE-MANIFEST.json`。原 D 盘工程的历史失败 Trace 和备份保留不动。远程仓库是否创建或推送，以本次交付报告的实际结果为准。

接口参考：[阿里云百炼 Function Calling 官方文档](https://help.aliyun.com/zh/model-studio/qwen-function-calling)。
