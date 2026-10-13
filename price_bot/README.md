# price_bot（共用モジュール）

価格チェック(旧 GitHub Actions「特売価格チェック」)は廃止済み。
このディレクトリに残っているのは、底値トラッカー `scripts/fetch_deals.py`(`.github/workflows/deals.yml`)が利用する共用コードのみ。

## 構成

```
price_bot/
├── requirements.txt       依存パッケージ(deals.yml が pip install で使用)
└── scripts/
    └── scraper.py         チラシ取得・Gemini解析・規格数値化の共用関数(fetch_deals.py から import)
```

## 依存関係

- `scripts/fetch_deals.py` → `price_bot/scripts/scraper.py`(`sys.path` で読み込み)
- `scraper.py` → `scripts/gemini_guard.py`(リポジトリ直下 `scripts/` の共用モジュール)
- 単独実行するエントリポイントはない。`scraper.py` の `main()` は廃止。
