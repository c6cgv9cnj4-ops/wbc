# 自宅Macの「起動係」（GitHub Actions の cron 遅延対策・N3）

GitHub Actions の cron は、設定では10〜30分おきでも、実測では3〜4時間おきにしか起動されない。そこで自宅Macの launchd から5分ごとに `scripts/dispatch_workflows.py` を動かし、速報系のワークフローを `gh workflow run` で起動する。

- 処理（取得・state・Discord送信）はこれまでどおり Actions 側で行う。Mac 側に Webhook や state は置かない（gh のログイン情報だけを使う）。
- 既存の cron はそのまま残す。Mac が止まっても、今の頻度（数時間おき）に戻るだけで、配信は止まらない。

| ワークフロー | 最小間隔 | 起動しない条件 |
|---|---|---|
| jma_alerts.yml | 10分 | 実行中・待機中の run がある / 直近の run から10分以内 |
| news.yml | 30分 | 同上（30分） |
| nikkei_cnbc_digest.yml | 60分 | 同上（60分） |

## 二重配信を防ぐ仕組み

1. 起動係は、実行中・待機中の run があるワークフローは起動しない。
2. 3つのワークフローには、同時実行を防ぐ設定（concurrency）が元からある。
3. 3つのワークフローの checkout は `ref: main` にしている。そのため、先行 run を待っていた run も、実行を始める時点の最新 state を読む。

## 有効化の手順（**push とワークフローの変更は要許可**）

1. このブランチ（feat/n3-n4）を main に反映する。checkout の変更も含むが、この時点では動作は変わらない。
2. 専用clone を最新にし、送信なしで判定だけ確認する
   ```bash
   cd ~/Services/anzn-local/wbc && git pull --ff-only && .venv/bin/python scripts/dispatch_workflows.py --dry-run
   ```
   3行とも「起動」か「見送り」が出て、`[ERROR]` が無いこと。
3. launchd に登録する（登録と同時に1回実行される）
   ```bash
   cp docs/launchd/com.rickykogyo.actions-dispatcher.plist ~/Library/LaunchAgents/ && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.rickykogyo.actions-dispatcher.plist
   ```
4. 停止の検知を有効にする: `news.yml` の env に `MAC_DISPATCHER: "on"` を追加して push する。これ以降、news の実行間隔が60分を超えると、#webhook_news に異常通知が届く（12時間の抑止つき）。

## 確認

- `tail ~/Library/Logs/actions-dispatcher.log`: 5分ごとに3行ずつ出て、`[ERROR]` が無いこと
- `gh run list -R c6cgv9cnj4-ops/wbc -L 10`: `workflow_dispatch` の run が、ほぼ10〜30分間隔で並んでいること

## Mac が止まった場合

| 状況 | 挙動 | 検知 |
|---|---|---|
| スリープ中 | 起動係は動かない。復帰後に1回実行される | news が60分以上空くと異常通知 |
| 電源オフ・ログアウト | 起動係は動かない（gh のキーチェーンはログイン中しか使えない） | 同上 |
| gh の認証切れ | ログに `[ERROR] … gh run list 失敗` が出て、exit 1 | 同上（news の空きで検知） |
| いずれの場合も | 既存の cron で、数時間おきの実行は続く | ― |

復旧: Mac を起動・ログインするだけでよい（RunAtLoad で即時に実行される）。gh の認証切れのときは、`gh auth login` をやり直す。

## 停止・元に戻す

```bash
launchctl bootout gui/$(id -u)/com.rickykogyo.actions-dispatcher
```
あわせて `news.yml` の `MAC_DISPATCHER: "on"` を消す（残すと、60分空くたびに異常通知が届く）。checkout の `ref: main` は、そのまま残してよい。
