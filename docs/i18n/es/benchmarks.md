# Pruebas de rendimiento

<!-- i18n:start -->
[English](../../benchmarks/README.md) | [简体中文](../zh-hans/benchmarks.md) | [日本語](../ja/benchmarks.md) | [한국어](../ko/benchmarks.md) | **Español** | [Português](../pt/benchmarks.md) | [Français](../fr/benchmarks.md) | [Deutsch](../de/benchmarks.md) | [Русский](../ru/benchmarks.md)
<!-- i18n:end -->


Evaluaciones empíricas del impacto de nmem en el rendimiento de los agentes. Todos los benchmarks utilizan una metodología reproducible con hardware local ($0 costo de inferencia).

## Resumen de resultados

| Benchmark | Hallazgo | Métrica clave |
|-----------|---------|------------|
| [Agente Multidisciplinario de Salud](healthcare-multi-agent.md) | Puntaje de revisión de creencias **5.00/5 vs 3.13/5 línea base** después de cambios en las guías — en un modelo de 14B, GPU única para consumidores | +7% en general, +60% en revisión de creencias |
| [Conocimiento Institucional de Spwig](spwig-institutional-knowledge.md) | La búsqueda de nmem MCP coincide con la precisión de un nuevo desarrollador a **la mitad del costo** | 4.27/5 puntaje del juez, $0.097 por tarea |
| [Señales de Reconocimiento](recognition-signals.md) | Las etiquetas de confianza en los prompts no cambian el comportamiento de modelos de 8B-30B | Reconocimiento calculado pero no inyectado |

## Benchmark de Spwig: Números rápidos

**Configuración:** Plataforma de comercio electrónico con 17 repositorios, 45 tareas de prueba, 5 variantes, 225 evaluaciones de doble juez.

| Variante | ¿Qué es? | Puntaje del juez | Costo |
|---------|-----------|-------------|------|
| **v8_mcp** | El agente busca en nmem mediante herramientas MCP | 4.27/5 | **$4.35** |
| new_developer | Sin memoria, explora desde cero | 4.36/5 | $8.18 |
| control | Memoria automática de Claude Code (cobertura de hechos del 82%) | 3.98/5 | $7.06 |
| v8_injected | Memoria pre-inyectada en el prompt | 4.02/5 | $15.27 |
| v8_briefing | API de briefing con señales de reconocimiento | 3.96/5 | $18.36 |

**Insight clave:** La búsqueda MCP es la más barata Y más precisa porque el agente decide qué buscar según cada pregunta. La pre-inyección adivina qué podría ser útil antes de ver la pregunta.

## Benchmark de Salud: Números rápidos

**Configuración:** Simulación de 180 días, 4 agentes de salud, 200 pacientes sintéticos, 1,705 encuentros, 40 preguntas de prueba, Qwen3-14B en RTX 4090 ($0 costo de inferencia).

| Categoría | nmem | Línea base | Delta |
|----------|------|----------|-------|
| **Revisión de creencias** | **4.75/5** | 3.21/5 | **+48%** |
| Recuerdo directo | 4.09/5 | 3.73/5 | +10% |
| Detección de patrones | 3.48/5 | 3.33/5 | +4% |
| En general | 3.84/5 | 3.60/5 | +7% |

**Insight clave:** La revisión de creencias es la diferenciación más fuerte de nmem. Cuando cambian las guías, nmem detecta la contradicción, la resuelve durante la consolidación y recupera la política actualizada. El LLM de línea base no tiene mecanismo para distinguir el conocimiento actual del antiguo. Después del día 120 (todos los cambios de guías), nmem obtiene un perfecto 5.00/5 en cada pregunta de revisión de creencias.

## Configuraciones probadas

| Benchmark | Modelo | Integración | Contexto |
|-----------|-------|-------------|---------|
| Spwig | Claude Sonnet 4.6 (200K ctx) | Herramientas MCP | Recuperación de conocimiento institucional |
| Salud | Qwen3-14B-AWQ (GPU para consumidores) | API de Python + búsqueda | Consolidación de multi-agente con el tiempo |

## Metodología

### Benchmark de Spwig
- **Agente:** Claude Sonnet 4.6, sin cabeza (`claude -p`), una invocación por tarea
- **Jueces:** Qwen3-14B (GPU) + Qwen3-30B-A3B MoE (CPU), puntuación independiente, escala de 1-5
- **Aislamiento:** PostgreSQL dedicado en el puerto 5435, separado del desarrollo
- **Corpus:** 6,076 entradas — conversaciones distiladas por LLM + auto-memoria segmentada semánticamente + documentos + git
- **Controles:** HOME limpio para new_developer, sin persistencia de sesión, misma base CLAUDE.md

### Benchmark de Salud
- **Modelo:** Qwen3-14B-AWQ en vLLM (una RTX 4090, $0 inferencia)
- **Juez:** Qwen3-14B, escala de 1-5 según rubrica
- **Datos:** Pacientes sintéticos de Synthea (Apache 2.0) + escenarios clínicos elaborados a mano
- **Simulación:** 180 días comprimidos en 4.6 horas mediante simulación de tiempo
- **Evaluación:** 40 preguntas en 7 intervalos (días 1, 30, 60, 90, 120, 150, 180)