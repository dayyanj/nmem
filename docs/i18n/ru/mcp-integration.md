# Интеграция MCP

<!-- i18n:start -->
[English](../mcp-integration.md) | [简体中文](../zh-hans/mcp-integration.md) | [日本語](../ja/mcp-integration.md) | [한국어](../ko/mcp-integration.md) | [Español](../es/mcp-integration.md) | [Português](../pt/mcp-integration.md) | [Français](../fr/mcp-integration.md) | [Deutsch](../de/mcp-integration.md) | **Русский**
<!-- i18n:end -->


nmem поставляется с сервером MCP (Model Context Protocol), который позволяет Claude Code, Cursor и другим инструментам ИИ использовать nmem в качестве постоянной памяти во время разговоров.

## Настройка

> **Примечание**: nmem еще не доступен в PyPI. Клонируйте и установите из исходного кода:

### 1. Установите nmem с поддержкой MCP