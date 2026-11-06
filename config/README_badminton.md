# バドミントン結果配信・世界ランキング：運用手順

## 1. Free APIの上限（50回/日・10回/分）の運用

- 上限は**UTC 0時にリセット**（JST 9時）。朝06:05 JSTの結果取得は、その時点のUTC日の枠を使う。
- 朝の取得は通常1回（ページングがあれば+α）。再試行（5xx・タイムアウトのみ最大3回）も枠を使う。
- **台帳の照合（search）は1回の実行で最大20件**。CLI の既定値も20で、20を超える指定は20に制限される。
- 検索で429（分または日次の上限）を受けたら、その日の検索は停止する。翌日、未検索の候補から再開する（検索済みは再検索しない）。
- 検索は**1日（UTC）合計30件まで**（`SEARCH_DAILY_BUDGET`）。残り20件は朝の結果取得・再試行・ページング用に必ず残す。
- 49候補は、1日目に20件・2日目に10件など、2日以上に分ける。翌日は未検索分から再開する。
- 人が確認する対象は `config/badminton_jp_confirm_queue.md`（JPN候補が出た人だけ、10名ずつの塊）。残りは `config/badminton_jp_candidates_review.md` を参照。

```bash
# 1回最大20件。途中まで検索した結果はファイルに保存され、次回は未検索分から再開する
BADMINTON_API_KEY=（キー） python3 scripts/badminton_jp_ledger.py search
```

## 2. 日本人選手ID台帳（config/badminton_jp_players.json）

- 候補: `config/badminton_jp_candidates.json`（JBA代表選手ページから49名。自動確定はしていない）。
- 確認一覧: `config/badminton_jp_candidates_review.md`（同名・照合結果を含む）。
- 確定は人が `confirm` で行う。以下がすべて揃う場合のみ台帳に入る。
  - 選手IDが、その候補の検索結果に含まれている（自由入力は不可）
  - 国コードが JPN（日本人確定）、または JPN 以外（非日本人確定）
  - 出典URL（JBA選手ページ）・確認者・確認日（YYYY-MM-DD）

```bash
python3 scripts/badminton_jp_ledger.py confirm \
  --detail-id <JBA詳細ID> --player-id <pl_ID> --decision JP \
  --source-url <JBA選手ページURL> --confirmed-by <確認者名> --confirmed-at <YYYY-MM-DD>
```

## 3. 状態ブランチ（badminton-results-state）

- 両workflowの `prepare` ジョブが、無ければ空の初期コミットだけのブランチを作る（既存は変更しない・force しない）。
- 手動で作る場合は `scripts/ensure_state_branch.sh` を実行してもよい（冪等）。
- 保存は対象JSONだけを add し、push が失敗したら取り込み直して最大3回再試行する。

## 4. 世界ランキング（badspi）

- 保存条件: ページの基準月が実行月と一致し、**かつ基準年を独立に確認できた場合のみ**保存する。
- 年はページに無い。根拠は (a) HTTP の Last-Modified の年月の一致、または (b) 人の確認（`--confirm-year-source` と `--confirm-year`）。
- どちらも無い場合は `[HOLD]`（保存しない）。現時点の確認では Last-Modified が返らないため、自動実行では保存されない。
- 基準月が一致しない場合は `[WAIT]`。前月データは推測で取得しない。
- 上位15位＋ページ掲載の日本選手のみ（全件ランキングではない）。

## 5. 結果通知（朝06:05 JST）

- `discord_output_enabled` は既定 `false`。true にするまで Discord へは送らない。
- 警告（取得件数はあるが正規化0件・未知の種目表記・開始日時不明）は、ログ（`::warning::`）とDiscordヘッダーに出る。「対象試合なし」とは区別される。

## 6. Mac mini でのローカル実行（launchd・採用方式）

- APIキーは GitHub Secrets に置かない。**macOS キーチェーン**から読む（コード・設定・ログ・GitHub には保存しない）。
- キーが無い場合は取得を試みず停止（終了コード 78）。badspi（キー不要）は継続する。
- 二重起動はロックで防止（終了コード 75）。当日分が取得済みなら再取得しない。
- badspi は週1回（7日以上古い、または未取得のとき）だけ取得する。
- 台帳の検索は1日30件まで（UTC）。確定は人が行う。Discord は送らない（既定 OFF。このランナーは Discord の認証情報を読まない）。
- 実行時の生成物（状態・ランキング・台帳・ログ）は `~/Library/Application Support/ricky-kogyo/badminton/` に置く（リポジトリには置かない）。

### 登録手順（社長の承認後・一度だけ）

```bash
# 1) キーを登録（値は端末で入力。チャットに貼らない）
security add-generic-password -s ricky-kogyo-badminton-api-free -a BADMINTON_API_KEY -w

# 2) 依存パッケージ（ローカルの python3 に）
python3 -m pip install requests

# 3) plist を展開して登録（06:05 JST に毎日実行）
scripts/install_local_badminton_launchd.sh --load
```

- 停止は `launchctl bootout gui/$(id -u)/com.rickykogyo.badminton-results`。
- ログは `~/Library/Application Support/ricky-kogyo/badminton/logs/` を見る。
- Mac mini の時刻帯は Asia/Tokyo であること。スリープ中は起床後に実行される。
