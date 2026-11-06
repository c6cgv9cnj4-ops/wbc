# バドミントン結果配信・世界ランキング：運用手順

## 1. Free APIの上限（50回/日・10回/分）の運用

- 上限は**UTC 0時にリセット**（JST 9時）。朝06:05 JSTの結果取得は、その時点のUTC日の枠を使う。
- 朝の取得は通常1回（ページングがあれば+α）。再試行（5xx・タイムアウトのみ最大3回）も枠を使う。
- **台帳の照合（search）は1回の実行で最大20件**。CLI の既定値も20で、20を超える指定は20に制限される。
- 検索で429（分または日次の上限）を受けたら、その日の検索は停止する。翌日、未検索の候補から再開する（検索済みは再検索しない）。
- 推奨: 49候補なら、**1日目に20件・2日目に20件・3日目に9件**に分ける。朝の結果取得と同じUTC日に検索を集中させない。

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

- 保存条件: ページの基準月（タイトルの「○月○日付」）が**実行時点のUTC月と一致**した場合のみ保存する。
- 年はページに無いため実行時の年を用いるが、基準月一致が条件なので、他年の古いページは保存されない。
- 一致しない週は `[WAIT]` として保存せず、次週に再確認する。前月データは推測で取得しない。
- 上位15位＋ページ掲載の日本選手のみ（全件ランキングではない）。

## 5. 結果通知（朝06:05 JST）

- `discord_output_enabled` は既定 `false`。true にするまで Discord へは送らない。
- 警告（取得件数はあるが正規化0件・未知の種目表記・開始日時不明）は、ログ（`::warning::`）とDiscordヘッダーに出る。「対象試合なし」とは区別される。
