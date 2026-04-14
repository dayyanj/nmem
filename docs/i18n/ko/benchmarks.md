# 벤치마크

<!-- i18n:start -->
[English](../../benchmarks/README.md) | [简体中文](../zh-hans/benchmarks.md) | [日本語](../ja/benchmarks.md) | **한국어** | [Español](../es/benchmarks.md) | [Português](../pt/benchmarks.md) | [Français](../fr/benchmarks.md) | [Deutsch](../de/benchmarks.md) | [Русский](../ru/benchmarks.md)
<!-- i18n:end -->


nmem이 에이전트 성능에 미치는 영향에 대한 실증 평가. 모든 벤치마크는 로컬 하드웨어에서 이중 판정자 점수를 사용한 재현 가능한 방법론을 사용합니다($0 판정 비용).

## 결과 요약

| 벤치마크 | 발견 | 주요 지표 |
|-----------|---------|------------|
| [스피그 기관 지식](spwig-institutional-knowledge.md) | nmem MCP 검색은 새로운 개발자의 정확도와 **반값의 비용**으로 일치 | 4.27/5 판정 점수, $0.097/작업 |
| [인식 신호](recognition-signals.md) | 프롬프트의 신뢰 태그는 8B-30B 모델의 행동을 변경하지 않음 | 인식은 계산되지만 주입되지 않음 |

## 스피그 벤치마크: 빠른 수치

**설정:** 17개 저장소의 전자상거래 플랫폼, 45개 테스트 작업, 5가지 변형, 225개 이중 판정 평가.

| 변형 | 무엇을 의미하는가 | 판정 점수 | 비용 |
|---------|-----------|-------------|------|
| **v8_mcp** | 에이전트가 MCP 도구를 통해 nmem을 검색 | 4.27/5 | **$4.35** |
| new_developer | 메모리 없이 처음부터 탐색 | 4.36/5 | $8.18 |
| control | Claude Code 자동 메모리 (82% 사실 커버리지) | 3.98/5 | $7.06 |
| v8_injected | 메모리가 프롬프트에 사전 주입됨 | 4.02/5 | $15.27 |
| v8_briefing | 인식 신호와 함께 briefing API 사용 | 3.96/5 | $18.36 |

**핵심 통찰:** MCP 검색은 에이전트가 각 질문에 따라 무엇을 검색할지 결정하기 때문에 가장 저렴하면서도 정확하다. 사전 주입은 질문을 보지 않고도 유용할 수 있는 내용을 추측한다.

## 현재 범위

현재까지의 모든 벤치마크는 **Claude Code (Sonnet 4.6, 200K 컨텍스트)**를 사용했다 — MCP 통합은 검증된 사용 사례이다. 더 작은 모델(8B-30B, 8K-32K 컨텍스트)을 사용한 에이전트 사용 사례는 다음에 계획되어 있다. 자세한 내용은 [범위 및 한계](spwig-institutional-knowledge.md#scope--limitations)를 참조하라.

## 방법론

- **에이전트:** Claude Sonnet 4.6, 헤드리스(`claude -p`), 작업당 단일 호출
- **판정자:** Qwen3-14B (GPU) + Qwen3-30B-A3B MoE (CPU), 독립적인 점수, 1-5 척도
- **분리:** 포트 5435에서 실행되는 독립적인 PostgreSQL, 개발과 분리됨
- **코퍼스:** 6,076개 항목 — LLM으로 정리된 대화 + 의미적으로 청크화된 자동 메모리 + 문서 + git
- **제어:** new_developer의 경우 HOME이 깨끗함, 세션 지속 없음, 동일한 CLAUDE.md 기반