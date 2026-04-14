# Intégration MCP

<!-- i18n:start -->
[English](../mcp-integration.md) | [简体中文](../zh-hans/mcp-integration.md) | [日本語](../ja/mcp-integration.md) | [한국어](../ko/mcp-integration.md) | [Español](../es/mcp-integration.md) | [Português](../pt/mcp-integration.md) | **Français** | [Deutsch](../de/mcp-integration.md) | [Русский](../ru/mcp-integration.md)
<!-- i18n:end -->


nmem fournit un serveur MCP (Model Context Protocol) qui permet à Claude Code, Cursor et d'autres outils IA d'utiliser nmem comme mémoire persistante à travers les conversations.

## Configuration

> **Note** : nmem n'est pas encore disponible sur PyPI. Clonez et installez depuis la source :

### 1. Installez nmem avec le support MCP