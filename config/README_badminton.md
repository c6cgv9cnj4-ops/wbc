# バドミントン結果配信・世界ランキング：運用手順

## 0. 実行系統（確定）
- **Discord へ投稿するのは既存 `badminton_alerts.yml`（毎朝06:05 JST）の1本だけ**。
- Mac mini のローカルジョブ（launchd）は **試合結果JSONの生成と専用ブランチへの公開**を行い、Discord へは送らない（`DISCORD_DELIVERY_ENABLED = False` で封じ込め）。
- GitHub Actions の `バドミントン新規モジュールのテスト` はテスト専用（定時実行・Secrets 参照なし）。
- APIキーは **macOS キーチェーン**から読む。GitHub Secrets には登録しない。
- Discord は既定で無効（`discord_output_enabled: false`）。ローカル実行は Discord の認証情報を読まない。
- 実行時の生成物（状態・ランキング・台帳・一覧・ログ・venv）は `~/Library/Application Support/ricky-kogyo/badminton/` に置く（リポジトリには置かない）。

## 1. Free APIの上限（50回/日・10回/分）
- 上限は **UTC 0時にリセット**（JST 9時）。06:05 JST の取得は、その時点のUTC日の枠を使う。
- 朝の取得は通常1回（ページングがあれば+α）。再試行（5xx・タイムアウトのみ最大3回）も枠を使う。
- 台帳の照合（search）は **1回最大20件、UTC 1日合計30件まで**。残り20件は朝の取得用に残す。
- 429 を受けたら、その日の検索は停止。翌日、未検索分から再開する（検索済みは再検索しない）。

## 2. 日本人選手ID台帳（一括確認）
1. 候補（JBA代表選手ページから49名）を照合する。
   ```bash
   BADMINTON_LOCAL_DIR="$HOME/Library/Application Support/ricky-kogyo/badminton" python3 scripts/badminton_jp_ledger.py search
   # キーはキーチェーンから読む（コマンドラインや環境変数には書かない）
   ```
2. 一括確認用のCSVを書き出す（社長が1ファイルで確認する）。
   ```bash
   BADMINTON_LOCAL_DIR="$HOME/Library/Application Support/ricky-kogyo/badminton" python3 scripts/badminton_jp_ledger.py export-decisions
   ```
   CSV の `decision(JP/NON_JP/空欄)` に JP か NON_JP を書き、`confirmed_by`・`confirmed_at(YYYY-MM-DD)` を埋める。
   根拠（JBA選手ページURL・API候補ID・国コード）が揃わない行は確定されない。判断しない行は空欄のまま。
3. まとめて適用する（根拠が揃う行だけ確定。残りは理由付きで表示）。
   ```bash
   BADMINTON_LOCAL_DIR="$HOME/Library/Application Support/ricky-kogyo/badminton" python3 scripts/badminton_jp_ledger.py apply-decisions --file <記入済みCSV>
   ```
- 自動確定はしない。同名の人・国コードが JPN でない人は、確定できないか、人が特に注意して判断する。

## 3. 世界ランキング（badspi）
- 週1回（7日以上古い、または未取得のとき）取得する。キー不要。
- 年はページに無いため「11月3日付（年未確認）」で保存する（年を捏造しない）。Last-Modified は年の証明にしない。
- 上位15位＋ページ掲載の日本選手のみ（全順位ランキングではない）。
- 結果通知では、保存済みの完全版を参照するだけ。氏名の完全一致のみ順位を表示し、それ以外は「順位不明」。

## 4. Mac mini のセットアップ（社長が一度だけ行う。承認後）
```bash
# 1) キーを登録（値は端末で入力。チャットに貼らない）
security add-generic-password -s ricky-kogyo-badminton-api-free -a BADMINTON_API_KEY -w

# 2) 専用venvの作成・固定版の導入・plist展開・plutil検証（登録はしない）
scripts/setup_local_badminton_env.sh

# 3) 登録（05:30・05:40・05:50 JST に毎日実行。既存の GitHub 06:05 配信より前に公開を完了させる）
scripts/install_local_badminton_launchd.sh --load
```
- 既存の python3 や他プロジェクトの依存は変更しない（専用venvのみ）。依存は `requirements-badminton-local.txt` で固定。
- 停止は `launchctl bootout gui/$(id -u)/com.rickykogyo.badminton-results`。
- ログは `~/Library/Application Support/ricky-kogyo/badminton/logs/`。
- キーが無い場合は試合結果の取得を停止（終了コード78）。badspi は継続する。
- 二重起動は排他ロックで防止（終了コード75）。
- Mac mini は Asia/Tokyo の時刻帯。キーチェーンが解錠された状態で動く。スリープ中は起床後に実行される。

## 5. 結果通知（朝06:05 JST）
- `discord_output_enabled` は既定 `false`。true にするまで Discord へは送らない。
- 警告（取得件数はあるが正規化0件・未知の種目表記・開始日時不明）は、ログとヘッダーに出る。「対象試合なし」とは区別される。

## 6. 試合結果の受け渡し（ハンドオフ）

### 方式と選定理由
- **Mac → GitHub の直接 push（main）は採用しない**。main にブランチ保護がなく、Mac に書き込み権限付きの資格情報を置くと漏えい時に main を直接書き換えられるため。
- 採用する方式：Mac が JSON を専用ブランチ `badminton-results-data` へ **fast-forward push のみ**で公開し、既存ワークフローが `git fetch` で読む（`scripts/publish_badminton_handoff.sh`）。
- 公開に使う資格情報は **そのブランチへの書き込み制限を掛けたディプロイキー**を想定。権限の制限（ブランチ単位の保護）は GitHub の設定で行う必要があり、未設定の間は公開しない。

### 安全策
- JSON は `schema_version`・生成時刻（26時間で失効）・`api_day_utc`（取得窓の一致）・`status`・`format_verified` を検証。1つでも満たさなければ新規結果は0件。
- 試合は1件ずつ検証（勝者と得点の整合、終了済み `finished` のみ、タイムゾーン必須、取得窓内、得点の範囲）。不正な試合は除外して理由を記録。
- 指定選手（既存の大会記事が担当）を含む試合は除外し、二重投稿を防ぐ。
- 既送信記録は既存の `state/badminton_alerts_seen.json` の新しいキー空間（`intl::<試合ID>`）に保存。内容が変わった場合は「（訂正）」として1回だけ送る。
- 既存配信への追加は **朝06:05の実行（morning 帯）のみ**。Mac の公開は 05:30 に始まり、遅れても 06:05 の取得より前に完了させる（再試行は 05:40・05:50）。送信成功後にだけ既送信として記録（失敗時は次回再試行）。
- 取り込みの例外・JSONの欠落・破損・期限切れ・取得失敗は、既存配信を止めない（既存の送信内容は従来どおり）。

### 応答形式の未検証
- `config/badminton_results.json` の `api_format_verified` が `false` の間、ローカルジョブは試合を1件も書き出さない（推測で補完しない）。
- 実APIの応答（start の形式、side のキー名、winner の型、games の型、status の意味、ページング）を実データで確認した後に、この値を `true` にする変更を PR で行う。

### 未解決（本番前に必要）
- 国内大会（JBA 結果ページで配信される試合）が API に含まれる場合、既存の JBA 結果配信と重複し得る。実データで含有を確認し、必要なら除外規則を追加する。

### 公開経路（ローカル実行から自動接続）
- 入口：`scripts/local_badminton_run.py`（launchd と手動実行の共通）。API取得に成功し、この実行で生成されたJSONだけを公開対象にする。
- 判定（`badminton_handoff.publish_decision`）：`data_publish_enabled` が true / 応答形式が検証済み / status が ok / 生成から15分以内 / キーがある / 前回と内容が違う。満たさなければ公開しない（理由をログに出す）。
- 公開：`scripts/publish_badminton_handoff.sh` が専用ブランチへ fast-forward push。失敗は `logs/publish.log` に残し、終了コードを1にする（Discord送信には影響しない）。
- ログ：鍵の場所は `<DEPLOY_KEY>` に置換して記録。鍵の中身は表示しない。
- **既定は無効**（`data_publish_enabled: false`）。

### 権限の証明（未達のため、公開は有効化しない）
- 現状：リポジトリのルールセット 0件、Deploy Key 0件、main のブランチ保護なし（GitHub API で確認）。
- Deploy Key に書き込み権限を与えると、リポジトリ全体への書き込みになり得る。専用ブランチへの限定は **ルールセットで main を含む他ブランチへの push を拒否する設定**が成立して初めて証明できる。
- 証明できるまで Deploy Key 方式は採用しない。ルールセットの作成・Deploy Key の登録は社長の操作（リポジトリ設定の変更）。
- 証明の手順（社長の設定後）：①専用ブランチへの push が成功する、②main への push が拒否される、③削除・force push が拒否される、の3点を実際に試して確認する。

### 国内大会の重複対策（安全側）
- 大会名に国内の語（全日本・日本・県・市・JBA など）を含む試合は除外。国際大会と確認できる語（BWF・Open・World など）を含まない試合も除外。
- 指定選手（既存の大会記事が担当）を含む試合も除外。
- 実APIの大会名の実データで、この規則の妥当性を確認する必要がある。

## 7. 実行時刻の設計（衝突の解消）

| 実行 | 時刻（JST） | 役割 |
|---|---|---|
| Mac launchd（初回） | 05:30 | API取得 → JSON生成 → 専用ブランチへ公開 |
| Mac launchd（再試行） | 05:40・05:50 | 公開が未完了なら公開だけ再試行（当日取得済みならAPIは呼ばない） |
| GitHub Actions（既存） | 06:05（cron `5 21 * * *` UTC、変更なし） | 専用ブランチを取得し、既存配信へ追加 |

- 1回の実行の上限は15分（設計上の上限）。最後の実行（05:50）の上限が 06:05 までに収まる。
- Mac の実行が遅れたり失敗したりしても、GitHub 側は「データなし」として既存配信を従来どおり送る。
- GitHub の cron は遅延し得るため、取得時刻は保証しない（遅延しても既存配信は止まらない）。

## 8. 公開の鮮度・再試行・既送信の扱い

- 公開対象：この実行が生成したJSON、または未公開の最新JSON。生成から2時間以内、status が ok、応答形式検証済み、キーあり、前回公開と内容が違う。
- 公開成功時だけ `publish_last.json`（内容ハッシュ）を更新する。失敗時は更新しないため、次回実行で再試行される。
- 古いJSON・取得失敗JSON・未検証JSON・破損JSONは公開しない。
- 公開スクリプトは、リモートへの接続（ls-remote）に失敗した場合は「ブランチ未作成」と見なさず、中止して終了コード2を返す。
- 既送信記録（`intl::`）は、GitHub 側が **送信成功後にだけ**更新する。
