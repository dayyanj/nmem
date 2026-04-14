# Benchmark

<!-- i18n:start -->
[English](../../benchmarks/README.md) | [简体中文](../zh-hans/benchmarks.md) | [日本語](../ja/benchmarks.md) | [한국어](../ko/benchmarks.md) | [Español](../es/benchmarks.md) | [Português](../pt/benchmarks.md) | **Français** | [Deutsch](../de/benchmarks.md) | [Русский](../ru/benchmarks.md)
<!-- i18n:end -->


Évaluations empiriques de l'impact de nmem sur les performances des agents. Tous les benchmarks utilisent une méthodologie reproductible avec un matériel local ($0 coût d'inférence).

## Résumé des résultats

| Benchmark | Finding | Métrique clé |
|-----------|---------|------------|
| [Healthcare Multi-Agent](healthcare-multi-agent.md) | Les scores de révision des croyances atteignent **5,00/5 contre 3,13/5 en tant que référence** après les changements de directives — sur un modèle de 14B, GPU unique | +7 % global, +60 % révision des croyances |
| [Spwig Institutional Knowledge](spwig-institutional-knowledge.md) | La recherche MCP de nmem correspond à la précision d'un nouveau développeur à **moitié prix** | 4,27/5 score du juge, $0,097/tâche |
| [Recognition Signals](recognition-signals.md) | Les balises de confiance dans les prompts ne modifient pas le comportement des modèles de 8B-30B | Calcul des reconnaissances mais pas d'injection |

## Benchmark Spwig : chiffres rapides

**Configuration :** plateforme e-commerce de 17 dépôts, 45 tâches de test, 5 variantes, 225 évaluations à double juge.

| Variante | Ce que c'est | Score du juge | Coût |
|---------|-----------|-------------|------|
| **v8_mcp** | L'agent recherche via les outils MCP de nmem | 4,27/5 | **$4,35** |
| new_developer | Aucune mémoire, exploration à partir de zéro | 4,36/5 | $8,18 |
| control | Mémoire automatique de Claude Code (82 % de couverture des faits) | 3,98/5 | $7,06 |
| v8_injected | Mémoire injectée en amont dans le prompt | 4,02/5 | $15,27 |
| v8_briefing | API de briefing avec signaux de reconnaissance | 3,96/5 | $18,36 |

**Insight clé :** La recherche MCP est à la fois la moins chère ET la plus précise car l'agent décide ce qu'il doit rechercher en fonction de chaque question. L'injection préalable devine ce qui pourrait être utile avant même de voir la question.

## Benchmark Healthcare : chiffres rapides

**Configuration :** simulation de 180 jours, 4 agents de santé, 200 patients synthétiques, 1 705 rencontres, 40 questions de test, Qwen3-14B sur RTX 4090 ($0 coût d'inférence).

| Catégorie | nmem | Référence | Delta |
|----------|------|----------|-------|
| **Révision des croyances** | **4,75/5** | 3,21/5 | **+48 %** |
| Rappel direct | 4,09/5 | 3,73/5 | +10 % |
| Détection des motifs | 3,48/5 | 3,33/5 | +4 % |
| Global | 3,84/5 | 3,60/5 | +7 % |

**Insight clé :** La révision des croyances est la différentielle la plus forte de nmem. Lorsque les directives changent, nmem détecte la contradiction, la résout lors de la consolidation et récupère la politique mise à jour. Le LLM de base n'a aucun mécanisme pour distinguer la connaissance actuelle de celle obsolète. Après le jour 120 (toutes les directives changées), nmem obtient une note parfaite de 5,00/5 sur toutes les questions de révision des croyances.

## Configurations testées

| Benchmark | Modèle | Intégration | Contexte |
|-----------|-------|-------------|---------|
| Spwig | Claude Sonnet 4.6 (200K ctx) | Outils MCP | Récupération des connaissances institutionnelles |
| Healthcare | Qwen3-14B-AWQ (GPU de consommation) | API Python + recherche | Consolidation multi-agent au fil du temps |

## Méthodologie

### Benchmark Spwig
- **Agent :** Claude Sonnet 4.6, sans interface graphique (`claude -p`), une invocation par tâche
- **Juges :** Qwen3-14B (GPU) + Qwen3-30B-A3B MoE (CPU), notation indépendante, échelle de 1 à 5
- **Isolation :** PostgreSQL dédié sur le port 5435, séparé du développement
- **Corpus :** 6 076 entrées — conversations distillées par LLM + mémoire auto-fragmentée sémantiquement + documents + git
- **Contrôles :** HOME propre pour new_developer, pas de persistance de session, même CLAUDE.md de base

### Benchmark Healthcare
- **Modèle :** Qwen3-14B-AWQ sur vLLM (un seul RTX 4090, $0 d'inférence)
- **Juge :** Qwen3-14B, notation sur une échelle de 1 à 5 selon le rubrique
- **Données :** Patients synthétiques Synthea (Apache 2.0) + scénarios cliniques manuels
- **Simulation :** 180 jours compressés en 4,6 heures via la simulation du temps
- **Évaluation :** 40 questions à 7 intervalles (jours 1, 30, 60, 90, 120, 150, 180)