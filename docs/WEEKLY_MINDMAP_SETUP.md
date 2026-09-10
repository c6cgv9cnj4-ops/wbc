# 週次棚卸しマインドマップ — セットアップ & テスト手順

毎週日曜 20:00 JST に、直近1週間の Discord モーニングジャーナル / Google カレンダー /
Google Tasks を集約し、

1. 円形放射状マインドマップ画像（PNG）を Discord `# 週間まとめ` チャンネルへ投稿
2. Google スプレッドシートの週次タブ `YYYY_Www`（A:大分類 / B:中分類 / C:トピック /
   D:採用チェックボックス）へ追記

する。

| ファイル | 役割 |
|---|---|
| `scripts/weekly_mindmap.py` | 収集 → Gemini 構造化 → PNG 描画 → Sheets 追記 → Discord 投稿 |
| `scripts/mint_google_oauth_token.py` | OAuth リフレッシュトークン発行（ローカルで1回） |
| `.github/workflows/weekly_mindmap.yml` | 定期実行（cron `0 11 * * 0` = 日曜 20:00 JST）|
| `requirements-weekly.txt` | 追加依存（`google-api-python-client` / `google-auth` / `matplotlib`）|
| `reports/weekly/YYYY_Www_mindmap.png` | マインドマップ画像（Actions アーティファクトのみ。リポジトリには置かない）|
| `reports/weekly/YYYY_Www_rows.csv` | 行データ（リポジトリにコミット & アーティファクト）|

---

## 1. 認証設計

Google 側（Calendar 読取・Tasks 読取・Sheets 書込）は **1本の OAuth リフレッシュ
トークン** に集約する。個人 Gmail（`rikihosokawa@gmail.com`）はサービスアカウントから
Google Tasks や個人カレンダーを読めないため、本人アカウントの OAuth 同意でトークンを作る。
家計簿システム（`ricky-kakeibo`）のサービスアカウントとは別系統。

Discord は既存の `DISCORD_BOT_TOKEN`（`export_discord_logs.py` と共用）でジャーナルを読み、
投稿は `# 週間まとめ` の Webhook を新規に作って使う。

---

## 2. OAuth リフレッシュトークンの発行（1回だけ）

### 2-1. Google Cloud Console

1. プロジェクトを用意（家計簿と同じ GCP プロジェクトで可）。
2. **API とサービス > ライブラリ** で以下を有効化：
   - Google Calendar API
   - Google Tasks API
   - Google Sheets API
3. **OAuth 同意画面**：
   - User Type = 外部
   - スコープ： `.../auth/calendar.readonly`, `.../auth/tasks.readonly`, `.../auth/spreadsheets`
   - **⚠️ 公開ステータスを必ず「本番（In production）」にする。**
     「テスト」のままだとリフレッシュトークンが **7 日で自動失効** し、週1回実行の
     このジョブは2回目以降必ず認証エラーになる。上記スコープはどれも「制限付き」では
     ないので、本番公開に Google の審査は不要（ボタン一つで切り替わる）。
4. **認証情報 > 認証情報を作成 > OAuth クライアント ID**：
   - 種類 = **デスクトップ アプリ**
   - 作成後、JSON をダウンロードしてリポジトリ直下に `client_secret.json` として保存
     （`.gitignore` 済み。コミット禁止）

### 2-2. トークン発行

```bash
pip install google-auth-oauthlib
python scripts/mint_google_oauth_token.py
```

ブラウザが開くのでアクセスを許可する。成功すると次の3つが**標準出力に平文表示**される：

```
GOOGLE_OAUTH_CLIENT_ID=xxxxxxxx.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=xxxxxxxx
GOOGLE_OAUTH_REFRESH_TOKEN=1//xxxxxxxx
```

Secrets に登録し終えたら、このターミナルのスクロールバック / シェル履歴を消すこと。
（`token.json` は既定では書き出さない。ローカルで再利用したいときだけ `--save-token`。）

---

## 3. Discord `# 週間まとめ` Webhook

`# 週間まとめ` チャンネル → 編集 → 連携サービス → ウェブフック → 新しいウェブフック →
URL をコピー。これを Secret `DISCORD_WEBHOOK_WEEKLY` に登録する。

（Webhook を作らず Bot 投稿にする場合は `DISCORD_WEBHOOK_WEEKLY` を空にして
`DISCORD_CHANNEL_ID_WEEKLY_SUMMARY` にチャンネル ID を入れる。Bot にそのチャンネルの
メッセージ送信権限が必要。）

`# モーニングジャーナル` のチャンネル ID は診断ツールで確認できる：

```bash
DISCORD_BOT_TOKEN=... python scripts/list_discord_channels.py
```

---

## 4. GitHub Secrets / Variables

**Settings > Secrets and variables > Actions**

### Secrets（必須）

| 名前 | 値 |
|---|---|
| `GEMINI_API_KEY` | （既存を流用）|
| `DISCORD_BOT_TOKEN` | （既存を流用）|
| `DISCORD_CHANNEL_ID_MORNING_JOURNAL` | `# モーニングジャーナル` のチャンネル ID |
| `DISCORD_WEBHOOK_WEEKLY` | `# 週間まとめ` の Webhook URL |
| `GOOGLE_OAUTH_CLIENT_ID` | 手順 2-2 の出力 |
| `GOOGLE_OAUTH_CLIENT_SECRET` | 手順 2-2 の出力 |
| `GOOGLE_OAUTH_REFRESH_TOKEN` | 手順 2-2 の出力 |

### Secrets（Sheets 追記に必須）

| 名前 | 値 |
|---|---|
| `WEEKLY_SPREADSHEET_ID` | 書き込み先スプレッドシートの ID。**初回に1度だけ** `python scripts/weekly_mindmap.py --create-spreadsheet` を実行すると空のスプレッドシートを作成して ID を表示するので、それを登録する。未設定でも PNG 生成と Discord 投稿は動くが、Sheets 追記はスキップされる（毎回新規作成する事故を防ぐため自動作成はしない）。 |

### Secrets（任意）

| 名前 | 既定動作 |
|---|---|
| `DISCORD_CHANNEL_ID_HEALTH` | `DISCORD_CHANNEL_ID_MORNING_JOURNAL` 未設定時のフォールバック |
| `DISCORD_CHANNEL_ID_WEEKLY_SUMMARY` | Webhook 不使用で Bot 投稿する場合のみ |

### Variables（任意）

| 名前 | 既定 | 用途 |
|---|---|---|
| `GEMINI_MODEL` | `gemini-3.6-flash` | 構造化モデル |
| `GOOGLE_CALENDAR_IDS` | `primary` | 読むカレンダー。カンマ区切りで複数可（例 `primary,xxxxx@group.calendar.google.com`）|

---

## 5. ローカル / モックでのテスト

```bash
# 依存
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-weekly.txt

# (A) 通信ゼロ。合成データで PNG と行 CSV を生成（レイアウト確認用）
python scripts/weekly_mindmap.py --use-mock
#   → reports/weekly/<週>_mindmap.png / <週>_rows.csv

# (B) 実データを収集するが Sheets 書き込み / Discord 投稿はしない（PNG は生成）
export GEMINI_API_KEY=... DISCORD_BOT_TOKEN=... DISCORD_CHANNEL_ID_MORNING_JOURNAL=...
export GOOGLE_OAUTH_CLIENT_ID=... GOOGLE_OAUTH_CLIENT_SECRET=... GOOGLE_OAUTH_REFRESH_TOKEN=...
python scripts/weekly_mindmap.py --dry-run

# (C) 対象週の基準日を指定（バックフィル・検証）
python scripts/weekly_mindmap.py --week 2026-09-07 --dry-run

# (D) 初回だけ: 書き込み先スプレッドシートを作成して ID を得る
python scripts/weekly_mindmap.py --create-spreadsheet
#   → 表示された WEEKLY_SPREADSHEET_ID を Secret / .env に登録

# (E) 本番と同じ全処理（Sheets 追記 + Discord 投稿まで実行）
python scripts/weekly_mindmap.py
```

macOS でフォント警告が出る場合は Noto Sans CJK か IPAex ゴシックを入れると
文字化けが消える（CI は `fonts-noto-cjk` を自動インストール）。

### GitHub Actions での手動テスト

Actions タブ → 「週次棚卸しマインドマップ」→ Run workflow。
`dry_run` に `1` を入れると Sheets / Discord をスキップし、生成物は
アーティファクト `weekly-mindmap` からダウンロードできる。

---

## 6. 挙動メモ

- ジャーナル 0 件 / カレンダー空 / タスク空 でも落ちない。空なら「記録なし」ノードの
  雛形マップ + ヘッダのみのシート + 短い通知になる。
- **OAuth トークン失効を毎回検知**する。実行開始時にトークン更新を1回試し、失敗したら
  `[ERROR]` ログ + Discord 本文に `⚠️ Google 認証エラー…` を必ず出す（データがあるのに
  黙って空マップを投稿し続けるのを防ぐ）。CI ステップ自体は緑のまま終わる。
- Gemini 失敗時は収集データから決定論的にツリーを組む（通知に「簡易構造化」と明記）。
- 週次タブが既にあれば中身を作り直す（冪等。同じ週を何度再実行しても行は重複しない）。
- Discord 送信は画像あり=multipart / 画像なし=生 JSON で自動切替。Discord GET は 429/5xx を
  指数バックオフで再試行。Google API は各呼び出しで `num_retries=5`。
- 画像 PNG はリポジトリにコミットしない（アーティファクトのみ）。行 CSV だけコミットする。
- `--week` の日付形式が不正でもジョブは落とさず当日基準にフォールバックする。
- OAuth スコープに `spreadsheets`（read/write）を含むため、リフレッシュトークンが漏れると
  当該 Gmail の全スプレッドシートが読み書き可能になる。Secrets の管理に注意。
- `DISCORD_*` の名前は `export_discord_logs.py` / 既存ワークフローの慣習に合わせている。
- `client_secret.json` / `token.json` / `.env` はコミットしない（`.gitignore` 済み）。
