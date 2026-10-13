# 地方バドミントン大会 自動通知（廃止・保存記録）

- 廃止の決定: 2026-10-13。バドミントン大会情報の自動収集・自動通知は今後使用しない。
- 廃止した機能: `.github/workflows/local_badminton_tournaments.yml`（毎朝 JST 07:00 の schedule と手動実行）、`scripts/fetch_local_badminton_tournaments.py`（Gemini 版と Python 版の両方）。
- このフォルダは監査・経緯確認のための保存記録です。ワークフローやスクリプトからは参照されません。

## 保存しているファイル
| ファイル | 内容 |
|---|---|
| `local_badminton_seen.json` | 既送信記録（通知済みの大会キーと日付）。廃止後も重複通知の経緯確認に使う |
| `local_badminton_last_run.json` | 最後の実行サマリ（取得結果・除外理由・取得失敗） |
| `local_badminton_koho_cache.json` | 広報PDFの読取結果のキャッシュ（Gemini 版で作成。再利用しない） |

## 復元する場合
- 通知を再開するには、廃止した2ファイルを git 履歴から戻し、Gemini 版の利用を再承認する必要がある。
- この記録のファイルを `state/` に戻すと、古い既送信記録として扱われる点に注意。
