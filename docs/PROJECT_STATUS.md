# PROJECT_STATUS（新しいセッションが最初に読む現在地ファイル）

最終更新: 2026-09-26 ／ 最新の機能commit: `950eba7`（2026-09-26・本番反映済み）
※この後にある `github-actions[bot]` の「〜既送信記録を更新」commitは state 自動更新のみ。

## 目的
GitHub Actions の定期実行で、ニュース・市況・地域情報・趣味（バドミントン等）を収集し、Discord の各チャンネルへ自動配信する個人用の情報基盤（リポジトリ: `c6cgv9cnj4-ops/wbc`）。

## 現在の本番状態（2026-09-26 確認）
- 直近40回の Actions 実行はすべて success（ただし実行結果が success でも、下記「現在確認されている問題」が発生している）。
- 950eba7 反映後の手動実行（run 36220160602）で確認した内容
  - 異常通知が #webhook_local（あんぜんねっと403）と #webhook_news（Gemini 402）に1通ずつ届いた
  - 全国ニュースは、記録を取り消してスキップ（次回以降に再試行）
  - 地域ニュースは配信された
  - マーケットは、土日の重複配信抑制で送信スキップ（既存の仕様どおり）`ニュース自動配信`（news.yml）は a9d6628 反映後も連続で success。
- a9d6628 反映後の手動実行（run 36090911374）で確認した内容
  - Reuters/Bloomberg の古い記事の除外が動作
  - 経済ニュースを Gemini で材料ごとに整理（判定失敗 0・フォールバック 0・Discord送信エラー 0）
  - #webhook_market / #webhook_local / #webhook_news へ配信

## 主要機能（ニュース配信まわり）
| チャンネル | 生成元 | 内容 |
|---|---|---|
| #webhook_market | `scripts/fetch_news.py` | 株価・先物・コモディティ・国債先物＋主要経済ニュース（材料ごとに集約し🔴🟡⚪順） |
| #webhook_market | `scripts/fetch_nikkei_cnbc_digest.py` | 日経CNBC動画の要約 |
| #webhook_local | `scripts/fetch_news.py` | あんぜんねっと（北本市）＋埼玉県央ローカルニュース |
| #webhook_news | `scripts/fetch_news.py` | 全国主要ニュース（Yahoo!トップピックス＋Gemini要約） |
| #webhook_news | `scripts/fetch_culture_news.py` | 国債・カルチャー・展覧会 |

## 最近完了した変更（1cae86b・950eba7, 2026-09-26 本番反映。手動実行 run 36220160602 で確認）
- 失敗の可視化: `scripts/news_alerts.py`（新規）＋ `fetch_news.py` に記録用の1行×9か所、`market_news_curation.py` に `LAST_ERROR` を追加。
  - 次の異常を実行の最後にまとめ、影響を受けたチャンネルへ「⚠️ ニュース自動配信で異常を検知」として1通送る
    - あんぜんねっとの取得失敗
    - 全国ニュースのGemini失敗・配信スキップ
    - 経済ニュース整理のGemini失敗
    - Google News取得失敗
    - Discordへの送信失敗
  - そのチャンネルへの送信に失敗した場合は、他のWebhookへ送る
  - 同じ種類の異常は12時間は再通知しない（`state/news_seen.json` の `_alert:<種類>` キーに最終通知時刻を記録）
  - 通常投稿の内容は変更前と完全一致することを、検証ハーネスで確認済み
- 全国ニュースのGemini失敗時の記事消失を修正: `summarize_national_news()` は、Gemini失敗時に None を、正常に0件なら [] を返すよう変更。
  - None のときだけ、その回の全国候補の既送信記録を取り消し、次回以降に再試行する
  - 障害中に RSS から押し出された記事は取り戻せない

## 最近完了した変更（a9d6628, 2026-09-25）
- `scripts/market_news_curation.py`（新規）
  - 経済ニュースのうち、同じ材料の記事を1グループに集約し、カテゴリと重要度（🔴🟡⚪）を付ける
  - Gemini には記事番号だけを返させ、リンクや見出しは元データから組み立てる
  - 失敗時は従来の箇条書きに戻す（記事は捨てない）
  - 🔴 は正式発表・政策判断・重大な市場イベントに限る。途中経過・見通し・解説・通常の市況は🟡以下
- `scripts/fetch_news.py`
  - Reuters/ブルームバーグの2クエリに限り、公開から72時間を超えた記事を除外（数年前の記事が新着扱いされていた不具合の修正）
  - `chunk_message()` が「　└ 」で始まるリンク行を、直前の見出し行と同じメッセージに収めるよう変更（他チャンネルの分割結果は不変）
- `scripts/dry_run_market_curation.py`（新規）: 送信・state更新なしのドライラン

## 重要ファイル
- `.github/workflows/*.yml` … 定期実行の定義（下表）
- `scripts/` … 各ワークフローが実行する本体
- `state/*.json` … 既送信記録。Actions が実行ごとに自動でcommitする（`news_seen.json` は14日保持）
- `requirements.txt` … 依存パッケージ。ローカル実行は `.venv/bin/python` を使う

## GitHub Actions / cron（主要なもの）
| workflow | cron (UTC) | JST目安 | 本体 |
|---|---|---|---|
| news.yml | `3,33 * * * *` | 30分ごと（実際はGitHubの遅延で2〜5時間おきの不定期） | fetch_news.py |
| nikkei_cnbc_digest.yml | `20 * * * *` | 毎時 | fetch_nikkei_cnbc_digest.py |
| culture_news.yml | `13 22/3/9` | 7:13 / 12:13 / 18:13 | fetch_culture_news.py |
| jma_alerts.yml | 10分ごと | — | fetch_jma_alerts.py |
| oshi_news.yml | `38 * * * *` | 毎時 | fetch_oshi_news.py |
| badminton_alerts.yml | 15分〜毎時 | — | fetch_badminton_alerts.py |

その他の日次・週次・月次ジョブ（deals / daily_summary / discord_logs / npb_results / sports_standings / weekly_mindmap / monthly-mindmap / price_check など）は、`.github/workflows/` の各ファイルを参照。

## 現在確認されている問題（2026-09-26 確認）
※ いずれも Actions の実行結果は success のまま。ログの ERROR/WARN にしか出ないため、気付きにくい。
- **Gemini API のクレジット切れ（402 "prepayment credits are depleted"）**: 2026-09-26 00:36Z 以降、news / nikkei_cnbc_digest / culture_news で発生。
  - 全国ニュース（#webhook_news）は要約0件となり、配信がスキップされる
  - 経済ニュースの整理は、従来の箇条書き表示に戻して配信を継続している
  - Gemini を使うスクリプトは計11本
  - 解消にはオーナーによる AI Studio でのクレジット追加が必要
- **あんぜんねっと（北本市安全安心情報）が 403 Forbidden**: GitHub Actions から取得すると、2026-09-10 以降およそ8割の実行で失敗している。
  - ローカル（Mac）からの取得は正常（200）。原因はクラウド側IPからのアクセス拒否と判断
- **スケジュール実行の大幅な遅延**: 実際の起動間隔は、設定値に関係なく中央値3〜4.5時間（直近約2週間の実測）。
  - news（30分設定）: 234分
  - jma_alerts（10分設定）: 200分
  - badminton_alerts（15分設定）: 183分
- **Discordへの送信失敗時は、記事が既送信の記録のまま失われる**（全チャンネル共通。state は送信前に記録される）。異常通知は届く。

## 未着手・検討中の課題（いずれもオーナー判断で保留中）
- #webhook_market の朝・昼・夜の固定配信化 … Phase 1 を運用してから判断
- 経済ニュースの個別企業に「企業名＋証券コード」を付ける … Phase 2（コードはAIで推測せず、辞書で引く方針）
- `site:reuters.com/markets/japan` クエリは、直近の記事を返さない（72時間フィルタ後は0件）。差し替えるか廃止するかは未定
- 日銀・FRBの政策決定日など、🔴が付くべき日の判定は、実データでまだ確認していない

## 本番反映時の検証手順
1. 構文チェック: `.venv/bin/python -m py_compile scripts/<変更ファイル>.py`
2. ドライラン（経済ニュース整理の場合）: `.venv/bin/python scripts/dry_run_market_curation.py [--use-state PATH] [--input PATH]`
   - Discordへの送信なし・state更新なし
   - 環境変数に GEMINI_API_KEY が必要（ローカルの .env から、値を表示せずに読み込む）
3. `git pull --rebase origin main` → commit → push（bot が state を頻繁にcommitするため、pull が必須）
4. `gh workflow run news.yml --ref main` → `gh run watch <id>` で success を確認し、ログで除外件数・フォールバック・送信エラーを確認
5. すぐ無効化する方法: 環境変数 `MARKET_NEWS_CURATION=off` で、経済ニュースを従来の箇条書き表示に戻せる
6. fetch_news.py 全体の送信なし検証: Discordへの送信・Gemini・通信を差し替えて main() を実行し、変更前のコードと投稿内容を比較する（今回はスクラッチ上のハーネスで実施。リポジトリには未収録）

## 今後の作業原則
- 既存機能を壊さない。変更は最小限にする
- 必要なファイルだけ調査する。同じファイルを何度も読み直さない
- Python等で可能な集計・比較・検証は Claude 自身が実行する
- 大量のコードやログをユーザーに提示しない
- 実装・テスト・dry-run まで、可能な限り一括して進める
- 既存テストを優先して利用する（現状、リポジトリに `tests/` は無い。各スクリプトのドライラン機能を使う）
- 本番コードを変更した場合は、テストと dry-run を行う
- 作業終了時には、必要に応じてこのファイルを更新する
- 推測ではなく、実際に確認した情報を記録する
