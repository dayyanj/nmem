# Benchmarks

<!-- i18n:start -->
[English](../../benchmarks/README.md) | [简体中文](../zh-hans/benchmarks.md) | [日本語](../ja/benchmarks.md) | [한국어](../ko/benchmarks.md) | [Español](../es/benchmarks.md) | [Português](../pt/benchmarks.md) | [Français](../fr/benchmarks.md) | **Deutsch** | [Русский](../ru/benchmarks.md)
<!-- i18n:end -->


Empirische Bewertungen des Einflusses von nmem auf die Agentenleistung. Alle Benchmarks verwenden eine reproduzierbare Methode mit lokaler Hardware ($0 Inferenzkosten).

## Zusammenfassung der Ergebnisse

| Benchmark | Erkenntnis | Schlüsselmetric |
|-----------|---------|------------|
| [Healthcare Multi-Agent](healthcare-multi-agent.md) | Glaubensrevisionsscores **5,00/5 vs. 3,13/5 Baseline** nach Änderung der Leitlinien – bei einem 14B-Modell, Einzel-GPU-Consumer | +7 % insgesamt, +60 % Glaubensrevision |
| [Spwig Institutional Knowledge](spwig-institutional-knowledge.md) | nmem MCP-Suche entspricht der Genauigkeit eines neuen Entwicklers bei **halb dem Kosten** | 4,27/5 Richterscore, $0,097/Aufgabe |
| [Recognition Signals](recognition-signals.md) | Vertrauensmarken in Prompts ändern das Verhalten von 8B-30B-Modellen nicht | Erkennung wird berechnet, aber nicht injiziert |

## Spwig-Benchmark: Schnelle Zahlen

**Einrichtung:** 17-Repo-eCommerce-Plattform, 45 Testaufgaben, 5 Varianten, 225 Doppel-Richter-Bewertungen.

| Variante | Was es ist | Richterscore | Kosten |
|---------|-----------|-------------|------|
| **v8_mcp** | Agent sucht in nmem über MCP-Tools | 4,27/5 | **$4,35** |
| new_developer | Kein Speicher, erkundet von Grund auf | 4,36/5 | $8,18 |
| control | Claude Code automatischer Speicher (82 % Faktabdeckung) | 3,98/5 | $7,06 |
| v8_injected | Speicher vorab in den Prompt injiziert | 4,02/5 | $15,27 |
| v8_briefing | Briefing-API mit Erkennungssignalen | 3,96/5 | $18,36 |

**Schlüsselinsight:** MCP-Suche ist sowohl **am günstigsten** als auch **am präzisesten**, da der Agent entscheidet, wonach gesucht werden soll, basierend auf jeder Frage. Vorab injizierte Speicher raten, was nützlich sein könnte, bevor die Frage gesehen wird.

## Healthcare-Benchmark: Schnelle Zahlen

**Einrichtung:** 180-Tage-Simulation, 4 Gesundheitsagenten, 200 synthetische Patienten, 1 705 Begegnungen, 40 Testfragen, Qwen3-14B auf RTX 4090 ($0 Inferenzkosten).

| Kategorie | nmem | Baseline | Delta |
|----------|------|----------|-------|
| **Glaubensrevision** | **4,75/5** | 3,21/5 | **+48 %** |
| Direkter Rückruf | 4,09/5 | 3,73/5 | +10 % |
| Mustererkennung | 3,48/5 | 3,33/5 | +4 % |
| Gesamt | 3,84/5 | 3,60/5 | +7 % |

**Schlüsselinsight:** Glaubensrevision ist der stärkste Unterscheidungsmerkmal von nmem. Wenn sich Leitlinien ändern, erkennt nmem die Widersprüchlichkeit, löst sie bei der Konsolidierung und ruft die aktualisierte Politik ab. Die Baseline-LLM hat kein Mechanismus, um aktuelles von veralteten Kenntnissen zu unterscheiden. Nach Tag 120 (alle Leitlinien geändert) erzielt nmem eine perfekte 5,00/5 bei jeder Glaubensrevision-Frage.

## Getestete Konfigurationen

| Benchmark | Modell | Integration | Kontext |
|-----------|-------|-------------|---------|
| Spwig | Claude Sonnet 4.6 (200K ctx) | MCP-Tools | Abruf institutioneller Kenntnisse |
| Healthcare | Qwen3-14B-AWQ (Consumer-GPU) | Python-API + Suche | Multi-Agenten-Konsolidierung über die Zeit |

## Methodik

### Spwig-Benchmark
- **Agent:** Claude Sonnet 4.6, headless (`claude -p`), eine Aufruf pro Aufgabe
- **Richter:** Qwen3-14B (GPU) + Qwen3-30B-A3B MoE (CPU), unabhängige Bewertung, Skala 1–5
- **Isolation:** Dedizierter PostgreSQL-Server auf Port 5435, getrennt von der Entwicklung
- **Korpus:** 6 076 Einträge – LLM-destillierte Gespräche + semantisch in Stücke geteilte automatische Speicher + Dokumente + Git
- **Kontrollen:** Sauberer HOME für new_developer, keine Sitzungspersistenz, gleiche CLAUDE.md-Base

### Healthcare-Benchmark
- **Modell:** Qwen3-14B-AWQ auf vLLM (einzelne RTX 4090, $0 Inferenz)
- **Richter:** Qwen3-14B, Skala 1–5 gemäß Bewertungskriterien
- **Daten:** Synthea-synthetische Patienten (Apache 2.0) + handgefertigte klinische Szenarien
- **Simulation:** 180 Tage in 4,6 Stunden komprimiert durch Zeitsimulation
- **Bewertung:** 40 Fragen an 7 Intervallen (Tage 1, 30, 60, 90, 120, 150, 180)