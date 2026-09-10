# -*- coding: utf-8 -*-
"""
Google OAuth リフレッシュトークン発行ヘルパ(ローカルで1回だけ実行)

weekly_mindmap.py が GitHub Actions で無人実行するために必要な
GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET / GOOGLE_OAUTH_REFRESH_TOKEN
の 3 つを取得する。個人 Gmail は Google Tasks / 個人カレンダーをサービス
アカウントから読めないため、本人アカウントの OAuth 同意で refresh token を作る。

------------------------------------------------------------------------------
事前準備(Google Cloud Console / 1回のみ)
------------------------------------------------------------------------------
  1. プロジェクトを用意(家計簿システムと同じ GCP プロジェクトで可)。
  2. 「API とサービス > 有効な API」で以下を有効化:
       - Google Calendar API
       - Google Tasks API
       - Google Sheets API
  3. 「OAuth 同意画面」: ユーザーの種類 = 外部。スコープは下記 3 つ
     (.../auth/calendar.readonly, tasks.readonly, spreadsheets)。
     ★ 公開ステータスを必ず「本番(In production)」にすること。★
       「テスト」のままだと発行されるリフレッシュトークンが 7 日で自動失効し、
       週1回実行の本ジョブは 2 回目以降必ず認証エラーになる。上記スコープは
       いずれも「制限付き」ではないため、本番公開に Google の審査は不要。
  4. 「認証情報 > 認証情報を作成 > OAuth クライアント ID」
       - アプリケーションの種類 = デスクトップ アプリ
       - 作成後、JSON をダウンロードして本ファイルと同じ場所に client_secret.json として保存。

------------------------------------------------------------------------------
実行
------------------------------------------------------------------------------
  pip install google-auth-oauthlib
  python scripts/mint_google_oauth_token.py                  # client_secret.json を自動検出
  python scripts/mint_google_oauth_token.py path/to/client_secret.json
  python scripts/mint_google_oauth_token.py --save-token     # token.json も書き出す(任意)

ブラウザが開くのでカレンダー/ToDo/スプレッドシートへのアクセスを許可する。
成功すると client_id / client_secret / refresh_token が **標準出力に平文表示** される。
GitHub リポジトリ Settings > Secrets and variables > Actions に同名で登録したら、
ターミナルのスクロールバック/履歴を消しておくこと。
(refresh token は再表示できない。無くしたら再実行して取り直す。)

client_secret.json / token.json は .gitignore 済み。絶対にコミットしない。
"""
import json
import os
import sys

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/tasks.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]


def _find_client_secret(argv: list[str]) -> str:
    for a in argv[1:]:
        if a and not a.startswith("-"):
            return a
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in ("client_secret.json",
                 os.path.join(here, "client_secret.json"),
                 os.path.join(os.getcwd(), "client_secret.json")):
        if os.path.exists(cand):
            return cand
    # client_secret_*.json も拾う
    for d in (os.getcwd(), here):
        for f in sorted(os.listdir(d)):
            if f.startswith("client_secret") and f.endswith(".json"):
                return os.path.join(d, f)
    return ""


def main() -> int:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("[ERROR] google-auth-oauthlib が必要です:  pip install google-auth-oauthlib")
        return 1

    path = _find_client_secret(sys.argv)
    if not path or not os.path.exists(path):
        print("[ERROR] client_secret.json が見つかりません。ファイルを配置するかパスを引数で渡してください。")
        return 1
    print(f"[INFO] OAuth クライアント: {path}")

    flow = InstalledAppFlow.from_client_secrets_file(path, scopes=SCOPES)
    try:
        creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] ブラウザ認証に失敗しました: {err}")
        print("        ブラウザが使える端末で実行してください（run_console は新しい "
              "google-auth-oauthlib では廃止されています）。")
        return 1

    if not creds.refresh_token:
        print("[ERROR] refresh_token が取得できませんでした。同意画面で prompt=consent を通してください。")
        return 1

    client_id = creds.client_id or ""
    client_secret = creds.client_secret or ""

    print("\n============================================================")
    print(" GitHub Secrets / .env に以下を登録してください")
    print("============================================================")
    print(f"GOOGLE_OAUTH_CLIENT_ID={client_id}")
    print(f"GOOGLE_OAUTH_CLIENT_SECRET={client_secret}")
    print(f"GOOGLE_OAUTH_REFRESH_TOKEN={creds.refresh_token}")
    print("============================================================")
    print("登録が済んだら、このターミナルのスクロールバック/履歴を消してください。")

    if "--save-token" in sys.argv:
        token_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token.json")
        with open(token_path, "w", encoding="utf-8") as fh:
            json.dump({
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": creds.refresh_token,
                "scopes": SCOPES,
            }, fh, ensure_ascii=False, indent=2)
        print(f"(--save-token 指定のため {token_path} にも保存しました。コミット禁止)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
