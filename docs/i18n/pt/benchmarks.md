# Benchmarks

<!-- i18n:start -->
[English](../../benchmarks/README.md) | [简体中文](../zh-hans/benchmarks.md) | [日本語](../ja/benchmarks.md) | [한국어](../ko/benchmarks.md) | [Español](../es/benchmarks.md) | **Português** | [Français](../fr/benchmarks.md) | [Deutsch](../de/benchmarks.md) | [Русский](../ru/benchmarks.md)
<!-- i18n:end -->


Avaliações empíricas do impacto do nmem no desempenho dos agentes. Todos os benchmarks utilizam metodologia reprodutível com hardware local ($0 custo de inferência).

## Resumo dos Resultados

| Benchmark | Achado | Métrica-chave |
|-----------|--------|---------------|
| [Agente de Saúde Multisserviços](healthcare-multi-agent.md) | Pontuação de revisão de crenças **5.00/5 vs 3.13/5 baseline** após mudanças nas diretrizes — em um modelo de 14B, GPU única do consumidor | +7% no geral, +60% revisão de crenças |
| [Conhecimento Institucional Spwig](spwig-institutional-knowledge.md) | A busca MCP do nmem corresponde à precisão de um novo desenvolvedor com **metade do custo** | 4.27/5 pontuação do juiz, $0.097/tarefa |
| [Sinais de Reconhecimento](recognition-signals.md) | Tags de confiança nos prompts não alteram o comportamento de modelos de 8B-30B | Reconhecimento calculado, mas não injetado |

## Benchmark Spwig: Números Rápidos

**Configuração:** Plataforma de e-commerce com 17 repositórios, 45 tarefas de teste, 5 variantes, 225 avaliações duplas pelos juízes.

| Variante | O que é | Pontuação do Juiz | Custo |
|----------|--------|-------------------|-------|
| **v8_mcp** | Agente busca nmem via ferramentas MCP | 4.27/5 | **$4.35** |
| new_developer | Nenhuma memória, explora do zero | 4.36/5 | $8.18 |
| control | Memória automática do Claude Code (82% cobertura de fatos) | 3.98/5 | $7.06 |
| v8_injected | Memória pré-injetada no prompt | 4.02/5 | $15.27 |
| v8_briefing | API de briefing com sinais de reconhecimento | 3.96/5 | $18.36 |

**Insight-chave:** A busca MCP é a mais barata E mais precisa porque o agente decide o que buscar com base em cada pergunta. A pre-injeção adivinha o que pode ser útil antes de ver a pergunta.

## Benchmark de Saúde: Números Rápidos

**Configuração:** Simulação de 180 dias, 4 agentes de saúde, 200 pacientes sintéticos, 1.705 encontros, 40 perguntas de teste, Qwen3-14B na RTX 4090 ($0 custo de inferência).

| Categoria | nmem | Baseline | Delta |
|----------|------|----------|-------|
| **Revisão de crenças** | **4.75/5** | 3.21/5 | **+48%** |
| Recall direto | 4.09/5 | 3.73/5 | +10% |
| Detecção de padrões | 3.48/5 | 3.33/5 | +4% |
| No geral | 3.84/5 | 3.60/5 | +7% |

**Insight-chave:** A revisão de crenças é a diferenciadora mais forte do nmem. Quando as diretrizes mudam, o nmem detecta a contradição, resolve-a na consolidação e recupera a política atualizada. O LLM baseline não tem mecanismo para distinguir conhecimento atual de conhecimento desatualizado. Após o dia 120 (todas as diretrizes mudadas), o nmem obtém uma pontuação perfeita de 5.00/5 em todas as perguntas de revisão de crenças.

## Configurações Testadas

| Benchmark | Modelo | Integração | Contexto |
|-----------|--------|------------|----------|
| Spwig | Claude Sonnet 4.6 (200K ctx) | Ferramentas MCP | Recuperação de conhecimento institucional |
| Saúde | Qwen3-14B-AWQ (GPU do consumidor) | API Python + busca | Consolidação multisserviços ao longo do tempo |

## Metodologia

### Benchmark Spwig
- **Agente:** Claude Sonnet 4.6, sem interface gráfica (`claude -p`), uma invocação por tarefa
- **Juízes:** Qwen3-14B (GPU) + Qwen3-30B-A3B MoE (CPU), pontuação independente, escala de 1-5
- **Isolamento:** PostgreSQL dedicado na porta 5435, separado do desenvolvimento
- **Corpus:** 6.076 entradas — conversas distiladas por LLM + auto-memória segmentada semanticamente + documentos + git
- **Controles:** HOME limpo para new_developer, sem persistência de sessão, mesmo CLAUDE.md base

### Benchmark de Saúde
- **Modelo:** Qwen3-14B-AWQ no vLLM (única RTX 4090, $0 inferência)
- **Juiz:** Qwen3-14B, escala de 1-5 contra rubrica
- **Dados:** Pacientes sintéticos Synthea (Apache 2.0) + cenários clínicos artesanais
- **Simulação:** 180 dias comprimidos em 4,6 horas via simulação de tempo
- **Avaliação:** 40 perguntas em 7 intervalos (dias 1, 30, 60, 90, 120, 150, 180)