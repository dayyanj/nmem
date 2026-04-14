# Benchmarks

<!-- i18n:start -->
[English](../../benchmarks/README.md) | [简体中文](../zh-hans/benchmarks.md) | [日本語](../ja/benchmarks.md) | [한국어](../ko/benchmarks.md) | [Español](../es/benchmarks.md) | [Português](../pt/benchmarks.md) | **Français** | [Deutsch](../de/benchmarks.md) | [Русский](../ru/benchmarks.md)
<!-- i18n:end -->


Évaluations empiriques de l'impact de nmem sur les performances des agents. Tous les benchmarks utilisent une méthodologie reproductible avec une notation par deux juges sur du matériel local ($0 coût de notation).

## Résumé des résultats

| Benchmark | Résultat | Métrique clé |
|-----------|---------|------------|
| [Spwig Connaissance institutionnelle](spwig-institutional-knowledge.md) | La recherche MCP de nmem correspond à la précision d'un nouveau développeur à **moitié prix** | 4.27/5 score des juges, $0.097/tâche |
| [Signaux de reconnaissance](recognition-signals.md) | Les balises de confiance dans les prompts ne modifient pas le comportement des modèles 8B-30B | Calcul des signaux de reconnaissance mais non injectés |

## Benchmark Spwig : chiffres rapides

**Configuration :** plateforme e-commerce avec 17 dépôts, 45 tâches de test, 5 variantes, 225 évaluations par deux juges.

| Variante | Ce que c'est | Score des juges | Coût |
|---------|-----------|-------------|------|
| **v8_mcp** | L'agent recherche via les outils MCP | 4.27/5 | **$4.35** |
| new_developer | Aucune mémoire, exploration à partir de zéro | 4.36/5 | $8.18 |
| control | Mémoire auto de Claude Code (82 % de couverture des faits) | 3.98/5 | $7.06 |
| v8_injected | Mémoire injectée préalablement dans le prompt | 4.02/5 | $15.27 |
| v8_briefing | API de briefing avec signaux de reconnaissance | 3.96/5 | $18.36 |

**Insight clé :** La recherche MCP est à la fois la moins chère ET la plus précise car l'agent décide ce qu'il doit rechercher en fonction de chaque question. L'injection préalable devine ce qui pourrait être utile avant même de voir la question.

## Portée actuelle

Tous les benchmarks à ce jour utilisent **Claude Code (Sonnet 4.6, 200K contexte)** — l'intégration MCP est le cas d'usage validé. Les cas d'utilisation agents avec des modèles plus petits (8B-30B, 8K-32K contexte) figurent sur la roadmap suivante. Voir [Portée et limites](spwig-institutional-knowledge.md#scope--limitations) pour plus de détails.

## Méthodologie

- **Agent :** Claude Sonnet 4.6, sans interface (`claude -p`), une seule invocation par tâche
- **Juges :** Qwen3-14B (GPU) + Qwen3-30B-A3B MoE (CPU), notation indépendante, échelle 1-5
- **Isolation :** PostgreSQL dédié sur le port 5435, séparé du développement
- **Corpus :** 6 076 entrées — conversations distillées par LLM + mémoire auto segmentée sémantiquement + documents + git
- **Contrôles :** HOME propre pour new_developer, pas de persistance de session, même base CLAUDE.md