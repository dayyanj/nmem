# Benchmark de mémoire d'agents multiples en santé

<!-- i18n:start -->
[English](../../benchmarks/healthcare-multi-agent.md) | [简体中文](../zh-hans/healthcare-multi-agent.md) | [日本語](../ja/healthcare-multi-agent.md) | [한국어](../ko/healthcare-multi-agent.md) | [Español](../es/healthcare-multi-agent.md) | [Português](../pt/healthcare-multi-agent.md) | **Français** | [Deutsch](../de/healthcare-multi-agent.md) | [Русский](../ru/healthcare-multi-agent.md)
<!-- i18n:end -->


## Résumé exécutif

Nous avons construit une simulation de 180 jours d'un service hospitalier avec 4 agents IA (triage, traitement, sortie, pharmacie) traitant 1 705 rencontres cliniques synthétiques provenant de 200 patients. Chaque agent écrit dans le journal de nmem, et le système exécute une consolidation quotidienne, un état de rêve hebdomadaire et une synthèse nocturne bihebdomadaire — compressant 6 mois d'opérations cliniques en un test de 4,6 heures.

Le benchmark teste si la mémoire cognitive améliore les réponses des agents au fil du temps par rapport à un point de référence avec le même LLM mais sans consolidation de la mémoire.

### Principaux résultats

1. **La révision des croyances est la fonctionnalité phare de nmem.** Après des changements de directives, nmem obtient 5,00/5 contre 3,13/5 (augmentation de 60 %) — des réponses parfaites à toutes les questions de révision des croyances à partir du jour 120.
2. **Amélioration globale avec un modèle de 14B sur un matériel de consommation.** nmem obtient 3,84/5 contre 3,60/5 (augmentation de 7 %) sur 205 évaluations, en utilisant Qwen3-14B sur un seul RTX 4090.
3. **nmem gagne 77 questions, perd 47, et se termine à égalité sur 81.** Les victoires se concentrent sur la révision des croyances (+1,54 en moyenne) et la mémorisation directe (+0,36 en moyenne). Les pertes se concentrent sur le raisonnement temporel (-0,39 en moyenne), où la capacité limitée de raisonnement du modèle de 14B est probablement le goulot d'étranglement.
4. **L'écart s'élargit au fil du temps.** Jour 1 : +0,20, Jour 30 : +0,29, Jour 120 : +0,43 — à mesure que plus de directives changent et que les connaissances se consolident, l'avantage de nmem augmente.
5. **Toutes les fonctionnalités sont exercées à grande échelle.** 361 promotions en mémoire à long terme, 56 fusions de doublons, 1 170 conflits résolus automatiquement, 11 motifs d'état de rêve synthétisés, 6 171 liens de connaissances — sur un budget d'inférence de 0 $ (vLLM local).

---

## Méthodologie

### Questions de recherche

1. La mémoire cognitive améliore-t-elle les connaissances cliniques à plusieurs agents sur 6 mois ?
2. nmem révise-t-il correctement les croyances lors des changements de directives ?
3. Le moteur de consolidation (promotion, suppression des doublons, synthèse) produit-il une récupération mesurablement meilleure ?
4. Peut-on utiliser ce travail sur une infrastructure modeste (modèle de 14B, GPU unique) ?

### Conception expérimentale

**Source de données :** [Synthea](https://synthetichealth.github.io/synthea/) générateur de patients synthétiques (Apache 2.0). 1 159 patients générés, les 200 premiers par densité d'encounters sélectionnés pour la fenêtre de simulation.

**Fenêtre de simulation :** 180 jours (du 1er janvier 2025 au 29 juin 2025)

**Agents :**

| Agent | Rôle | Processus |
|-------|------|-----------|
| triage | Évaluation de l'urgence | Tous les encounters — vitales, plainte principale, priorité |
| traitement | Décisions cliniques | Diagnostics, procédures, résultats de laboratoire |
| sortie | Coordination des soins | Plans de soins, suivi, suivi des réadmissions |
| pharmacie | Gestion des médicaments | Ordonnances, allergies, interactions médicamenteuses |

**Scénarios injectés** (événements manuellement conçus que Synthea ne peut pas générer) :

| Jour | Événement | Fonctionnalité testée |
|-----|-------|----------------|
| 30 | Seuil d'hypertension abaissé de 140/90 → 130/80 | Révision des croyances |
| 45 | Rappel de Metformin ER (contamination NDMA) | Propagation inter-agents |
| 50 | Patient diabétique réadmis dans les 30 jours | Détection des motifs de réadmission |
| 75 | Thérapie de deuxième ligne du diabète : sulfonylurées → GLP-1 | Révision des croyances |
| 90 | Bactrim prescrit à un patient avec allergie aux sulfamides | Résolution des conflits |
| 110 | Interaction Lisinopril+potassium : MODÉRÉ → SÉVÈRE | Révision des croyances |
| 120 | Prescription initiale d'opioïdes : 7 jours → 3 jours | Changement de politique |
| 130 | Motif de réadmission le vendredi 3e | Détection des motifs |

**Cycle de simulation quotidien :**