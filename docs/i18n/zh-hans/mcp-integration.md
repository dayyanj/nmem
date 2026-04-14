# MCP 集成

<!-- i18n:start -->
[English](../mcp-integration.md) | **简体中文** | [日本語](../ja/mcp-integration.md) | [한국어](../ko/mcp-integration.md) | [Español](../es/mcp-integration.md) | [Português](../pt/mcp-integration.md) | [Français](../fr/mcp-integration.md) | [Deutsch](../de/mcp-integration.md) | [Русский](../ru/mcp-integration.md)
<!-- i18n:end -->


nmem 提供了一个 MCP（Model Context Protocol）服务器，允许 Claude Code、Cursor 和其他 AI 工具将 nmem 用作跨对话的持久化内存。

## 设置

> **注意**：nmem 尚未发布到 PyPI。请克隆源代码并安装：

### 1. 安装支持 MCP 的 nmem