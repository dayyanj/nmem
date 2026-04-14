# Integração com MCP

<!-- i18n:start -->
[English](../mcp-integration.md) | [简体中文](../zh-hans/mcp-integration.md) | [日本語](../ja/mcp-integration.md) | [한국어](../ko/mcp-integration.md) | [Español](../es/mcp-integration.md) | **Português** | [Français](../fr/mcp-integration.md) | [Deutsch](../de/mcp-integration.md) | [Русский](../ru/mcp-integration.md)
<!-- i18n:end -->


nmem inclui um servidor MCP (Model Context Protocol) que permite que o Claude Code, o Cursor e outras ferramentas de IA usem o nmem como memória persistente em conversas.

## Configuração

> **Nota**: O nmem ainda não está disponível no PyPI. Clonar e instalar a partir da fonte:

### 1. Instale o nmem com suporte a MCP