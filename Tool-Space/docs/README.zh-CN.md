# Tool-Space：Automation → Automation Pro

功能按目录划分：`automation_environment` 提供本地环境、接口目录和状态快照，`tool_graph` 构建工具关系图谱并采样可执行工作流，`task_generation` 联合生成 Pro 题目与原生 verifier，`verification` 执行 replay 和评分，`examples` 提供示例与离线模型响应。

## 离线跑通

```bash
uv sync --locked
uv run python run_pipeline.py demo --output outputs/demo
```

环境执行、图谱、采样、评分和独立 replay 都真实运行；模型响应使用固定示例，不需要密钥。

## 使用真实模型

配置 `PRO_MODEL`、`PRO_BASE_URL`、`PRO_API_KEY`、`PRO_API` 后运行：

```bash
uv run python run_pipeline.py run \
  --input examples/project_reconciliation.json \
  --walk-config examples/walk.json \
  --count 1 --output outputs/pro
```

支持 Chat Completions 和 Responses，不内置私有地址或密钥。输入格式见 [input-format.md](input-format.md)。`run_pipeline.py` 也提供 `graph`、`walk`、`synthesize`、`verify`、`score` 分阶段命令；`synthesize --resume` 可继续中断的生成。

题目生成保留原默认 `grounded_decisions_v2` 提示词、难度检查和语义审查；评分直接调用 AutomationBench 的 `partial_credit`。原生存在性与关键词断言仍可能漏判重复输出、数字与含义错配等情况。模块来源见 [source-mapping.md](source-mapping.md)。

输出位于 `outputs/pro/tasks/automation_pro.jsonl`，包含问题、初始状态、原生断言与参考轨迹。用于 RL 时，只向模型提供问题和工具，将 reference 和 verifier 保留在环境侧。验证与开源许可证说明见 [README](../README.md)。
