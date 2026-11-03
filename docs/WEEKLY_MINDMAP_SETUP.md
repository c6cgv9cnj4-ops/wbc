# 週次観測（モーニングジャーナル）— セットアップ & テスト手順

> 2026-09-26 再編。旧「週次棚卸しマインドマップ（感情4象限・3大アクション・GitHub Pages公開）」は廃止。
> 目的は評価・矯正ではなく、書き続けた記録から自分でも気づいていない変化を**観測**すること。

毎週日曜 20:00 JST に、Discord `# モーニングジャーナル` の直近8週分を取得し、

| 担当 | やること |
|---|---|
| Python（`scripts/journal_observe.py`） | 書いた日数・文字数、語が「書いた日のうち何日に出たか」の変化（🆕新しく出た／↩️再び出た／⬆️増えた／⬇️減った／💤出てこなかった／🔁続いている）、表現（〜たい・やった・面倒・不安 等）の1000字あたり出現率、同じ日に出た組み合わせ、根拠（日付・抜粋・スレッドURL）。同じ入力なら必ず同じ結果 |
| Gemini | 上の観測と根拠抜粋だけを受け取り、観測ID（O1…）を参照した**仮説**を複数の可能性として返す。指示・評価・断定の文は機械的に除去。失敗しても観測だけで完結 |

出力先（すべて非公開の場所のみ）:

1. Discord `# 週間まとめ` — 1通目: **A.書いた記録 + B.変化**（PNGダッシュボード添付・ピン留め）／2通目: **C.気づき（仮説）+ D.根拠**
2. スプレッドシート「**週次観測**」タブ — 1週1行（対象週で上書き・冪等）。数値列はそのままグラフ化できる
3. 月末週（その月最後の日曜）は「直近28日 vs その前28日」の同じ観測を 🌕 月次観測 として別投稿

| ファイル | 役割 |
|---|---|
| `scripts/weekly_mindmap.py` | 収集 → 観測 → 仮説 → PNG → Sheets → Discord（ファイル名・CLIは互換のため据え置き）|
| `scripts/journal_observe.py` | 観測・仮説・出力整形（通信は Gemini のみ）|
| `tests/test_journal_observe.py` | 単体テスト（`python -m unittest discover -s tests`）|
| `.github/workflows/weekly_mindmap.yml` | 定期実行（cron `0 11 * * 0`）。成果物のコミット・アーティファクト保存はしない |
| `reports/weekly/YYYY_Www_observe.png` | ローカル生成物（コミットしない）|

**公開リポジトリのため**: ジャーナル由来の PNG・語・抜粋はコミットも Actions アーティファクト保存もしない。
Actions のログ（公開）には件数だけを出し、本文プレビューはローカル実行時のみ表示する。
旧タブ「週次ログ」「週次レビュー」は履歴として残し、以後は更新しない。

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

# (A) 通信ゼロ。合成8週分で観測 → PNG と Discord 本文プレビュー
python scripts/weekly_mindmap.py --use-mock
#   → reports/weekly/<週>_observe.png
python -m unittest discover -s tests   # 観測ロジックの単体テスト

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

Actions タブ → 「週次観測（モーニングジャーナル）」→ Run workflow。
`dry_run` に `1` を入れると Sheets / Discord をスキップする。ログには件数
（書いた日数・観測件数・新規/増減の数）だけが出る（本文・語は出さない）。

---

## 6. 挙動メモ

- ジャーナル 0 件でも落ちない。短い通知と「書いた日数 0」の行を残す。
- 引数なし実行は「直近の日曜で終わる週」が対象（cron が遅れて月曜に起動しても前週を扱う）。
- 比較データが無い（初回・長い空白後）ときは、増減・不在を判定せず「新しく出た」だけを出す。
- 語の抽出は形態素解析を使わず文字種の連なりで行う（再現性優先）。「片付け」→「片付」の
  ように語尾が欠ける・ひらがな語は拾わない、という限界がある。
- **OAuth トークン失効を毎回検知**する。実行開始時にトークン更新を1回試し、失敗したら
  `[ERROR]` ログ + Discord 本文に `⚠️ Google 認証エラー…` を必ず出す（データがあるのに
  黙って空マップを投稿し続けるのを防ぐ）。CI ステップ自体は緑のまま終わる。
- Gemini 失敗（402 クレジット切れ等）時は C 欄に「今回は仮説なし」と出し、A・B・D はそのまま出す。
- 週次観測シートは対象週（A列）で既存行を検索し上書きする（冪等）。値は RAW で書く。
- Discord 送信は画像あり=multipart / 画像なし=生 JSON で自動切替。Discord GET は 429/5xx を
  指数バックオフで再試行。Google API は各呼び出しで `num_retries=5`。
- `--week` の日付形式が不正でもジョブは落とさず当日基準にフォールバックする。
- OAuth スコープに `spreadsheets`（read/write）を含むため、リフレッシュトークンが漏れると
  当該 Gmail の全スプレッドシートが読み書き可能になる。Secrets の管理に注意。
- `DISCORD_*` の名前は `export_discord_logs.py` / 既存ワークフローの慣習に合わせている。
- `client_secret.json` / `token.json` / `.env` はコミットしない（`.gitignore` 済み）。

---

## 週次レビュー（2026-09-20 追加 → 2026-09-26 週次実行から外した）

> 分類（思考/ToDo/感情/保留）と Friction & Action・Next Focus は「観測」の方針と合わないため、
> 週次パイプラインからは呼ばなくなった。`scripts/journal_review.py` は手動ワークフロー
> 「ジャーナル思考マップHTML生成(手動)」とスレッド取得（`collect_threads`）のために残している。

（以下は 2026-09-26 までの旧仕様。シートの既存行は履歴として残る）

旧仕様では毎週日曜の実行で、「週次ログ」タブに加え、シート **「週次レビュー」** に1週=1行を追加する（新しい週が常に2行目＝先頭。古い週は下に残り、スクロールで見返せる）。同じ週を再実行すると同じ行を上書きする。

| 列 | 内容 |
|---|---|
| A / B | 週ID(2026-W38) / 週のタイトル(2026年9月第3週 マインドマップ)・期間・実行日 |
| C | 【俯瞰】4カテゴリ放射状マップ(`=IMAGE`。Pages `mindmap/review/{週}.png`) |
| D | 【資産】💡 Ideas(各行に該当スレッドURL) |
| E | 【教訓・脱出】🛑 Friction & Action(ノイズ→対処の1行セット) |
| F | 【次週フォーカス】🎯 Next Focus(1つだけ・太字) |
| G〜M | 月〜日のDiscordスレッドURL(`https://discord.com/channels/<guild>/<thread>`) |

- 実装: `scripts/journal_review.py`（`weekly_mindmap.py` から呼ぶ。失敗しても既存処理は継続）。
- 1枚完結HTML(D3.js。ノードクリックで該当スレッドが開く): 週次実行時は `reports/weekly/{週}_journal_mindmap.html`（アーティファクトのみ）。任意期間は Actions「ジャーナル思考マップHTML生成(手動)」を実行（アーティファクト保持1日）。
- 必須Secrets: 既存に加え `WEEKLY_SPREADSHEET_ID`。OAuth同意画面は「本番(In production)」必須（テストのままだとトークンが7日で失効し、書き込みが止まる。2026-09-20 に実際に失効していた）。
- 注意: `mindmap/`・`reports/weekly/*.csv` は公開リポジトリ/Pagesに置かれるため、日記由来の短い要約が公開される（=IMAGEに公開URLが必要なため）。
