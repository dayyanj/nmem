# Benchmark de Memória de Agentes Múltiplos em Saúde

<!-- i18n:start -->
[English](../../benchmarks/healthcare-multi-agent.md) | [简体中文](../zh-hans/healthcare-multi-agent.md) | [日本語](../ja/healthcare-multi-agent.md) | [한국어](../ko/healthcare-multi-agent.md) | [Español](../es/healthcare-multi-agent.md) | **Português** | [Français](../fr/healthcare-multi-agent.md) | [Deutsch](../de/healthcare-multi-agent.md) | [Русский](../ru/healthcare-multi-agent.md)
<!-- i18n:end -->


## Resumo Executivo

Construímos uma simulação de 180 dias de uma ala hospitalar com 4 agentes de IA (triagem, tratamento, alta, farmácia) processando 1.705 encontros clínicos sintéticos de 200 pacientes. Cada agente escreve no journal da nmem, e o sistema executa diariamente a consolidação, semanalmente o dreamstate e a síntese noturna a cada duas semanas — compactando 6 meses de operações clínicas em uma execução de benchmark de 4,6 horas.

O benchmark testa se a memória cognitiva melhora as respostas dos agentes ao longo do tempo em comparação com uma linha de base com o mesmo LLM, mas sem consolidação de memória.

### Principais Resultados

1. **A revisão de crenças é a característica-chave da nmem.** Após mudanças nas diretrizes, a nmem obtém 5,00/5 contra a linha de base 3,13/5 (+60%) — respostas perfeitas em todas as perguntas de revisão de crenças a partir do dia 120.
2. **Melhora geral com um modelo de 14B em hardware de consumo.** A nmem obtém 3,84/5 contra a linha de base 3,60/5 (+7%) em 205 avaliações, usando Qwen3-14B em uma única GPU RTX 4090.
3. **A nmem vence 77 perguntas, perde 47 e empata 81.** As vitórias concentram-se em revisão de crenças (+1,54 média) e recall direto (+0,36 média). As derrotas concentram-se em raciocínio temporal (-0,39 média), onde a capacidade limitada de raciocínio do modelo de 14B é provavelmente o gargalo.
4. **A vantagem aumenta ao longo do tempo.** Dia 1: +0,20, Dia 30: +0,29, Dia 120: +0,43 — à medida que mais diretrizes mudam e o conhecimento se consolida, a vantagem da nmem cresce.
5. **Todos os recursos exercitados em larga escala.** 361 promoções de LTM, 56 fusões de duplicatas, 1.170 conflitos resolvidos automaticamente, 11 padrões de dreamstate sintetizados, 6.171 links de conhecimento — em um orçamento de inferência de $0 (vLLM local).

---

## Metodologia

### Perguntas de Pesquisa

1. A memória cognitiva melhora o conhecimento clínico de agentes múltiplos ao longo de 6 meses?
2. A nmem revisa corretamente as crenças quando as diretrizes mudam?
3. O motor de consolidação (promoção, dedup, síntese) produz uma recuperação mensuravelmente melhor?
4. Isso pode funcionar em infraestrutura modesta (modelo de 14B, única GPU)?

### Projeto Experimental

**Fonte de dados:** [Synthea](https://synthetichealth.github.io/synthea/) gerador de pacientes sintéticos (Apache 2.0). 1.159 pacientes gerados, os 200 com maior densidade de encontros selecionados para a janela de simulação.

**Janela de simulação:** 180 dias (2025-01-01 a 2025-06-29)

**Agentes:**

| Agente | Função | Processos |
|-------|------|-----------|
| triagem | Avaliação de urgência | Todos os encontros — sinais vitais, queixa principal, prioridade |
| tratamento | Decisões clínicas | Diagnósticos, procedimentos, resultados de laboratório |
| alta | Coordenação de cuidados | Planos de cuidados, acompanhamentos, rastreamento de readmissão |
| farmácia | Gestão de medicamentos | Prescrições, alergias, interações medicamentosas |

**Cenários injetados** (eventos artesanalmente criados que Synthea não pode gerar):

| Dia | Evento | Funcionalidade Testada |
|-----|-------|------------------------|
| 30 | Limiar de hipertensão reduzido de 140/90 → 130/80 | Revisão de crenças |
| 45 | Recall de metformina ER (contaminação NDMA) | Propagação entre agentes |
| 50 | Paciente diabético readmitido dentro de 30 dias | Detecção de padrão de readmissão |
| 75 | Terapia de segunda linha para diabetes: sulfonylureas → GLP-1 | Revisão de crenças |
| 90 | Bactrim prescrito a paciente com alergia a sulfa | Resolução de conflito |
| 110 | Interação lisinopril+potássio: MODERADA → SEVERA | Revisão de crenças |
| 120 | Prescrição inicial de opióides: 7 dias → 3 dias | Mudança de política |
| 130 | Padrão de readmissão de quinta-feira | Detecção de padrão |

**Ciclo diário de simulação:**