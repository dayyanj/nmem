# Pruebas de rendimiento

<!-- i18n:start -->
[English](../../benchmarks/README.md) | [简体中文](../zh-hans/benchmarks.md) | [日本語](../ja/benchmarks.md) | [한국어](../ko/benchmarks.md) | **Español** | [Português](../pt/benchmarks.md) | [Français](../fr/benchmarks.md) | [Deutsch](../de/benchmarks.md) | [Русский](../ru/benchmarks.md)
<!-- i18n:end -->


Evaluaciones empíricas del impacto de nmem en el rendimiento de los agentes. Todos los benchmarks utilizan una metodología reproducible con puntuación de doble juez en hardware local ($0 costo de juicio).

## Resumen de resultados

| Benchmark | Hallazgo | Métrica clave |
|-----------|---------|------------|
| [Spwig Institutional Knowledge](spwig-institutional-knowledge.md) | La búsqueda de nmem MCP coincide con la precisión de un nuevo desarrollador a **la mitad del costo** | 4.27/5 puntuación del juez, $0.097/tarea |
| [Recognition Signals](recognition-signals.md) | Las etiquetas de confianza en los prompts no cambian el comportamiento de modelos de 8B-30B | Reconocimiento calculado pero no inyectado |

## Benchmark Spwig: Números rápidos

**Configuración:** Plataforma de comercio electrónico con 17 repositorios, 45 tareas de prueba, 5 variantes, 225 evaluaciones de doble juez.

| Variante | Qué es | Puntuación del juez | Costo |
|---------|-----------|-------------|------|
| **v8_mcp** | El agente busca en nmem a través de herramientas MCP | 4.27/5 | **$4.35** |
| new_developer | Sin memoria, explora desde cero | 4.36/5 | $8.18 |
| control | Memoria automática de Claude Code (82% de cobertura de hechos) | 3.98/5 | $7.06 |
| v8_injected | Memoria pre-inyectada en el prompt | 4.02/5 | $15.27 |
| v8_briefing | API de briefing con señales de reconocimiento | 3.96/5 | $18.36 |

**Insight clave:** La búsqueda MCP es tanto la más barata **como** la más precisa porque el agente decide qué buscar según cada pregunta. La pre-inyección adivina qué podría ser útil antes de ver la pregunta.

## Alcance actual

Todos los benchmarks hasta ahora utilizan **Claude Code (Sonnet 4.6, 200K contexto)** — la integración de MCP es el caso de uso validado. Los casos de uso agentic con modelos más pequeños (8B-30B, 8K-32K contexto) están en el próximo plan de trabajo. Consulte [Alcance y limitaciones](spwig-institutional-knowledge.md#scope--limitations) para más detalles.

## Metodología

- **Agente:** Claude Sonnet 4.6, sin cabeza (`claude -p`), una invocación por tarea
- **Jueces:** Qwen3-14B (GPU) + Qwen3-30B-A3B MoE (CPU), puntuación independiente, escala de 1-5
- **Aislamiento:** PostgreSQL dedicado en el puerto 5435, separado del desarrollo
- **Corpus:** 6,076 entradas — conversaciones destiladas por LLM + auto-memoria fragmentada semánticamente + documentos + git
- **Controles:** HOME limpio para new_developer, sin persistencia de sesión, mismo CLAUDE.md base