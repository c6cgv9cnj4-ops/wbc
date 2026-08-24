/**
 * 北本・桶川エリア 底値データベース＆価格トラッカー (Professional Dashboard Edition)
 *
 * 【使い方】
 * 1. 既存のApps Scriptプロジェクト(このファイルの旧バージョンを貼り付けたもの)を開き、
 *    このファイルの内容で丸ごと上書き保存する。
 * 2. ファイル冒頭の SHARED_SECRET を、自分で決めたランダムな文字列に書き換えて保存する
 *    (この値をGitHub Secretsの DEALS_GAS_SHARED_SECRET にも同じ値で登録する)。
 *    ※このファイルには実際のシークレット値を書き込まないこと(公開リポジトリで
 *      管理しているため、実際の値が漏えいする)。
 * 3. 関数選択プルダウンで buildPriceTracker を選び、▷実行ボタンを1回だけ押す。
 *    (このシートに紐づいた状態のApps Scriptとして実行すること。既存の
 *    「価格ログ蓄積」シートのデータはそのまま引き継がれ、削除されない。
 *    未使用になった旧ジャンル別タブは削除ではなく「_旧」を付けて非表示アーカイブされる)
 * 4. 右上「デプロイ」>「デプロイを管理」>既存のウェブアプリのデプロイの鉛筆アイコン>
 *    「バージョン」を「新バージョン」にして「デプロイ」(URLは変わらない)。
 *
 * 【重要な注意】
 * このスクリプトはClaudeの実行環境からは直接実行できません(Googleアカウントの
 * 認可が必要なため)。上記の手順を実際にご自身で一度実行していただく必要が
 * あります。実行後に結果を教えていただければ、以降の疎通確認はこちらで代行します。
 */

// GitHub Actions(fetch_deals.py)からのリクエストを認証するための合言葉。必ず書き換えること。
// 【重要】このリポジトリ(wbc)は公開リポジトリのため、実際の値をこのファイルに
// 書き込んでコミットしないこと。ここはプレースホルダーのままにし、実際の値は
// Apps Scriptエディタ上で直接書き換えて保存すること(GitHub Secretsの
// DEALS_GAS_SHARED_SECRET と同じ値にする)。
var SHARED_SECRET = 'CHANGE_ME_TO_A_RANDOM_STRING';

var SPREADSHEET_NAME = '北本・桶川 底値トラッカー';
var MASTER_SHEET_NAME = '📊 底値ダッシュボード';
var LOG_SHEET_NAME = '📋 価格ログ蓄積';
var LEGACY_LOG_SHEET_NAME = '価格ログ蓄積'; // 旧バージョンでのシート名(データ引き継ぎ用)

// 追跡対象店舗(計8店舗。ウエルシアは生鮮・米を除く「ペーパー類・洗剤・調味料・飲料」のみが対象。
// scripts/fetch_deals.py の DEALS_STORES の category_scope と対応させる)
var STORES = ['ロヂャース北本店', 'マルサン桶川店', '業務スーパー', 'ヤオコー', 'ベルク', 'とりせん', 'ヨークマート', 'ウエルシア'];

var THEME = {
  headerBg: '#1E293B',        // スレートネイビー
  headerFont: '#FFFFFF',
  zebraEven: '#F8FAFC',       // ゼブラ背景(偶数行)
  bestPriceFont: '#166534',
  fontFamily: 'Roboto, "Noto Sans JP", sans-serif',
};

// ダッシュボードシートの列(A〜N、店舗数に応じてG〜Mが伸縮する)
var COL = {
  GENRE: 1, MAKER: 2, NAME: 3, SPEC: 4, UNIT: 5, AMAZON: 6,
  STORE_START: 7, // G列から店舗数ぶん
};
COL.STORE_END = COL.STORE_START + STORES.length - 1; // 7店舗なら G〜M
COL.MIN_PRICE = COL.STORE_END + 1;   // 実店舗最安単価
COL.BEST_STORE = COL.STORE_END + 2;  // エリア最安店舗
COL.RANKING = COL.STORE_END + 3;     // 買い推奨・価格順位ランキング
var MASTER_HEADER_COUNT = COL.RANKING;

// 価格ログ蓄積タブの列
var LOG_COL = {
  DATE: 1, STORE: 2, GENRE: 3, NAME: 4, SPEC: 5, RAW_PRICE: 6, UNIT_PRICE: 7, DEAL_TYPE: 8, MEMO: 9,
};
var LOG_COLUMN_WIDTHS = [150, 120, 110, 220, 100, 110, 100, 110, 180];

// =====================================================================
// 1. スプレッドシート構築・書式再適用(手動で実行する)
// =====================================================================

function buildPriceTracker() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  if (!ss) {
    throw new Error(
      'アクティブなスプレッドシートが見つかりません。' +
      'このスクリプトは対象のスプレッドシートを開いた状態(拡張機能>Apps Script)で実行してください。'
    );
  }

  var masterSheet = ss.getSheetByName(MASTER_SHEET_NAME) || ss.insertSheet(MASTER_SHEET_NAME, 0);
  buildMasterDashboardSheet_(masterSheet);

  // 価格ログは「新規作成」ではなく「既存シートを探してリネーム+書式再適用」にすることで、
  // 既に蓄積済みの実データを一切失わないようにする。
  var logSheet = ss.getSheetByName(LOG_SHEET_NAME) || ss.getSheetByName(LEGACY_LOG_SHEET_NAME);
  if (!logSheet) {
    logSheet = ss.insertSheet(LOG_SHEET_NAME, 1);
  } else if (logSheet.getName() !== LOG_SHEET_NAME) {
    logSheet.setName(LOG_SHEET_NAME);
  }
  ensureLogSheetHeader_(logSheet);
  applyLogSheetLayout_(logSheet);

  // 旧ジャンル別タブ等、新設計で使わなくなったシートは削除せず、
  // 「_旧」を付けて非表示アーカイブするだけにとどめる(データを失わないため)。
  ss.getSheets().forEach(function (sheet) {
    var name = sheet.getName();
    if (name !== MASTER_SHEET_NAME && name !== LOG_SHEET_NAME) {
      if (name.indexOf('_旧') === -1) {
        sheet.setName(name + '_旧');
      }
      sheet.hideSheet();
    }
  });

  Logger.log('スプレッドシート初期化完了: ' + ss.getUrl());
  return ss.getUrl();
}

function buildMasterDashboardSheet_(sheet) {
  sheet.setTabColor('#0284C7');

  var headers = ['ジャンル', 'メーカー', '商品名', '規格・容量', '単位', 'Amazon基準単価']
    .concat(STORES)
    .concat(['実店舗最安単価', 'エリア最安店舗', '買い推奨・価格順位ランキング']);

  sheet.getRange(1, 1, 1, headers.length).setValues([headers]);

  var headerRange = sheet.getRange(1, 1, 1, headers.length);
  headerRange.setBackground(THEME.headerBg)
    .setFontColor(THEME.headerFont)
    .setFontWeight('bold')
    .setFontSize(10)
    .setVerticalAlignment('middle')
    .setHorizontalAlignment('center');

  sheet.getRange(1, COL.AMAZON).setNote(
    '自動取得していません。誤った金額を自動で入れると誤判定の原因になるため、' +
    '実際の相場をご自身で確認したうえで手動入力してください(空欄なら実店舗内ランキングのみ表示されます)。'
  );

  sheet.setRowHeight(1, 40);
  sheet.setFrozenRows(1);
  sheet.setFrozenColumns(3); // 商品名まで固定

  var widths = [110, 110, 220, 100, 70, 110]; // ジャンル/メーカー/商品名/規格・容量/単位/Amazon基準単価
  for (var i = 0; i < widths.length; i++) {
    sheet.setColumnWidth(i + 1, widths[i]);
  }
  for (var s = COL.STORE_START; s <= COL.STORE_END; s++) {
    sheet.setColumnWidth(s, 105);
  }
  sheet.setColumnWidth(COL.MIN_PRICE, 110);
  sheet.setColumnWidth(COL.BEST_STORE, 130);
  sheet.setColumnWidth(COL.RANKING, 320);

  // 既存データ行があれば、価格列の書式(3桁カンマ+右寄せ)を再適用する
  var lastRow = sheet.getLastRow();
  if (lastRow >= 2) {
    var rows = lastRow - 1;
    sheet.getRange(2, COL.AMAZON, rows, 1).setNumberFormat('¥#,##0.0').setHorizontalAlignment('right');
    sheet.getRange(2, COL.STORE_START, rows, COL.STORE_END - COL.STORE_START + 1)
      .setNumberFormat('¥#,##0.0').setHorizontalAlignment('right');
    sheet.getRange(2, COL.MIN_PRICE, rows, 1).setNumberFormat('¥#,##0.0').setHorizontalAlignment('right');
  }
}

function ensureLogSheetHeader_(sheet) {
  var headers = ['更新日時', '店舗名', 'ジャンル', '商品名', '規格・容量', '実売価格(税込)', '換算単価', '特売区分', '備考'];
  sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  sheet.getRange(1, 1, 1, headers.length)
    .setBackground(THEME.headerBg)
    .setFontColor(THEME.headerFont)
    .setFontWeight('bold')
    .setFontSize(10)
    .setVerticalAlignment('middle')
    .setHorizontalAlignment('center');
  sheet.setRowHeight(1, 38);
  sheet.setFrozenRows(1);
  sheet.setTabColor('#64748B');
}

// 価格ログ蓄積タブの列幅・折り返し・日付/数値の表示形式をまとめて適用する。
// データ行(sheet.clear())には一切触れない(既存の蓄積データを保持するため)。
function applyLogSheetLayout_(sheet) {
  for (var i = 0; i < LOG_COLUMN_WIDTHS.length; i++) {
    sheet.setColumnWidth(i + 1, LOG_COLUMN_WIDTHS[i]);
  }
  sheet.getRange(1, 1, sheet.getMaxRows(), LOG_COLUMN_WIDTHS.length)
    .setWrapStrategy(SpreadsheetApp.WrapStrategy.CLIP);
  sheet.getRange(1, LOG_COL.NAME, sheet.getMaxRows(), 1).setWrapStrategy(SpreadsheetApp.WrapStrategy.WRAP);

  var dataRows = sheet.getMaxRows() - 1;
  if (dataRows > 0) {
    sheet.getRange(2, LOG_COL.DATE, dataRows, 1).setNumberFormat('yyyy/mm/dd hh:mm').setHorizontalAlignment('center');
    sheet.getRange(2, LOG_COL.RAW_PRICE, dataRows, 1).setNumberFormat('¥#,##0').setHorizontalAlignment('right');
    sheet.getRange(2, LOG_COL.UNIT_PRICE, dataRows, 1).setNumberFormat('¥#,##0.0').setHorizontalAlignment('right');
  }
}

/**
 * データを変更せず、書式(列幅・折り返し・数値フォーマット・ゼブラ)だけを
 * 再適用したい場合に使う(buildPriceTracker()のうち書式部分だけを再実行するショートカット)。
 */
function applyFormattingToExistingSheet() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var masterSheet = ss.getSheetByName(MASTER_SHEET_NAME);
  if (masterSheet) buildMasterDashboardSheet_(masterSheet);
  var logSheet = ss.getSheetByName(LOG_SHEET_NAME) || ss.getSheetByName(LEGACY_LOG_SHEET_NAME);
  if (logSheet) applyLogSheetLayout_(logSheet);
  Logger.log('フォーマットの再適用が完了しました。');
}

// =====================================================================
// 2. Webアプリ経由の自動連携(doGet) — GitHub Actions(fetch_deals.py)からの
//    リクエストを受け、ダッシュボードの該当商品行を動的に追加/更新 + 価格ログに追記する。
//    price_bot/gas/Code.gs のdoGetがPOSTで405になった実績があるため、
//    同じくGET経由に統一している。
// =====================================================================

/**
 * クエリパラメータ:
 *   secret       : SHARED_SECRETと同じ値
 *   items        : JSON配列文字列。各要素は以下の形式:
 *     {
 *       "genre": "調味料・油・日配",
 *       "maker": "かどや",                   // 不明な場合は省略可("-"になる)
 *       "product_name": "純正ごま油",         // 正規化後のコア品目名(完全一致で商品行を検索/作成)
 *       "spec": "400g",                      // 規格・容量の原文(任意)
 *       "store": "ヤオコー",                  // STORES のいずれかと完全一致させる
 *       "unit_price": 135.0,                 // 比較単位(100g等)あたりの単価
 *       "raw_price": 398,                    // 実売価格(税込)
 *       "deal_type": "週末セール",            // 平日市/週末朝市/通常 等、自由記述
 *       "memo": "倍巻き表記からの推定値のため要確認" // 低信頼度の推定値等の注記(任意)
 *     }
 */
function doGet(e) {
  var params = (e && e.parameter) || {};
  if (!params.items) {
    return jsonResponse_({ ok: true, message: 'Price Tracker Professional WebApp is operational.' });
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
    var result = processIncomingDeals_(items);
    response = { ok: true, updated: result.updated, logged: result.logged, errors: result.errors };
  } catch (err) {
    response = { ok: false, error: err.message };
  }
  return jsonResponse_(response);
}

function processIncomingDeals_(items) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var masterSheet = ss.getSheetByName(MASTER_SHEET_NAME);
  var logSheet = ss.getSheetByName(LOG_SHEET_NAME) || ss.getSheetByName(LEGACY_LOG_SHEET_NAME);
  if (!masterSheet) throw new Error('シートが見つかりません: ' + MASTER_SHEET_NAME);
  if (!logSheet) throw new Error('シートが見つかりません: ' + LOG_SHEET_NAME);

  var updated = 0;
  var logged = 0;
  var errors = [];

  items.forEach(function (item) {
    try {
      if (!item.product_name) throw new Error('product_nameが空です');
      var storeIndex = STORES.indexOf(item.store);
      if (storeIndex === -1) throw new Error('未対応の店舗名: ' + item.store);

      updateMasterRecord_(masterSheet, item, storeIndex);
      updated++;

      appendLogRecord_(logSheet, item);
      logged++;
    } catch (err) {
      errors.push(String(err.message || err));
    }
  });

  return { updated: updated, logged: logged, errors: errors };
}

function updateMasterRecord_(sheet, item, storeIndex) {
  var lastRow = sheet.getLastRow();
  var productNames = lastRow > 1 ? sheet.getRange(2, COL.NAME, lastRow - 1, 1).getValues() : [];
  var targetRow = -1;

  for (var i = 0; i < productNames.length; i++) {
    if (productNames[i][0] === item.product_name) {
      targetRow = i + 2;
      break;
    }
  }

  if (targetRow === -1) {
    targetRow = lastRow + 1;
    sheet.getRange(targetRow, COL.GENRE).setValue(item.genre || '一般');
    sheet.getRange(targetRow, COL.MAKER).setValue(item.maker || '-');
    sheet.getRange(targetRow, COL.NAME).setValue(item.product_name);
    sheet.getRange(targetRow, COL.SPEC).setValue(item.spec || '-');
    sheet.getRange(targetRow, COL.UNIT).setValue(item.unit || '-');

    var storeRangeA1 = sheet.getRange(targetRow, COL.STORE_START, 1, COL.STORE_END - COL.STORE_START + 1).getA1Notation();
    var storeHeaderA1 = sheet.getRange(1, COL.STORE_START, 1, COL.STORE_END - COL.STORE_START + 1).getA1Notation();

    sheet.getRange(targetRow, COL.MIN_PRICE).setFormula('=IFERROR(MIN(' + storeRangeA1 + '), "-")');
    sheet.getRange(targetRow, COL.BEST_STORE).setFormula(
      '=IFERROR(INDEX(' + storeHeaderA1 + ', MATCH(MIN(' + storeRangeA1 + '), ' + storeRangeA1 + ', 0)), "-")'
    );
    sheet.getRange(targetRow, COL.RANKING).setFormula(
      '=IF(COUNT(' + storeRangeA1 + ')=0, "データなし", ' +
      '"🥇 " & INDEX(' + storeHeaderA1 + ', MATCH(SMALL(' + storeRangeA1 + ',1), ' + storeRangeA1 + ', 0)) &' +
      'IF(COUNT(' + storeRangeA1 + ')>1, "  |  🥈 " & INDEX(' + storeHeaderA1 + ', MATCH(SMALL(' + storeRangeA1 + ',2), ' + storeRangeA1 + ', 0)), "") &' +
      'IF(COUNT(' + storeRangeA1 + ')>2, "  |  🥉 " & INDEX(' + storeHeaderA1 + ', MATCH(SMALL(' + storeRangeA1 + ',3), ' + storeRangeA1 + ', 0)), ""))'
    );

    sheet.setRowHeight(targetRow, 32);
    sheet.getRange(targetRow, 1, 1, MASTER_HEADER_COUNT)
      .setVerticalAlignment('middle')
      .setFontFamily(THEME.fontFamily)
      .setFontSize(9.5);

    sheet.getRange(targetRow, COL.STORE_START, 1, COL.STORE_END - COL.STORE_START + 1)
      .setNumberFormat('¥#,##0.0').setHorizontalAlignment('right');
    sheet.getRange(targetRow, COL.MIN_PRICE).setNumberFormat('¥#,##0.0').setHorizontalAlignment('right');
    sheet.getRange(targetRow, COL.GENRE, 1, 2).setHorizontalAlignment('center');
    sheet.getRange(targetRow, COL.SPEC, 1, 2).setHorizontalAlignment('center');
    sheet.getRange(targetRow, COL.BEST_STORE).setHorizontalAlignment('center').setFontWeight('bold').setFontColor(THEME.bestPriceFont);

    if (targetRow % 2 === 0) {
      sheet.getRange(targetRow, 1, 1, MASTER_HEADER_COUNT).setBackground(THEME.zebraEven);
    }
  }

  if (typeof item.unit_price === 'number') {
    sheet.getRange(targetRow, COL.STORE_START + storeIndex).setValue(item.unit_price);
  }
}

function appendLogRecord_(sheet, item) {
  sheet.appendRow([
    new Date(),
    item.store || '',
    item.genre || '',
    item.product_name || '',
    item.spec || '',
    (typeof item.raw_price === 'number') ? item.raw_price : '',
    (typeof item.unit_price === 'number') ? item.unit_price : '',
    item.deal_type || '通常',
    item.memo || '',
  ]);

  var nextRow = sheet.getLastRow();
  sheet.setRowHeight(nextRow, 30);
  sheet.getRange(nextRow, 1, 1, LOG_COLUMN_WIDTHS.length)
    .setVerticalAlignment('middle')
    .setFontFamily(THEME.fontFamily)
    .setFontSize(9.5);

  sheet.getRange(nextRow, LOG_COL.DATE).setNumberFormat('yyyy/mm/dd hh:mm').setHorizontalAlignment('center');
  sheet.getRange(nextRow, LOG_COL.STORE, 1, 2).setHorizontalAlignment('center');
  sheet.getRange(nextRow, LOG_COL.RAW_PRICE).setNumberFormat('¥#,##0').setHorizontalAlignment('right');
  sheet.getRange(nextRow, LOG_COL.UNIT_PRICE).setNumberFormat('¥#,##0.0').setHorizontalAlignment('right');
  sheet.getRange(nextRow, LOG_COL.DEAL_TYPE).setHorizontalAlignment('center');
  sheet.getRange(nextRow, LOG_COL.NAME).setWrapStrategy(SpreadsheetApp.WrapStrategy.WRAP);

  if (nextRow % 2 === 0) {
    sheet.getRange(nextRow, 1, 1, LOG_COLUMN_WIDTHS.length).setBackground(THEME.zebraEven);
  }
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
