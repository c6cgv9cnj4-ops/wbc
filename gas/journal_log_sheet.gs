/**
 * ジャーナルログ集約シート Web App
 *
 * 【役割】
 * GitHub Actions(scripts/sync_weekly_sheet.py, scripts/generate_monthly_mindmap.py)から
 * 呼び出される、メモ(#インプット)とモーニングジャーナル(#ヘルス・日報)の生ログを
 * Google スプレッドシートへ集約するための中継 Web App。
 *   - upsert: 週次同期(weekly-sheet-sync.yml)が1日ぶんのログを1行として書き込む/上書きする。
 *   - fetch : 月次マインドマップ生成(monthly-mindmap.yml)が当月分の全行を読み出す。
 * 価格トラッカー用の gas/create_price_tracker.gs / price_bot/gas/Code.gs とは無関係の、
 * 完全に独立した新規デプロイとして扱うこと(シークレットもスプレッドシートも別)。
 *
 * 【使い方】
 * 1. https://script.google.com で新規プロジェクトを作成し(または集約したい
 *    スプレッドシートを開いて「拡張機能」>「Apps Script」)、このファイルの内容を
 *    丸ごと貼り付けて保存する。
 * 2. Apps Scriptエディタ左側の「プロジェクトの設定」(歯車アイコン)>
 *    「スクリプト プロパティ」>「スクリプト プロパティを追加」で、
 *    プロパティ名: JOURNAL_GAS_SHARED_SECRET / 値: 自分で決めたランダムな文字列
 *    を1回だけ登録する(この値をGitHub Secretsの JOURNAL_GAS_SHARED_SECRET にも
 *    同じ値で登録する)。このファイルには実際のシークレット値を書き込まないこと
 *    (公開リポジトリで管理しているため)。
 * 3. 右上「デプロイ」>「新しいデプロイ」>種類「ウェブアプリ」を選択し、
 *      - 実行するユーザー: 自分
 *      - アクセスできるユーザー: 全員
 *    でデプロイする(GitHub Actionsからの匿名アクセスを secret パラメータで
 *    認証する設計のため)。発行されたウェブアプリURLを GitHub Secrets の
 *    JOURNAL_GAS_WEB_APP_URL に登録する。
 * 4. 初回の doPost/doGet 呼び出し時に、このスクリプトが紐づくスプレッドシート内に
 *    "ログ" という名前のシートを自動作成し、ヘッダー行を書き込む(手動初期化は不要)。
 *    コードを更新した場合は「デプロイを管理」>鉛筆アイコン>「新バージョン」で
 *    再デプロイすること(URLは変わらない)。
 *
 * 【シート構造(自動生成される "ログ" シート)】
 *   key | week | date | type | text | synced_at
 *   - key       : `${date}|${type}` (例: "2026-09-04|daily")。再送時の重複防止キー。
 *   - week      : ISO週ラベル (例: "2026-W36")
 *   - date      : "YYYY-MM-DD"
 *   - type      : "daily"(#インプット/メモ) または "health"(#ヘルス・日報/モーニングジャーナル)
 *   - text      : 当日の生ログ本文(複数行)
 *   - synced_at : 最終同期日時(ISO8601)
 *
 * 【API】
 *   POST/GET 共通。secret は毎回必須。
 *   - mode=upsert: 送信されたitems(配列)を key で照合し、存在すれば上書き、
 *     無ければ追記する。 { mode, secret, items:[{key,week,date,type,text}, ...] }
 *     → { ok:true, written:件数 }
 *   - mode=fetch : month("YYYY-MM")に前方一致する date の行を全件返す。
 *     { mode, secret, month }
 *     → { ok:true, items:[{key,week,date,type,text,synced_at}, ...] }
 *   POSTは JSON ボディ、GETはクエリパラメータ(itemsはJSON文字列化して渡す)の
 *   どちらでも同じロジックで処理する。
 */

const SHEET_NAME = "ログ";
const HEADERS = ["key", "week", "date", "type", "text", "synced_at"];

function doPost(e) {
  var params = JSON.parse(e.postData.contents || "{}");
  return jsonOutput(handleRequest(params));
}

function doGet(e) {
  return jsonOutput(handleRequest(e.parameter || {}));
}

function handleRequest(params) {
  try {
    var secretProp = PropertiesService.getScriptProperties().getProperty("JOURNAL_GAS_SHARED_SECRET");
    if (!secretProp || params.secret !== secretProp) {
      return { ok: false, error: "unauthorized" };
    }
    if (params.mode === "upsert") {
      return handleUpsert(params);
    }
    if (params.mode === "fetch") {
      return handleFetch(params);
    }
    return { ok: false, error: "unknown mode: " + params.mode };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

function getOrCreateSheet() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_NAME);
    sheet.appendRow(HEADERS);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function handleUpsert(params) {
  var items = params.items;
  if (typeof items === "string") {
    items = JSON.parse(items || "[]");
  }
  items = items || [];

  var sheet = getOrCreateSheet();
  var lastRow = sheet.getLastRow();
  var keyToRow = {};
  if (lastRow >= 2) {
    var keys = sheet.getRange(2, 1, lastRow - 1, 1).getValues();
    for (var i = 0; i < keys.length; i++) {
      keyToRow[keys[i][0]] = i + 2;
    }
  }

  var now = new Date().toISOString();
  var written = 0;
  items.forEach(function (item) {
    var row = [item.key, item.week, item.date, item.type, item.text, now];
    var existingRow = keyToRow[item.key];
    if (existingRow) {
      sheet.getRange(existingRow, 1, 1, HEADERS.length).setValues([row]);
    } else {
      sheet.appendRow(row);
      keyToRow[item.key] = sheet.getLastRow();
    }
    written++;
  });

  return { ok: true, written: written };
}

function handleFetch(params) {
  var month = params.month || "";
  var sheet = getOrCreateSheet();
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) {
    return { ok: true, items: [] };
  }
  var data = sheet.getRange(2, 1, lastRow - 1, HEADERS.length).getValues();
  var items = [];
  data.forEach(function (r) {
    var date = String(r[2]);
    if (!month || date.indexOf(month) === 0) {
      items.push({
        key: r[0],
        week: r[1],
        date: r[2],
        type: r[3],
        text: r[4],
        synced_at: r[5],
      });
    }
  });
  return { ok: true, items: items };
}

function jsonOutput(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(
    ContentService.MimeType.JSON
  );
}
