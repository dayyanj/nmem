# 基准测试

<!-- i18n:start -->
[English](../../benchmarks/README.md) | **简体中文** | [日本語](../ja/benchmarks.md) | [한국어](../ko/benchmarks.md) | [Español](../es/benchmarks.md) | [Português](../pt/benchmarks.md) | [Français](../fr/benchmarks.md) | [Deutsch](../de/benchmarks.md) | [Русский](../ru/benchmarks.md)
<!-- i18n:end -->


对nmem对代理性能影响的实证评估。所有基准测试均采用可重复的方法论，并在本地硬件上进行双评委评分（$0 评分成本）。

## 结果概览

| 基准测试 | 发现 | 关键指标 |
|-----------|---------|------------|
| [Spwig 机构知识](spwig-institutional-knowledge.md) | nmem MCP 搜索的准确率达到新开发者的 **一半成本** | 4.27/5 评委评分，$0.097/任务 |
| [识别信号](recognition-signals.md) | 提示中的信任标签不会改变 8B-30B 模型行为 | 计算了识别但未注入 |

## Spwig 基准测试：快速数据

**设置：** 17 个仓库的电子商务平台，45 个测试任务，5 个变体，225 次双评委评估。

| 变体 | 说明 | 评委评分 | 成本 |
|---------|-----------|-------------|------|
| **v8_mcp** | 代理通过 MCP 工具搜索 nmem | 4.27/5 | **$4.35** |
| new_developer | 无记忆，从头开始探索 | 4.36/5 | $8.18 |
| control | Claude Code 自动记忆（82% 事实覆盖率） | 3.98/5 | $7.06 |
| v8_injected | 记忆预先注入到提示中 | 4.02/5 | $15.27 |
| v8_briefing | 带识别信号的简报 API | 3.96/5 | $18.36 |

**关键见解：** MCP 搜索既最便宜又最准确，因为代理会根据每个问题决定搜索什么。预先注入的猜测是在看到问题之前就假设可能有用的内容。

## 当前范围

迄今为止的所有基准测试均使用 **Claude Code（Sonnet 4.6，200K 上下文）** ——MCP 集成是经过验证的用例。使用较小模型（8B-30B，8K-32K 上下文）的代理用例是下一步路线图。详情请参阅 [范围与限制](spwig-institutional-knowledge.md#scope--limitations)。

## 方法论

- **代理：** Claude Sonnet 4.6，无头模式（`claude -p`），每个任务单次调用
- **评委：** Qwen3-14B（GPU）+ Qwen3-30B-A3B MoE（CPU），独立评分，1-5 分制
- **隔离：** 端口 5435 上的专用 PostgreSQL，与开发环境分开
- **语料库：** 6,076 条目 —— LLM 蒸馏对话 + 语义分块自动记忆 + 文档 + git
- **控制：** new_developer 的 HOME 清洁，无会话持久化，相同的 CLAUDE.md 基础