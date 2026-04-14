# ベンチマーク

<!-- i18n:start -->
[English](../../benchmarks/README.md) | [简体中文](../zh-hans/benchmarks.md) | **日本語** | [한국어](../ko/benchmarks.md) | [Español](../es/benchmarks.md) | [Português](../pt/benchmarks.md) | [Français](../fr/benchmarks.md) | [Deutsch](../de/benchmarks.md) | [Русский](../ru/benchmarks.md)
<!-- i18n:end -->


nmemのエージェント性能への影響に関する実証評価。すべてのベンチマークは、ローカルハードウェア（$0の判定コスト）で再現可能な方法論を使用しています。

## 結果の概要

| ベンチマーク | 発見 | 主要なメトリクス |
|-----------|---------|------------|
| [Spwig Institutional Knowledge](spwig-institutional-knowledge.md) | nmemのMCP検索は、新規開発者の精度と同等であり、コストはその半分に抑えられる | 4.27/5の判定スコア、$0.097/タスク |
| [Recognition Signals](recognition-signals.md) | プロンプト内の信頼タグは8B-30Bモデルの挙動に変化をもたらさない | 認識は計算されるが注入されない |

## Spwigベンチマーク: 簡易な数値

**セットアップ:** 17リポジトリのeCommerceプラットフォーム、45のテストタスク、5つのバリアント、225の双子判定評価。

| バリアント | 概要 | 判定スコア | コスト |
|---------|-----------|-------------|------|
| **v8_mcp** | エージェントはMCPツールを通じてnmemを検索する | 4.27/5 | **$4.35** |
| new_developer | メモリなし、スクラッチから探索する | 4.36/5 | $8.18 |
| control | Claude Codeの自動メモリ（82%の事実カバレッジ） | 3.98/5 | $7.06 |
| v8_injected | メモリをプロンプトに事前に注入する | 4.02/5 | $15.27 |
| v8_briefing | 認識シグナル付きのBriefing API | 3.96/5 | $18.36 |

**主要な洞察:** MCP検索は、エージェントが各質問に基づいて検索する内容を決定するため、最も安価であり、かつ正確性も高い。事前に注入されたメモリは、質問を確認する前に何が役に立つかを推測している。

## 現在の範囲

これまでのすべてのベンチマークは **Claude Code (Sonnet 4.6, 200K context)** を使用しています — MCP統合は検証済みのユースケースです。8B-30B（8K-32K context）の小さなモデルを用いたエージェントユースケースは、今後のロードマップに含まれます。詳細については [Scope & Limitations](spwig-institutional-knowledge.md#scope--limitations) を参照してください。

## 方法論

- **エージェント:** Claude Sonnet 4.6、ヘッドレス（`claude -p`）、タスクごとに単一の呼び出し
- **判定者:** Qwen3-14B（GPU）+ Qwen3-30B-A3B MoE（CPU）、独立したスコアリング、1-5スケール
- **分離:** ポート5435の専用PostgreSQL、開発環境とは分離
- **コーパス:** 6,076エントリ — LLMで精錬された会話 + 論理的にチャンク化された自動メモリ + ドキュメント + git
- **制御:** new_developerのためのクリーンなHOME、セッションの永続化なし、同じCLAUDE.mdベース