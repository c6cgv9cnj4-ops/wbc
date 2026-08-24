/**
 * 北本・桶川エリア 特売価格トラッカー ＆ Google ToDo買い物リスト自動連携(GCP不使用版)
 *
 * シート構成:
 *   ダッシュボード（特売一覧・ToDo送信）
 *   商品マスタ / 店舗マスタ / 価格履歴（ログ）
 *
 * このファイルは2つの経路でデータを受け取る:
 *   1. 手動: ダッシュボードのA列チェックボックスをONにして
 *      メニュー「買い物連携」>「チェック項目をToDoへ送信」を実行 (sendToShoppingList)
 *   2. 自動: GitHub Actions上のscraper.py/push_to_sheets.pyが、
 *      このスクリプトを「ウェブアプリ」としてデプロイしたURLへHTTP POSTする (doPost)
 *      → ダッシュボード・価格履歴（ログ）へ書き込み、🟢判定は即座にGoogle ToDoへ自動登録する。
 *      GCPのサービスアカウント・JSON鍵は一切不要。
 *
 * 事前準備:
 *   1. 拡張機能 > Apps Script を開き、このファイルの内容を貼り付ける。
 *   2. 左側「サービス」の＋ボタンから「Tasks API」を追加する。
 *   3. プロジェクトの設定(歯車アイコン) > スクリプト プロパティ で
 *      キー "SHARED_SECRET" 、値に自分で決めたランダムな文字列を登録する
 *      (この値をGitHub Secretsの GAS_SHARED_SECRET にも同じ値で登録する)。
 *   4. 右上「デプロイ」>「新しいデプロイ」>種類「ウェブアプリ」
 *      - 実行するユーザー: 自分
 *      - アクセスできるユーザー: 全員
 *      デプロイ後に表示されるURLをGitHub Secretsの GAS_WEB_APP_URL に登録する。
 *   5. 保存後、一度 onOpen または sendToShoppingList を手動実行して権限を承認する。
 */

var SHEET_DASHBOARD = 'ダッシュボード';
var SHEET_LOG = '価格履歴（ログ）';
var TASKLIST_TITLE = '買い物';
var DASHBOARD_MAX_ROW = 200;

// ダッシュボードの列インデックス(1始まり)
var COL = {
  SEND: 1,          // A: ToDo送信(チェックボックス)
  PRODUCT: 2,       // B: 商品名
  STORE: 3,         // C: 店舗名
  PRICE: 4,         // D: 今回税込価格
  UNIT_PRICE: 5,    // E: 単位単価
  AMAZON_PRICE: 6,  // F: Amazon基準単価
  DIFF: 7,          // G: vs Amazon差額
  DEADLINE: 8,      // H: 特売期限
  SIGNAL: 9         // I: 判定シグナル
};

// =====================================================================
// メニュー・手動送信(従来通り)
// =====================================================================

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('買い物連携')
    .addItem('チェック項目をToDoへ送信', 'sendToShoppingList')
    .addToUi();
}

function getOrCreateShoppingTaskList_() {
  var taskLists = Tasks.Tasklists.list().items || [];
  for (var i = 0; i < taskLists.length; i++) {
    if (taskLists[i].title === TASKLIST_TITLE) return taskLists[i].id;
  }
  var created = Tasks.Tasklists.insert({ title: TASKLIST_TITLE });
  return created.id;
}

function sendToShoppingList() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var ui = SpreadsheetApp.getUi();
  var sheet = ss.getSheetByName(SHEET_DASHBOARD);

  if (!sheet) {
    ui.alert('「' + SHEET_DASHBOARD + '」シートが見つかりません。');
    return;
  }

  var lastRow = sheet.getLastRow();
  if (lastRow < 2) {
    ui.alert('送信対象のデータがありません。');
    return;
  }

  var numRows = lastRow - 1;
  var values = sheet.getRange(2, 1, numRows, 9).getValues();

  var taskListId;
  try {
    taskListId = getOrCreateShoppingTaskList_();
  } catch (err) {
    ui.alert(
      'Google Tasks APIへの接続に失敗しました。\n\n' +
      '拡張機能 > Apps Script > 左側「サービス」の＋から「Tasks API」を追加し、' +
      '一度実行して権限を承認してください。\n\nエラー詳細: ' + err.message
    );
    return;
  }

  var sentCount = 0;
  var failedRows = [];

  for (var i = 0; i < values.length; i++) {
    var row = values[i];
    var isChecked = row[COL.SEND - 1] === true;
    if (!isChecked) continue;

    var sheetRow = i + 2;
    var productName = row[COL.PRODUCT - 1];
    var storeName = row[COL.STORE - 1];

    if (!productName || !storeName) {
      failedRows.push(sheetRow);
      continue;
    }

    try {
      insertShoppingTask_(taskListId, storeName, productName, row[COL.PRICE - 1],
        row[COL.UNIT_PRICE - 1], row[COL.DIFF - 1], row[COL.DEADLINE - 1], false);
      sheet.getRange(sheetRow, COL.SEND).setValue(false);
      appendToLog_(ss, row);
      sentCount++;
    } catch (err) {
      failedRows.push(sheetRow);
    }
  }

  if (sentCount === 0 && failedRows.length === 0) {
    ss.toast('チェックが入っている行がありませんでした。', '買い物リスト連携', 5);
  } else if (failedRows.length > 0) {
    ui.alert(
      '送信完了(一部エラーあり)',
      sentCount + '件を送信しました。\n\n以下の行は商品名/店舗名が未入力、' +
      'または送信に失敗したためスキップしました: ' + failedRows.join(', '),
      ui.ButtonSet.OK
    );
  } else {
    ss.toast(sentCount + '件をGoogle ToDo「' + TASKLIST_TITLE + '」リストへ送信しました。', '送信完了', 5);
  }
}

/**
 * Google ToDoへ1件登録する共通処理。
 * deadline は Dateオブジェクト、またはnull/undefinedを受け付ける。
 * isAuto=true のときはタイトル先頭に🟢を付け、自動判定であることが分かるようにする。
 */
function insertShoppingTask_(taskListId, storeName, productName, price, unitPrice, diff, deadline, isAuto) {
  var prefix = isAuto ? '🟢' : '';
  var title = prefix + '【' + storeName + '】' + productName + ' (税込' + formatNumber_(price) + '円)';
  var diffText = (typeof diff === 'number')
    ? (diff <= 0 ? diff.toFixed(1) : '+' + diff.toFixed(1))
    : '不明';
  var notes = '単価: ' + formatNumber_(unitPrice) + '円/単位 (vs Amazon: ' + diffText + '円)'
    + (isAuto ? ' ※自動判定通知' : '');

  var task = { title: title, notes: notes };
  if (Object.prototype.toString.call(deadline) === '[object Date]' && !isNaN(deadline)) {
    task.due = toIsoDueDate_(deadline);
  }
  Tasks.Tasks.insert(task, taskListId);
}

function appendToLog_(ss, row) {
  var logSheet = ss.getSheetByName(SHEET_LOG);
  if (!logSheet) return;
  logSheet.appendRow([
    new Date(),
    row[COL.PRODUCT - 1],
    row[COL.STORE - 1],
    row[COL.PRICE - 1],
    row[COL.UNIT_PRICE - 1],
    row[COL.AMAZON_PRICE - 1],
    row[COL.DIFF - 1],
    row[COL.SIGNAL - 1],
    row[COL.DEADLINE - 1]
  ]);
}

function toIsoDueDate_(date) {
  var y = date.getFullYear();
  var m = ('0' + (date.getMonth() + 1)).slice(-2);
  var d = ('0' + date.getDate()).slice(-2);
  return y + '-' + m + '-' + d + 'T00:00:00.000Z';
}

function formatNumber_(n) {
  if (typeof n !== 'number') return String(n);
  return (Math.round(n * 10) / 10).toLocaleString('ja-JP');
}

// =====================================================================
// Webアプリ経由の自動連携(doPost) — GitHub Actions からのPOSTを受ける
// =====================================================================

/**
 * POSTで受け取るJSON形式:
 * {
 *   "secret": "スクリプトプロパティSHARED_SECRETと同じ値",
 *   "items": [
 *     {
 *       "product_name": "キッコーマン 濃いだし本つゆ (1L基準)",
 *       "store": "業務スーパー北本店",
 *       "price_yen": 348,
 *       "unit_price": 34.8,
 *       "amazon_base_price": 39.8,
 *       "diff": -5.0,
 *       "deadline": "2026-08-28",   // ISO日付文字列 or null
 *       "signal": "🟢 店舗買い推奨（底値圏）"
 *     },
 *     ...
 *   ]
 * }
 */
function doPost(e) {
  var response;
  try {
    if (!e || !e.postData || !e.postData.contents) {
      throw new Error('リクエストボディがありません');
    }
    var payload = JSON.parse(e.postData.contents);

    var expected = PropertiesService.getScriptProperties().getProperty('SHARED_SECRET');
    if (!expected) {
      throw new Error('SHARED_SECRETがスクリプトプロパティに設定されていません');
    }
    if (payload.secret !== expected) {
      return jsonResponse_({ ok: false, error: 'unauthorized' });
    }

    var items = payload.items || [];
    var result = processIncomingItems_(items);
    response = { ok: true, written: result.written, notified: result.notified, errors: result.errors };
  } catch (err) {
    response = { ok: false, error: err.message };
  }
  return jsonResponse_(response);
}

/**
 * doGetは疎通確認用(ブラウザでURLを開いたときに簡単な応答を返す)。
 */
function doGet(e) {
  return jsonResponse_({ ok: true, message: 'このエンドポイントはPOST専用です。' });
}

function jsonResponse_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

/**
 * scraper.py側で既に単位単価・判定シグナルまで計算済みの行を受け取り、
 * ダッシュボード・価格履歴（ログ）へ書き込む。🟢判定は即座にGoogle ToDoへ自動登録する。
 */
function processIncomingItems_(items) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var dashboard = ss.getSheetByName(SHEET_DASHBOARD);
  var written = 0;
  var notified = 0;
  var errors = [];
  var taskListId = null;

  items.forEach(function (item) {
    try {
      var deadlineDate = item.deadline ? new Date(item.deadline + 'T00:00:00') : '';
      var rowValues = [
        false,
        item.product_name || '',
        item.store || '',
        (typeof item.price_yen === 'number') ? item.price_yen : '',
        (typeof item.unit_price === 'number') ? item.unit_price : '',
        (typeof item.amazon_base_price === 'number') ? item.amazon_base_price : '',
        (typeof item.diff === 'number') ? item.diff : '',
        deadlineDate,
        item.signal || ''
      ];

      if (dashboard) {
        var rowNumber = findFirstBlankDashboardRow_(dashboard);
        if (rowNumber) {
          dashboard.getRange(rowNumber, 1, 1, 9).setValues([rowValues]);
        }
      }
      appendToLog_(ss, rowValues);
      written++;

      var isGreen = typeof item.signal === 'string' && item.signal.indexOf('🟢') === 0;
      if (isGreen && item.product_name && item.store) {
        if (!taskListId) taskListId = getOrCreateShoppingTaskList_();
        insertShoppingTask_(
          taskListId, item.store, item.product_name, item.price_yen,
          item.unit_price, item.diff,
          (deadlineDate instanceof Date) ? deadlineDate : null,
          true
        );
        notified++;
      }
    } catch (err) {
      errors.push(String(err.message || err));
    }
  });

  if (notified > 0) {
    ss.toast(notified + '件の🟢底値情報をGoogle ToDoへ自動通知しました。', '特売自動通知', 5);
  }

  return { written: written, notified: notified, errors: errors };
}

/**
 * ダッシュボードB列(商品名)が空欄になっている最初の行番号(2始まり)を返す。
 * 空き行が無い場合はnull(その場合はappendRow相当で末尾に追加する)。
 */
function findFirstBlankDashboardRow_(sheet) {
  var lastRow = Math.max(sheet.getLastRow(), 1);
  var checkRows = Math.min(DASHBOARD_MAX_ROW, Math.max(lastRow, DASHBOARD_MAX_ROW)) - 1;
  var values = sheet.getRange(2, COL.PRODUCT, checkRows, 1).getValues();
  for (var i = 0; i < values.length; i++) {
    if (!values[i][0]) return i + 2;
  }
  return null;
}

// =====================================================================
// 初期セットアップ補助
// =====================================================================

/**
 * SHARED_SECRETをスクリプトプロパティに設定するための補助関数。
 * コード中の 'ここに自分で決めたランダムな文字列を入れる' を実際の値に書き換えてから
 * 1回だけ手動実行し、実行後はこの関数を削除するか値を戻しておくことを推奨する。
 */
function setupSharedSecret_ONCE() {
  var secret = 'ここに自分で決めたランダムな文字列を入れる';
  if (secret === 'ここに自分で決めたランダムな文字列を入れる') {
    throw new Error('先にsecretの値を書き換えてから実行してください。');
  }
  PropertiesService.getScriptProperties().setProperty('SHARED_SECRET', secret);
  SpreadsheetApp.getUi().alert('SHARED_SECRETを設定しました。GitHub Secretsの GAS_SHARED_SECRET にも同じ値を登録してください。');
}
