# PROJECT_STATUS（新しいセッションが最初に読む現在地ファイル）

最終更新: 2026-09-26 深夜 ／ 最新の機能commit: `66cebda`（2026-09-26・本番反映済み。ANZNのMac移行とN3起動係が稼働中）
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

## 最近完了した変更（日記データを GitHub から排除, 2026-09-26）
- 方針: 日記（#ヘルス・日報）・個人メモ（#インプット）とその派生物は GitHub に置かない。保管先は Google スプレッドシート（GAS）のみ
- `discord_logs.yml`: 取得→同じジョブでシートへ同期（`sync_weekly_sheet.py`）。コミットしない。日記フォーラムは Issue 化しない。Issue のタイトルはログに出さない
- `weekly-sheet-sync.yml`: Discord から直近7日を取り直して同期（repo の logs に依存しない）
- `monthly-mindmap.yml`: コミットとスケジュールを停止（後継は週次の🌕月次観測）。`daily_summary.yml`（停止中）: コミットを停止
- `journal_mindmap_html.yml`: 成果物の保存を廃止
- GAS 送信・取得の例外表示から URL を除去（secret と本文がログに出ないように）
- `_config.yml`: Pages の配信対象から logs/・reports/・mindmap/・state/・scripts/ などを除外
- 再発防止: `scripts/check_no_personal_data.py` と `privacy_guard.yml`（push のたびに検査）、`.gitignore`
- テスト: `tests/test_privacy.py`（11件）。全体は41件
- 追加: #インプットの TODO/BUY → 公開 Issue 起票も停止（discord_logs.yml から issues 権限を削除）。#モーニングジャーナルの投稿ガイドラインに8項目テンプレートを設定し、その固定見出しは週次観測で数えない（`journal_observe.strip_template`）。テスト全51件

## 最近完了した変更（週次観測への再編, 2026-09-26。未push＝本番未反映）
- `weekly_mindmap.py` を「感情4象限の分類＋3大アクション」から「モーニングジャーナルの観測ダッシュボード」へ再編。詳細は `docs/WEEKLY_MINDMAP_SETUP.md`
  - Python（新規 `scripts/journal_observe.py`）: 直近8週を計数し、書いた日数・文字数・語の新規/再登場/増減/不在/継続・表現の出現率・共起・根拠URLを出す
  - Gemini: 観測IDを参照する仮説だけ。指示・評価・断定の文は除去。失敗（402 含む）でも観測のみで投稿
  - 出力: Discord #週間まとめ 2通（A+B / C+D）＋シート「週次観測」タブ。月末週は28日 vs 前28日を別投稿
  - 廃止: 4象限・放射状マップ・3大アクション・週次レビューの週次実行・GitHub Pages 公開・行CSVコミット・アーティファクト保存
- テスト: `tests/test_journal_observe.py`（20件、`.venv/bin/python -m unittest discover -s tests`）

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

## 最近完了した変更（a91c6e2, 2026-09-26 本番反映。news / jma_alerts / nikkei_cnbc_digest の手動実行で success を確認）
- N6 CNBC: 要約できずリンクだけ配信した動画を `pending_summary` に残し、RSSに載っている間（最大72時間・1回4本まで）再要約して追送する
- N5 気象警報: Discordへの送信に失敗した回は state を保存しない（次回に再送）
- N5 fetch_news: 地域・あんぜんねっと・全国・マーケットの送信に失敗したら、その区画で新たに記録した既送信キーを巻き戻す（スポーツ振り分け分と管理キーは残す）
- 残課題: メッセージの一部だけが届いた場合の重複 / スポーツ振り分けの送信失敗 / カルチャーとスポーツ順位の送信失敗時の消失

## 自宅Macで稼働中のジョブ（2026-09-26 23:45 JST 開始）
専用clone: `~/Services/anzn-local/wbc`（main。更新は確認のうえ `git pull --ff-only` を手動で実行）。launchd は両ジョブとも RunAtLoad あり。
| ジョブ | 間隔 | 内容 | state・ログ |
|---|---|---|---|
| `com.rickykogyo.anzn-local` | 15分 | `scripts/anzn_local.py` があんぜんねっとの新着Embedを #webhook_local へ送る。送信に成功したときだけ既送信を確定 | `~/Library/Application Support/anzn-local/anzn_seen.json` / `~/Library/Logs/anzn-local.log` |
| `com.rickykogyo.actions-dispatcher` | 5分 | `scripts/dispatch_workflows.py` が jma（10分）/ news（30分）/ CNBC（60分）を `gh workflow run` で起動する。実行中・間隔の内側なら見送り | `~/Library/Logs/actions-dispatcher.log` |
- Actions 側の設定
  - `news.yml` の `ANZN_SOURCE: "mac"`: あんぜんねっとは取得しない。防災まとめ欄は「自宅Macから配信中」と表示する
  - `MAC_DISPATCHER: "on"`: news の実行間隔が60分を超えたら異常通知する
- 切り替え時の記録: Actions側の既送信キー11件を引き継ぎ、初回の新着は0件（配信済み10件の再送なし）
  - 即時の再実行も0件。Actions の news は `ANZN_SOURCE=mac` でスキップを確認、403は0件
  - 起動係は、23:50 に jma を自動で起動し success
- 手順・停止・ロールバック: `docs/ANZN_LOCAL_SETUP.md` / `docs/ACTIONS_DISPATCHER.md`
- 未対応: あんぜんねっとのジョブ単体の停止検知（news の空きで、起動係の停止は検知できる）

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
- **日記・個人メモの公開（2026-09-26 止血済み・履歴は未除去）**
  - 現行ファイル（logs/health・logs/daily・reports/・mindmap/）は Git 管理から外した。今後の生成もコミットしない（下記「最近完了した変更」）
  - **git 履歴には残っている**: logs/health 26コミット・logs/daily 24コミット・reports/weekly・reports/monthly・mindmap。除去には filter-repo と force push が必要（オーナー判断で未実施）
  - 過去の Actions ログに、GAS 送信 URL（共有シークレットと日記本文入り）が出ていた可能性がある（sync_weekly_sheet の例外表示）。確認・ログ削除・シークレットの再発行が未対応
- **Gemini API のクレジット切れ（402 "prepayment credits are depleted"）**: 2026-09-26 00:36Z 以降、news / nikkei_cnbc_digest / culture_news で発生。
  - 全国ニュース（#webhook_news）は要約0件となり、配信がスキップされる
  - 経済ニュースの整理は、従来の箇条書き表示に戻して配信を継続している
  - Gemini を使うスクリプトは計11本
  - 解消にはオーナーによる AI Studio でのクレジット追加が必要
- **【解決】あんぜんねっとの新着が配信されない不具合**: 5f6031a で修正（元URLを識別キーにする）。本番の定期実行（2026-09-26 20:43 JST・run 36239671584）で、10件を個別キーで記録し、Embedの送信成功を確認。
  - あんぜんねっとに限り、クエリを含む元URLを識別キーにするよう修正（`fetch_anzn_new_items`）
- **【解決・自宅Mac配信へ移行】あんぜんねっとが Actions から 403**: GitHub Actions のIPが拒否されていた（約9割）。2026-09-26 23:45 から自宅Macで取得・配信している（上の「自宅Macで稼働中のジョブ」）。
- **【対策稼働中】スケジュール実行の遅延**: cron の実測は3〜4.5時間おき。2026-09-26 から、自宅Macの起動係が jma・news・CNBC を起動している。Mac が止まると cron の頻度に戻る（news が60分空くと通知）。
- **check.yml（Automated Quality & Link Check）が毎回失敗**: 少なくとも 2026-09-23 から、flake8 の構文チェック（E9,F63,F7,F82）で失敗し続けている。配信処理とは無関係。原因ファイルは未調査。
- **Discordへの送信失敗時の記事消失**: fetch_news・気象警報・CNBC は a91c6e2 で対応済み。カルチャー（送信失敗でも state を保存）とスポーツ順位（送信前に保存）は未対応。

## 未着手・検討中の課題（いずれもオーナー判断で保留中）
- Mac側あんぜんねっと配信の停止検知（heartbeat）… Mac配信へ切り替えた後の後続タスク
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
- 既存テストを優先して利用する（`tests/` は週次観測のみ。他は各スクリプトのドライラン機能を使う）
- 本番コードを変更した場合は、テストと dry-run を行う
- 作業終了時には、必要に応じてこのファイルを更新する
- 推測ではなく、実際に確認した情報を記録する
