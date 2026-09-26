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

## A. 準備（Actions側はまだ切り替えない）

1. 専用cloneを作る
   ```bash
   mkdir -p ~/Services/anzn-local && git clone https://github.com/c6cgv9cnj4-ops/wbc.git ~/Services/anzn-local/wbc
   ```
   `scripts/anzn_local.py` が main に入る前なら、`git -C ~/Services/anzn-local/wbc checkout feat/anzn-local`
2. 最小限の依存関係だけを入れる
   ```bash
   cd ~/Services/anzn-local/wbc && python3 -m venv .venv && .venv/bin/pip install feedparser requests beautifulsoup4
   ```
3. `.env` を作る（**オーナーが自分でエディタで記入**。Webhook の値はチャット・ログ・commit に出さない）
   ```bash
   touch ~/Services/anzn-local/wbc/.env && chmod 600 ~/Services/anzn-local/wbc/.env && open -e ~/Services/anzn-local/wbc/.env
   ```
   中身は `DISCORD_WEBHOOK_LOCAL=<#webhook_localのWebhook URL>` の1行
4. 送信なしで確認する（Discordへは送らず、stateも保存しない）
   ```bash
   ~/Services/anzn-local/wbc/.venv/bin/python ~/Services/anzn-local/wbc/scripts/anzn_local.py --dry-run
   ```
   「取得成功 / 新着N件」と出ればOK。

## B. 切り替え（この順番で行う）

1. `news.yml` に `ANZN_SOURCE: "mac"` が入った commit を main へ push する（**要許可**）。
   これで Actions 側は、あんぜんねっとの取得をやめる。
2. 専用cloneを最新にし、Actions側の既送信キーを引き継ぐ（送信はしない。既存のキーは上書きしない）
   ```bash
   cd ~/Services/anzn-local/wbc && git checkout main && git pull --ff-only && .venv/bin/python scripts/anzn_local.py --import-state state/news_seen.json
   ```
3. launchd に登録する
   ```bash
   cp docs/launchd/com.rickykogyo.anzn-local.plist ~/Library/LaunchAgents/ && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.rickykogyo.anzn-local.plist
   ```
4. 確認: `tail -n 20 ~/Library/Logs/anzn-local.log`
   - 「取得成功 / 新着N件」が出ていること
   - `#webhook_local` に Embed が届いていること
   - 初回は、未配信の直近分（最大10件）が1通でまとめて届く

B-1 から B-3 の間は、あんぜんねっとの配信が止まる（数分程度）。二重配信は起きない。

## 運用

- 更新: 専用cloneへの反映は、内容を確認してから手動で `git -C ~/Services/anzn-local/wbc pull --ff-only` を実行する（自動では更新しない）。
- 停止:
  ```bash
  launchctl bootout gui/$(id -u)/com.rickykogyo.anzn-local
  ```
- Actions 側に戻す: `news.yml` の `ANZN_SOURCE: "mac"` の行を消して push する。
- スリープ・電源オフについて
  - スリープ中の分は、復帰後に1回だけ実行される
  - 電源オフの場合は、ログイン時に実行される
  - ページには直近10件（約9日分）が残るので、短期間の停止なら追いつける
- 未対応: Mac 側の停止を検知する仕組み（heartbeat）はまだ無い（PROJECT_STATUS の後続タスク）。
