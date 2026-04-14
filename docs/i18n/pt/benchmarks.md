# Benchmarks

<!-- i18n:start -->
[English](../benchmarks/README.md) | [简体中文](../zh-hans/benchmarks.md) | [日本語](../ja/benchmarks.md) | [한국어](../ko/benchmarks.md) | [Español](../es/benchmarks.md) | **Português** | [Français](../fr/benchmarks.md) | [Deutsch](../de/benchmarks.md) | [Русский](../ru/benchmarks.md)
<!-- i18n:end -->


Avaliações empíricas do impacto do nmem no desempenho dos agentes. Todos os benchmarks utilizam metodologia reprodutível com pontuação de duplo juiz em hardware local ($0 custo de julgamento).

## Resumo dos Resultados

| Benchmark | Encontrado | Métrica Chave |
|-----------|---------|------------|
| [Spwig Institutional Knowledge](spwig-institutional-knowledge.md) | A busca do nmem via MCP corresponde à precisão de um novo desenvolvedor com **metade do custo** | 4.27/5 pontuação do juiz, $0.097/tarefa |
| [Recognition Signals](recognition-signals.md) | Tags de confiança nos prompts não alteram o comportamento de modelos 8B-30B | Reconhecimento calculado, mas não injetado |

## Benchmark Spwig: Números Rápidos

**Configuração:** Plataforma de comércio eletrônico com 17 repositórios, 45 tarefas de teste, 5 variantes, 225 avaliações de duplo juiz.

| Variante | O que é | Pontuação do Juiz | Custo |
|---------|-----------|-------------|------|
| **v8_mcp** | Agente busca nmem via ferramentas MCP | 4.27/5 | **$4.35** |
| new_developer | Nenhuma memória, explora do zero | 4.36/5 | $8.18 |
| control | Memória automática do Claude Code (82% cobertura de fatos) | 3.98/5 | $7.06 |
| v8_injected | Memória pré-injetada no prompt | 4.02/5 | $15.27 |
| v8_briefing | API de briefing com sinais de reconhecimento | 3.96/5 | $18.36 |

**Insight principal:** A busca via MCP é a mais barata E mais precisa porque o agente decide o que buscar com base em cada pergunta. A pre-injeção adivinha o que pode ser útil antes de ver a pergunta.

## Escopo Atual

Todos os benchmarks até o momento utilizam **Claude Code (Sonnet 4.6, 200K contexto)** — a integração MCP é o caso de uso validado. Casos de uso agênticos com modelos menores (8B-30B, 8K-32K contexto) estão no próximo passo do roadmap. Veja [Escopo e Limitações](spwig-institutional-knowledge.md#scope--limitations) para detalhes.

## Metodologia

- **Agente:** Claude Sonnet 4.6, sem interface gráfica (`claude -p`), uma invocação por tarefa
- **Juízes:** Qwen3-14B (GPU) + Qwen3-30B-A3B MoE (CPU), pontuação independente, escala de 1-5
- **Isolamento:** PostgreSQL dedicado na porta 5435, separado do desenvolvimento
- **Corpus:** 6.076 entradas — conversas distiladas por LLM + memória automática fragmentada semanticamente + documentos + git
- **Controles:** HOME limpo para new_developer, sem persistência de sessão, mesmo CLAUDE.md base