/**
 * 北本・桶川エリア 底値トラッカー(厳選4ジャンル) 自動構築スクリプト
 *
 * 【使い方】
 * 1. https://script.google.com/ で「新しいプロジェクト」を作成し、
 *    このファイルの内容を貼り付けて保存する。
 *    (スプレッドシートに紐付いていないスタンドアロンのApps Scriptでよい。
 *     buildPriceTracker() が新しいスプレッドシートを自分で作成する)
 * 2. ファイル冒頭の SHARED_SECRET を、自分で決めたランダムな文字列に書き換えて保存する
 *    (この値をGitHub Secretsの DEALS_GAS_SHARED_SECRET にも同じ値で登録する)。
 * 3. 関数選択プルダウンで buildPriceTracker を選び、▷実行ボタンを1回だけ押す。
 *    権限承認が求められたら承認する。
 * 4. 実行完了後、「実行数」ログ(または実行後に出るポップアップ)に表示される
 *    スプレッドシートのURLを控えておく。
 * 5. 右上「デプロイ」>「新しいデプロイ」>種類「ウェブアプリ」
 *    - 実行するユーザー: 自分 / アクセスできるユーザー: 全員
 *    デプロイ後のURLをGitHub Secretsの DEALS_GAS_WEB_APP_URL に登録する。
 *
 * 【重要な注意】
 * このスクリプトはClaudeの実行環境からは直接実行できません(Googleアカウントの
 * 認可が必要なため)。上記の手順1〜3を実際にご自身で一度実行していただく必要が
 * あります。実行後にURLを教えていただければ、以降の疎通確認はこちらで代行します。
 */

// GitHub Actions(fetch_deals.py)からのリクエストを認証するための合言葉。必ず書き換えること。
var SHARED_SECRET = 'CHANGE_ME_TO_A_RANDOM_STRING';

var SPREADSHEET_NAME = '北本・桶川 底値トラッカー';
var LOG_SHEET_NAME = '価格ログ蓄積';

// ジャンル別タブに掲載する厳選品目。
// scripts/fetch_deals.py 側の DEALS_ITEMS と内容(特にnameとgenre)を必ず一致させること。
var GENRE_ITEMS = {
  '調味料・油': [
    { maker: '各社', name: '生しょうゆ(プラ容器)', content: '1000ml基準', unit: '100ml' },
    { maker: 'かどや', name: '純正ごま油', content: '400g基準', unit: '100g' },
    { maker: '各社', name: 'ノンオイルドレッシング', content: '180ml基準', unit: '100ml' },
  ],
  '生鮮（肉・青果）': [
    { maker: '国産', name: '若鶏モモ肉', content: '100g基準', unit: '100g' },
    { maker: '国産', name: '豚バラ切落し', content: '100g基準', unit: '100g' },
    { maker: '-', name: 'キャベツ', content: '1玉基準', unit: '1玉' },
    { maker: '-', name: '玉ねぎ', content: '1kg基準', unit: '1kg' },
    { maker: '-', name: 'じゃがいも', content: '1kg基準', unit: '1kg' },
  ],
  '主食・米': [
    { maker: '各社', name: '白米 5kg', content: '5000g基準', unit: '100g' },
    { maker: '各社', name: '白米 10kg', content: '10000g基準', unit: '100g' },
  ],
  'ペーパー類': [
    { maker: '各社', name: 'トイレットペーパー(12ロール等)', content: 'ロール数×長さ基準', unit: '1m' },
    { maker: '各社', name: 'キッチンペーパー(4ロール等)', content: 'ロール数基準', unit: '1ロール' },
  ],
};

var GENRE_NAMES = Object.keys(GENRE_ITEMS);

var STORE_COLUMNS = ['ロヂャース北本店', 'マルサン桶川店', 'ヤオコー', 'ベルク', 'ウエルシア'];
// ジャンル別タブの列: A〜L
var HEADER_ROW = ['メーカー', '商品名', '内容量(数値)', '単位', 'Amazon基準単価']
  .concat(STORE_COLUMNS)
  .concat(['実店舗最安単価', '買い推奨順位']);
// A:メーカー B:商品名 C:内容量 D:単位 E:Amazon基準単価 F〜J:店舗別単価 K:最安単価 L:判定

var COL = {
  MAKER: 1, NAME: 2, CONTENT: 3, UNIT: 4, AMAZON: 5,
  STORE_START: 6, STORE_END: 10, // F〜J (ロヂャース〜ウエルシア)
  MIN_PRICE: 11, RANK: 12,
};

// =====================================================================
// 1. スプレッドシート構築(手動で1回だけ実行する)
// =====================================================================

function buildPriceTracker() {
  var ss = SpreadsheetApp.create(SPREADSHEET_NAME);

  // デフォルトの「シート1」を最初のジャンルタブとして使い、残りを追加する
  var defaultSheet = ss.getSheets()[0];
  defaultSheet.setName(GENRE_NAMES[0]);
  buildGenreSheet_(defaultSheet, GENRE_NAMES[0]);

  for (var i = 1; i < GENRE_NAMES.length; i++) {
    var genre = GENRE_NAMES[i];
    var sheet = ss.insertSheet(genre);
    buildGenreSheet_(sheet, genre);
  }

  var logSheet = ss.insertSheet(LOG_SHEET_NAME);
  buildLogSheet_(logSheet);

  var url = ss.getUrl();
  Logger.log('作成したスプレッドシートのURL: ' + url);
  // スタンドアロンスクリプトなのでUI(ブラウザのポップアップ)は使えないため、
  // 実行後は「実行数」ログでURLを確認する。
  return url;
}

function buildGenreSheet_(sheet, genre) {
  sheet.clear();
  sheet.getRange(1, 1, 1, HEADER_ROW.length).setValues([HEADER_ROW]);
  sheet.getRange(1, 1, 1, HEADER_ROW.length).setFontWeight('bold').setBackground('#1F4E78').setFontColor('#FFFFFF');
  sheet.setFrozenRows(1);

  var items = GENRE_ITEMS[genre];
  for (var i = 0; i < items.length; i++) {
    var row = i + 2;
    var item = items[i];
    sheet.getRange(row, COL.MAKER).setValue(item.maker);
    sheet.getRange(row, COL.NAME).setValue(item.name);
    sheet.getRange(row, COL.CONTENT).setValue(item.content);
    sheet.getRange(row, COL.UNIT).setValue(item.unit);
    // E列(Amazon基準単価)は自動取得していないため空欄のまま。
    // 実際の相場を確認したうえで手動で入力してください(誤った金額を自動で
    // 入れると誤判定の原因になるため、あえて空欄にしています)。

    var minPriceCell = sheet.getRange(row, COL.MIN_PRICE).getA1Notation();
    var storeRangeA1 = sheet.getRange(row, COL.STORE_START, 1, COL.STORE_END - COL.STORE_START + 1).getA1Notation();
    var storeHeaderA1 = sheet.getRange(1, COL.STORE_START, 1, COL.STORE_END - COL.STORE_START + 1).getA1Notation();
    var amazonCell = sheet.getRange(row, COL.AMAZON).getA1Notation();

    sheet.getRange(row, COL.MIN_PRICE).setFormula(
      '=IFERROR(MIN(' + storeRangeA1 + '), "")'
    );

    sheet.getRange(row, COL.RANK).setFormula(
      '=IF(COUNT(' + storeRangeA1 + ')=0, "データなし",' +
      'IF(AND(ISNUMBER(' + amazonCell + '), ' + amazonCell + '>0, ' + minPriceCell + '>=' + amazonCell + '), "🔴 Amazonが安値",' +
      '"🟢 1位:" & INDEX(' + storeHeaderA1 + ', MATCH(SMALL(' + storeRangeA1 + ',1), ' + storeRangeA1 + ', 0)) &' +
      'IF(COUNT(' + storeRangeA1 + ')>1, " (次点:" & INDEX(' + storeHeaderA1 + ', MATCH(SMALL(' + storeRangeA1 + ',2), ' + storeRangeA1 + ', 0)) & ")", "")))'
    );
  }

  sheet.autoResizeColumns(1, HEADER_ROW.length);
}

function buildLogSheet_(sheet) {
  var headers = ['日付', '店舗名', 'ジャンル', '商品名', '実売価格', '換算単価', '特売種別'];
  sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  sheet.getRange(1, 1, 1, headers.length).setFontWeight('bold').setBackground('#1F4E78').setFontColor('#FFFFFF');
  sheet.setFrozenRows(1);
  sheet.autoResizeColumns(1, headers.length);
}

// =====================================================================
// 2. Webアプリ経由の自動連携(doGet) — GitHub Actions(fetch_deals.py)からの
//    リクエストを受け、ジャンル別タブの該当セルを更新 + 価格ログに追記する。
//    price_bot/gas/Code.gs のdoGetがPOSTで405になった実績があるため、
//    同じくGET経由に統一している。
// =====================================================================

/**
 * クエリパラメータ:
 *   secret       : SHARED_SECRETと同じ値
 *   items        : JSON配列文字列。各要素は以下の形式:
 *     {
 *       "genre": "調味料・油",
 *       "product_name": "純正ごま油",       // GENRE_ITEMSのnameと完全一致させる
 *       "store": "ヤオコー",                // STORE_COLUMNSのいずれかと完全一致させる
 *       "unit_price": 135.0,                // ジャンルタブのunit(100g等)あたりの単価
 *       "raw_price": 398,                   // 実売価格(税込)
 *       "deal_type": "週末セール"           // 平日市/週末朝市/通常 等、自由記述
 *     }
 */
function doGet(e) {
  var params = (e && e.parameter) || {};
  if (!params.items) {
    return jsonResponse_({ ok: true, message: 'このエンドポイントは稼働中です。secret/itemsパラメータ付きでアクセスするとデータを登録します。' });
  }

  var response;
  try {
    if (SHARED_SECRET === 'CHANGE_ME_TO_A_RANDOM_STRING') {
      throw new Error('SHARED_SECRETがまだ初期値のままです。ファイル冒頭の値を書き換えてください。');
    }
    if (params.secret !== SHARED_SECRET) {
      return jsonResponse_({ ok: false, error: 'unauthorized' });
    }

    var items = safeJsonParse_(params.items) || [];
    var result = processDealItems_(items);
    response = { ok: true, updated: result.updated, logged: result.logged, errors: result.errors };
  } catch (err) {
    response = { ok: false, error: err.message };
  }
  return jsonResponse_(response);
}

function processDealItems_(items) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var updated = 0;
  var logged = 0;
  var errors = [];

  items.forEach(function (item) {
    try {
      var genre = item.genre;
      var productName = item.product_name;
      var storeName = item.store;
      var unitPrice = item.unit_price;

      var sheet = ss.getSheetByName(genre);
      if (!sheet) throw new Error('ジャンルタブが見つかりません: ' + genre);

      var storeColIndex = STORE_COLUMNS.indexOf(storeName);
      if (storeColIndex === -1) throw new Error('未対応の店舗名: ' + storeName);
      var storeCol = COL.STORE_START + storeColIndex;

      var lastRow = sheet.getLastRow();
      var names = sheet.getRange(2, COL.NAME, Math.max(lastRow - 1, 0), 1).getValues();
      var rowIndex = -1;
      for (var i = 0; i < names.length; i++) {
        if (names[i][0] === productName) { rowIndex = i + 2; break; }
      }
      if (rowIndex === -1) throw new Error('該当商品行が見つかりません: ' + productName);

      if (typeof unitPrice === 'number') {
        sheet.getRange(rowIndex, storeCol).setValue(unitPrice);
        updated++;
      }

      appendPriceLog_(ss, item);
      logged++;
    } catch (err) {
      errors.push(String(err.message || err));
    }
  });

  return { updated: updated, logged: logged, errors: errors };
}

function appendPriceLog_(ss, item) {
  var logSheet = ss.getSheetByName(LOG_SHEET_NAME);
  if (!logSheet) return;
  logSheet.appendRow([
    new Date(),
    item.store || '',
    item.genre || '',
    item.product_name || '',
    (typeof item.raw_price === 'number') ? item.raw_price : '',
    (typeof item.unit_price === 'number') ? item.unit_price : '',
    item.deal_type || '',
  ]);
}

function safeJsonParse_(text) {
  try {
    return JSON.parse(text);
  } catch (err) {
    return null;
  }
}

function jsonResponse_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
