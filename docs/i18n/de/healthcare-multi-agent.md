# Gesundheitswesen-Mehrfach-Agenten-Speicherbenchmark

<!-- i18n:start -->
[English](../../benchmarks/healthcare-multi-agent.md) | [简体中文](../zh-hans/healthcare-multi-agent.md) | [日本語](../ja/healthcare-multi-agent.md) | [한국어](../ko/healthcare-multi-agent.md) | [Español](../es/healthcare-multi-agent.md) | [Português](../pt/healthcare-multi-agent.md) | [Français](../fr/healthcare-multi-agent.md) | **Deutsch** | [Русский](../ru/healthcare-multi-agent.md)
<!-- i18n:end -->


## Executive Summary

Wir haben eine 180-tägige Simulation eines Krankenhausflügels mit 4 KI-Agenten (Triage, Behandlung, Entlassung, Apotheke) erstellt, die 1.705 synthetische klinische Begegnungen von 200 Patienten verarbeiten. Jeder Agent schreibt in nmems Journal, und das System führt täglich Konsolidierung, wöchentliche Dreamstate- und zweiwöchentliche Nachtsynthese durch – 6 Monate klinischer Betriebsabläufe werden in einen 4,6-Stunden-Benchmarklauf komprimiert.

Der Benchmark testet, ob kognitive Speicherung die Antworten der Agenten im Laufe der Zeit verbessert, im Vergleich zu einer Baseline mit demselben LLM, aber ohne Speicherungskonsolidierung.

### Headline Findings

1. **Glaubensrevision ist der Schlüsselmerkmal von nmem.** Nach Änderungen der Leitlinien erzielt nmem 5,00/5 vs. Baseline 3,13/5 (+60 %) – perfekte Antworten auf alle Glaubensrevision-Fragen ab Tag 120.
2. **Gesamtverbesserung mit einem 14B-Modell auf Consumer-Hardware.** nmem erzielt 3,84/5 vs. Baseline 3,60/5 (+7 %) bei 205 Bewertungen, mit Qwen3-14B auf einem einzigen RTX 4090.
3. **nmem gewinnt 77 Fragen, verliert 47, teilt 81.** Die Siege konzentrieren sich auf Glaubensrevision (+1,54 Durchschnitt) und direkte Erinnerung (+0,36 Durchschnitt). Verluste konzentrieren sich auf zeitliche Schlussfolgerung (-0,39 Durchschnitt), wobei die begrenzte Schlussfolgerungsfähigkeit des 14B-Modells wahrscheinlich der Engpass ist.
4. **Der Abstand wird mit der Zeit größer.** Tag 1: +0,20, Tag 30: +0,29, Tag 120: +0,43 – je mehr Leitlinien sich ändern und Wissen konsolidiert, desto größer wird der Vorteil von nmem.
5. **Alle Funktionen werden in großem Maßstab getestet.** 361 LTM-Erhebungen, 56 Duplikat-Verknüpfungen, 1.170 Konflikte automatisch gelöst, 11 Dreamstate-Muster synthetisiert, 6.171 Wissensverknüpfungen – mit einem $0 Inferenzbudget (lokale vLLM).

---

## Methodik

### Forschungsfragen

1. Verbessert kognitive Speicherung klinisches Wissen von Mehrfach-Agenten über 6 Monate?
2. Revidiert nmem korrekt Glaubenssätze, wenn Leitlinien sich ändern?
3. Erzeugt der Konsolidierungsengine (Erhöhung, Duplikat-Verknüpfung, Synthese) messbar bessere Retrieval?
4. Funktioniert dies auf begrenzter Infrastruktur (14B-Modell, ein GPU)?

### Experimentelles Design

**Datenquelle:** [Synthea](https://synthetichealth.github.io/synthea/) synthetischer Patientengenerator (Apache 2.0). 1.159 Patienten generiert, Top 200 nach Begegnungsdichte für den Simulationsschalter ausgewählt.

**Simulationsschalter:** 180 Tage (2025-01-01 bis 2025-06-29)

**Agenten:**

| Agent | Rolle | Verarbeitet |
|-------|------|-----------|
| triage | Dringlichkeitseinschätzung | Alle Begegnungen – Vitalzeichen, Hauptbeschwerde, Priorität |
| treatment | Klinische Entscheidungen | Diagnosen, Verfahren, Laborergebnisse |
| discharge | Pflegekoordination | Pflegepläne, Nachsorge, Wiederbelegungsspur |
| pharmacy | Medikamentenmanagement | Rezepte, Allergien, Arzneimittelinteraktionen |

**Eingefügte Szenarien** (handgefertigte Ereignisse, die Synthea nicht generieren kann):

| Tag | Ereignis | Getestete Funktion |
|-----|-------|----------------|
| 30 | Hypertonie-Schwellenwert gesenkt 140/90 → 130/80 | Glaubensrevision |
| 45 | Metformin ER-Rückruf (NDMA-Verunreinigung) | Cross-agent-Verbreitung |
| 50 | Diabetiker wird innerhalb von 30 Tagen erneut belegt | Wiederbelegungsmustererkennung |
| 75 | Diabetes 2. Linie-Therapie: Sulfonylurea → GLP-1 | Glaubensrevision |
| 90 | Bactrim wird einem Patienten mit Sulfa-Allergie verordnet | Konfliktlösung |
| 110 | Lisinopril+Kalium-Interaktion: MODERATE → SEVERE | Glaubensrevision |
| 120 | Opioid-Initialrezept: 7 Tage → 3 Tage | Politikänderung |
| 130 | Dritter Freitag-Entlassung-Wiederbelegungsmuster | Mustererkennung |

**Täglicher Simulationsschalter:**