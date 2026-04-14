# Benchmarks

<!-- i18n:start -->
[English](../../benchmarks/README.md) | [简体中文](../zh-hans/benchmarks.md) | [日本語](../ja/benchmarks.md) | [한국어](../ko/benchmarks.md) | [Español](../es/benchmarks.md) | [Português](../pt/benchmarks.md) | [Français](../fr/benchmarks.md) | **Deutsch** | [Русский](../ru/benchmarks.md)
<!-- i18n:end -->


Empirische Bewertungen des Einflusses von nmem auf die Agentenleistung. Alle Benchmarks verwenden eine reproduzierbare Methodik mit zweifacher Richterbewertung auf lokaler Hardware ($0 Bewertungskosten).

## Ergebnisseübersicht

| Benchmark | Ergebnis | Schlüsselmetric |
|-----------|---------|------------|
| [Spwig Institutional Knowledge](spwig-institutional-knowledge.md) | nmem MCP-Suche entspricht der Genauigkeit eines neuen Entwicklers bei **halb dem Kosten** | 4,27/5 Richterscore, $0,097/Aufgabe |
| [Recognition Signals](recognition-signals.md) | Vertrauensmarken in Prompts verändern das Verhalten von 8B-30B-Modellen nicht | Erkennung wird berechnet, aber nicht injiziert |

## Spwig-Benchmark: Schnelle Zahlen

**Einrichtung:** 17-Repo eCommerce-Plattform, 45 Testaufgaben, 5 Varianten, 225 zweifache Richterbewertungen.

| Variante | Was es ist | Richterscore | Kosten |
|---------|-----------|-------------|------|
| **v8_mcp** | Agent durchsucht nmem über MCP-Tools | 4,27/5 | **$4,35** |
| new_developer | Kein Speicher, erkundet von Grund auf | 4,36/5 | $8,18 |
| control | Claude Code automatischer Speicher (82 % Faktabdeckung) | 3,98/5 | $7,06 |
| v8_injected | Speicher vorab in den Prompt injiziert | 4,02/5 | $15,27 |
| v8_briefing | Briefing-API mit Erkennungssignalen | 3,96/5 | $18,36 |

**Schlüsselinsight:** MCP-Suche ist sowohl am günstigsten ALS auch am genauesten, weil der Agent entscheidet, wonach gesucht werden soll, basierend auf jeder Frage. Vorab injizierte Speicher raten, was nützlich sein könnte, bevor die Frage gesehen wird.

## Aktueller Umfang

Alle Benchmarks bislang verwenden **Claude Code (Sonnet 4.6, 200K Kontext)** – die MCP-Integration ist der validierte Anwendungsfall. Agente-Anwendungsbeispiele mit kleineren Modellen (8B-30B, 8K-32K Kontext) stehen als nächstes auf der Roadmap. Siehe [Umfang & Einschränkungen](spwig-institutional-knowledge.md#scope--limitations) für Details.

## Methodik

- **Agent:** Claude Sonnet 4.6, headless (`claude -p`), eine Einladung pro Aufgabe
- **Richter:** Qwen3-14B (GPU) + Qwen3-30B-A3B MoE (CPU), unabhängige Bewertung, Skala 1-5
- **Isolierung:** Dedicierter PostgreSQL auf Port 5435, getrennt von der Entwicklung
- **Korpus:** 6.076 Einträge – LLM-destillierte Gespräche + semantisch in Stücke geteilte automatische Speicher + Dokumente + Git
- **Kontrollen:** Sauberer HOME für new_developer, keine Sitzungspersistenz, gleicher CLAUDE.md-Grundlage