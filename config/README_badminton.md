# バドミントン結果配信・世界ランキング：運用手順

## 0. 実行系統（確定）
- **収集・配信は Mac mini のローカル実行（launchd・毎朝06:05）に一本化**。GitHub Actions は定時実行しない（テスト専用）。
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

# 3) 登録（06:05 JST に毎日実行）
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
