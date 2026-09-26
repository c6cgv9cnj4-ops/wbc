# あんぜんねっと 自宅Mac配信のセットアップ手順

GitHub Actions のIPからは、あんぜんねっとが約9割403になる（自宅Macからは200）。そのため、あんぜんねっとの新着Embedだけを、自宅Macの launchd から15分ごとに `#webhook_local` へ送る。

| 項目 | 場所 |
|---|---|
| 専用clone（launchd はここだけを実行する） | `/Users/hosokawachikara/Services/anzn-local/wbc` |
| Webhook（秘密情報） | 専用clone直下の `.env`（gitignore済み・権限600） |
| Mac専用state | `~/Library/Application Support/anzn-local/anzn_seen.json` |
| ログ | `~/Library/Logs/anzn-local.log` |
| launchd | `~/Library/LaunchAgents/com.rickykogyo.anzn-local.plist`（テンプレートは `docs/launchd/`） |

- Claude などが編集する作業ツリー（`~/Desktop/my-project`）は、launchd から実行しない。
- clone の置き場所は `~/Desktop` や `~/Documents` の外にする（macOS のプライバシー保護で、launchd からのアクセスが拒否されうるため）。
- state は Actions の `state/news_seen.json` とは共有しない。

## A. 準備（Actions側はまだ切り替えない。2026-09-26夜に1〜3は実施済み）

1. 専用cloneを作る
   ```bash
   mkdir -p ~/Services/anzn-local && git clone https://github.com/c6cgv9cnj4-ops/wbc.git ~/Services/anzn-local/wbc
   ```
   `feat/anzn-local` が未pushのうちは、`git -C ~/Services/anzn-local/wbc fetch ~/Desktop/my-project feat/anzn-local:feat/anzn-local` で取り込んで checkout する。
2. 最小限の依存関係だけを入れる（既存venvと同じバージョン）
   ```bash
   cd ~/Services/anzn-local/wbc && python3 -m venv .venv && .venv/bin/pip install beautifulsoup4==4.15.0 feedparser==6.0.14 requests==2.34.2
   ```
3. `.env`（権限600・gitignore対象）は作成済み。中身はコメントアウトされた空の行だけ。
4. **【オーナー】** `open -e ~/Services/anzn-local/wbc/.env` で開き、`# DISCORD_WEBHOOK_LOCAL=` の `#` を外して、`=` の後に `#webhook_local` の Webhook URL を記入する。値はチャット・ログに出さない。
5. Webhook を確認する（**送信はしない**。Discord の Webhook情報をGETするだけで、表示するのはHTTPステータスのみ）
   ```bash
   cd ~/Services/anzn-local/wbc && .venv/bin/python -c "import sys;sys.path.insert(0,'scripts');import anzn_local,requests;u=anzn_local.load_webhook(anzn_local.DEFAULT_ENV_PATH);print('Webhook: 未記入' if not u else f'Webhook確認 HTTP {requests.get(u,timeout=10).status_code}')"
   ```
   `HTTP 200` ならOK。`未記入` や 401/404 の場合は、B に進まない。
6. 送信なしで確認する: `.venv/bin/python scripts/anzn_local.py --dry-run` を実行し、「取得成功」と出ればOK。
   この時点では Mac側stateが空なので「新着10件」と出るのが正常。

## B. 切り替え（B-1〜B-6 を続けて実行する。目安は10分以内）

順番の理由:
- Actions を先に止めてから state を引き継ぐ。こうすれば、Actions が最後に送った分まで Mac側stateに入る。
- launchd の登録（RunAtLoad で即実行される）は、必ず引き継ぎの後に行う。先に登録すると、配信済みの10件が再送される。

1. 実行中の news.yml が無いことを確認する（0 が出ればOK。1以上なら、終わるまで待つ）
   ```bash
   gh run list -R c6cgv9cnj4-ops/wbc --workflow=news.yml --json status -q '[.[]|select(.status!="completed")]|length'
   ```
2. **【要許可】** main へ反映する。共有の作業ツリー（my-project）ではなく、feat の worktree から行う
   ```bash
   cd ~/Desktop/my-project-anzn-feat && git fetch origin && git rebase origin/main && git push origin HEAD:main
   ```
   push した時点から、Actions はあんぜんねっとを取得しない。
3. 1 をもう一度実行し、0 であることを確認する。push の直前に起動した run が旧コードのまま動いている可能性があるため。1以上なら、終わるまで待つ。
4. 専用cloneを main に更新し、Actions側の既送信キーを引き継ぐ（送信はしない。既存のキーは上書きしない）
   ```bash
   cd ~/Services/anzn-local/wbc && git checkout main && git pull --ff-only && .venv/bin/python scripts/anzn_local.py --import-state state/news_seen.json && .venv/bin/python scripts/anzn_local.py --dry-run
   ```
   dry-run が「新着0件」ならOK。20:43以降に本当に新しい出動があった場合だけ、その件数が出る。**10件と出たら、引き継ぎ失敗なので B-5 に進まない。**
5. launchd に登録する（登録と同時に1回実行される）
   ```bash
   cp docs/launchd/com.rickykogyo.anzn-local.plist ~/Library/LaunchAgents/ && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.rickykogyo.anzn-local.plist
   ```
6. 確認（下の「確認チェック」）

あんぜんねっとの配信が止まるのは、B-2 から B-5 の間だけ（数分）。なお、Actions からの取得はもともと約9割が失敗していたため、実質的な影響はほぼない。

## 確認チェック

- **launchd**: 次のコマンドで `state = running` か、`runs` が1以上・`last exit code = 0` であること
  ```bash
  launchctl print gui/$(id -u)/com.rickykogyo.anzn-local | grep -E "state|runs|last exit code"
  ```
- **ログ**: `tail -n 5 ~/Library/Logs/anzn-local.log` で「取得成功 / 新着0件」（または本当の新着の件数）が出ていること。`[ERROR]` が無いこと
- **state**: `~/Library/Application Support/anzn-local/anzn_seen.json` に、あんぜんねっとのキーが11件以上あること
- **Discord**: 初回で10件の再送Embedが**届いていない**こと。本当の新着があれば、それだけが届く
- **Actions**:
  - 次の run（手動実行 `gh workflow run news.yml -R c6cgv9cnj4-ops/wbc --ref main` か定期実行）のログに「ANZN_SOURCE=mac のため」が出ていること
  - 「あんぜんねっとの取得に失敗」が出ていないこと
  - 地域ニュースの防災まとめ欄が「自宅Macから配信中」になっていること
- **翌日**: ログに15分おきの実行記録が続いていること（Macの停止検知 heartbeat はまだ無いため、目視で確認）

## ロールバック（Mac方式をやめて Actions方式へ戻す）

1. Mac側を止める
   ```bash
   launchctl bootout gui/$(id -u)/com.rickykogyo.anzn-local
   ```
2. **【要許可】** `news.yml` の `ANZN_SOURCE: "mac"` の行（とコメント2行）を消して、main へ push する。次の run から Actions が再び取得する（成功率は約1割に戻る）。
3. 注意: Mac側だけで配信した記事は Actions側stateに無い。そのため、Actions が次に取得に成功した回で、ページ上に残っている分が1回だけ再送されうる（最大10件・1通）。
4. 実行中に問題が起きた場合の判断基準
   - B-4 の dry-run が10件 → B-5 に進まず、原因を調べる（Actions は止まったまま。長引くなら 2 で戻す）
   - B-5 の後にログへ `[ERROR]` が出る → 1 で止めて原因を調べ、30分以内に直せなければ 2 で戻す

## 運用

- 更新: 専用cloneへの反映は、内容を確認してから手動で `git -C ~/Services/anzn-local/wbc pull --ff-only` を実行する（自動では更新しない）。
- 停止: 上のロールバック 1 と同じ。
- スリープ・電源オフについて
  - スリープ中の分は、復帰後に1回だけ実行される
  - 電源オフの場合は、ログイン時に実行される
  - ページには直近10件（約9日分）が残るので、短期間の停止なら追いつける
- 未対応: Mac 側の停止を検知する仕組み（heartbeat）はまだ無い（PROJECT_STATUS の後続タスク）。
