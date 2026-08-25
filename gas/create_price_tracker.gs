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
 * 3. 関数選択プルダウンで実行する関数を選ぶ:
 *    - 初めてこのシートに導入する場合、または旧レイアウト(店舗1列/ヨークマート含む)から
 *      横展開レイアウト(店舗2列/ヨークマート除外)へ移行する場合は
 *      migrateToDualColumnLayout を1回だけ実行する(既存データは新レイアウトに変換して
 *      引き継がれる。ヨークマート列のデータのみ引き継がれず破棄される)。
 *    - 単に書式だけを再適用したい場合は applyFormattingToExistingSheet を実行する。
 *    - まだ一度もこのシートで初期化していない場合は buildPriceTracker を実行する。
 *    (どの関数も、このシートに紐づいた状態のApps Scriptとして実行すること。既存の
 *    「価格ログ蓄積」シートのデータはそのまま引き継がれ、削除されない)
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

// 追跡対象店舗(計7店舗。ヨークマートは監視対象から削除した。
// ウエルシアは生鮮・米を除く「ペーパー類・洗剤・調味料・飲料」のみが対象。
// scripts/fetch_deals.py の DEALS_STORES の category_scope と対応させる)
var STORES = ['ロヂャース北本店', 'マルサン桶川店', '業務スーパー', 'ヤオコー', 'ベルク', 'とりせん', 'ウエルシア'];

var THEME = {
  headerBg: '#1E293B',        // スレートネイビー
  headerFont: '#FFFFFF',
  zebraEven: '#F8FAFC',       // ゼブラ背景(偶数行)
  bestPriceFont: '#166534',
  fontFamily: 'Roboto, "Noto Sans JP", sans-serif',
};

// ダッシュボードシートの列。
// 店舗ごとに「実売価格(内容量表示、テキスト)」「換算単価(数値、比較・ランキング用)」の
// 2列を持つ横展開レイアウト(Amazon基準単価も同様に2列)。
var COL = {
  GENRE: 1, MAKER: 2, NAME: 3, SPEC: 4, UNIT: 5,
  AMAZON_PRICE: 6, AMAZON_UNIT: 7,
  STORE_START: 8, // H列から店舗数×2列ぶん
};
COL.STORE_END = COL.STORE_START + STORES.length * 2 - 1;
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
// 列番号ヘルパー(店舗ごとに2列を割り当てる横展開レイアウト用)
// =====================================================================

function getStorePriceColumn_(storeIndex) {
  return COL.STORE_START + storeIndex * 2;
}
function getStoreUnitColumn_(storeIndex) {
  return COL.STORE_START + storeIndex * 2 + 1;
}
function columnToLetter_(col) {
  var letter = '';
  while (col > 0) {
    var rem = (col - 1) % 26;
    letter = String.fromCharCode(65 + rem) + letter;
    col = Math.floor((col - 1) / 26);
  }
  return letter;
}

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

  var headers = ['ジャンル', 'メーカー', '商品名', '規格・容量', '単位', 'Amazon実売価格(内容量)', 'Amazon換算単価'];
  STORES.forEach(function (s) {
    headers.push(s + ' 実売価格(内容量)');
    headers.push(s + ' 換算単価');
  });
  headers.push('実店舗最安単価', 'エリア最安店舗', '買い推奨・価格順位ランキング');

  sheet.getRange(1, 1, 1, headers.length).setValues([headers]);

  var headerRange = sheet.getRange(1, 1, 1, headers.length);
  headerRange.setBackground(THEME.headerBg)
    .setFontColor(THEME.headerFont)
    .setFontWeight('bold')
    .setFontSize(10)
    .setVerticalAlignment('middle')
    .setHorizontalAlignment('center');

  sheet.getRange(1, COL.AMAZON_PRICE).setNote(
    '自動取得していません。マスタデータ(基準価格・容量)として、ご自身で確認した実売価格と' +
    '内容量を手動入力してください(例: 1,280円(500ml))。空欄なら実店舗内ランキングのみ表示されます。'
  );
  sheet.getRange(1, COL.AMAZON_UNIT).setNote(
    '左のAmazon実売価格(内容量)から算出した100g/100mlあたりの換算単価を手動入力してください。' +
    'この列の数値が実店舗の換算単価と比較され、ランキングに反映されます。'
  );

  sheet.setRowHeight(1, 40);
  sheet.setFrozenRows(1);
  sheet.setFrozenColumns(3); // 商品名まで固定

  var widths = [110, 110, 220, 100, 70]; // ジャンル/メーカー/商品名/規格・容量/単位
  for (var i = 0; i < widths.length; i++) {
    sheet.setColumnWidth(i + 1, widths[i]);
  }
  sheet.setColumnWidth(COL.AMAZON_PRICE, 150);
  sheet.setColumnWidth(COL.AMAZON_UNIT, 100);
  for (var s = 0; s < STORES.length; s++) {
    sheet.setColumnWidth(getStorePriceColumn_(s), 120);
    sheet.setColumnWidth(getStoreUnitColumn_(s), 95);
  }
  sheet.setColumnWidth(COL.MIN_PRICE, 110);
  sheet.setColumnWidth(COL.BEST_STORE, 130);
  sheet.setColumnWidth(COL.RANKING, 320);

  // 既存データ行があれば、換算単価列の書式(3桁カンマ+右寄せ)を再適用する
  var lastRow = sheet.getLastRow();
  if (lastRow >= 2) {
    var rows = lastRow - 1;
    sheet.getRange(2, COL.AMAZON_UNIT, rows, 1).setNumberFormat('¥#,##0.0').setHorizontalAlignment('right');
    for (var u = 0; u < STORES.length; u++) {
      sheet.getRange(2, getStoreUnitColumn_(u), rows, 1).setNumberFormat('¥#,##0.0').setHorizontalAlignment('right');
      sheet.getRange(2, getStorePriceColumn_(u), rows, 1).setHorizontalAlignment('center');
    }
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

/**
 * 【1回だけ実行する移行関数】
 * 旧レイアウト(店舗1列=換算単価のみ、ヨークマート列を含む)のダッシュボードから、
 * 新レイアウト(店舗2列=実売価格(内容量)+換算単価、ヨークマート除外)へデータを移行する。
 *
 * 既存の商品行(ジャンル/メーカー/商品名/規格・容量/単位/Amazon基準単価/各店舗の換算単価)は
 * ヘッダーのテキストを見て自動的に対応する新しい列へ引き継がれる。
 * ヨークマート列にあったデータのみ、監視対象店舗から削除されたため引き継がれず破棄される。
 * 各店舗の「実売価格(内容量)」列は、旧レイアウトには存在しなかった情報のため空欄になる
 * (次回以降のfetch_deals.py実行時に自動で埋まる)。
 *
 * 既にこのシートが新レイアウトで作成済み(移行不要)の場合は、安全のため何もせず終了する。
 */
function migrateToDualColumnLayout() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(MASTER_SHEET_NAME);
  if (!sheet) {
    throw new Error('シートが見つかりません: ' + MASTER_SHEET_NAME);
  }

  var lastRow = sheet.getLastRow();
  var lastCol = sheet.getLastColumn();

  if (lastRow < 2) {
    buildMasterDashboardSheet_(sheet);
    Logger.log('データ行が無いため、新レイアウトでの初期化のみ実行しました。');
    return;
  }

  var headerRow = sheet.getRange(1, 1, 1, lastCol).getValues()[0];
  if (headerRow.indexOf('Amazon実売価格(内容量)') !== -1) {
    Logger.log('既に新レイアウト(横展開)のため、移行をスキップしました。');
    return;
  }

  var fixedHeaders = ['ジャンル', 'メーカー', '商品名', '規格・容量', '単位', 'Amazon基準単価'];
  var summaryHeaders = ['実店舗最安単価', 'エリア最安店舗', '買い推奨・価格順位ランキング'];
  var oldAmazonCol = headerRow.indexOf('Amazon基準単価') + 1; // 見つからなければ0

  var oldStoreCols = []; // { name: '店舗名', col: 列番号 }
  for (var c = 1; c <= lastCol; c++) {
    var h = headerRow[c - 1];
    if (!h || fixedHeaders.indexOf(h) !== -1 || summaryHeaders.indexOf(h) !== -1) continue;
    oldStoreCols.push({ name: h, col: c });
  }

  var droppedStores = oldStoreCols
    .map(function (sc) { return sc.name; })
    .filter(function (name) { return STORES.indexOf(name) === -1; });

  var dataRange = sheet.getRange(2, 1, lastRow - 1, lastCol).getValues();
  var records = dataRange.map(function (row) {
    var rec = {
      genre: row[COL.GENRE - 1],
      maker: row[COL.MAKER - 1],
      name: row[COL.NAME - 1],
      spec: row[COL.SPEC - 1],
      unit: row[COL.UNIT - 1],
      amazonUnit: oldAmazonCol ? row[oldAmazonCol - 1] : '',
      storeUnitPrices: {},
    };
    oldStoreCols.forEach(function (sc) {
      rec.storeUnitPrices[sc.name] = row[sc.col - 1];
    });
    return rec;
  });

  // 既存データ範囲をクリアしてから新レイアウトのヘッダー・書式を構築する
  sheet.getRange(2, 1, lastRow - 1, lastCol).clearContent().clearFormat();
  buildMasterDashboardSheet_(sheet);

  records.forEach(function (rec, i) {
    if (!rec.name) return; // 空行はスキップ
    var row = i + 2;
    sheet.getRange(row, COL.GENRE).setValue(rec.genre || '一般');
    sheet.getRange(row, COL.MAKER).setValue(rec.maker || '-');
    sheet.getRange(row, COL.NAME).setValue(rec.name);
    sheet.getRange(row, COL.SPEC).setValue(rec.spec || '-');
    sheet.getRange(row, COL.UNIT).setValue(rec.unit || '-');
    if (typeof rec.amazonUnit === 'number') {
      sheet.getRange(row, COL.AMAZON_UNIT).setValue(rec.amazonUnit);
    }
    STORES.forEach(function (storeName, si) {
      var v = rec.storeUnitPrices[storeName];
      if (typeof v === 'number') {
        sheet.getRange(row, getStoreUnitColumn_(si)).setValue(v);
      }
    });
    setRowFormulas_(sheet, row);
    applyRowStyle_(sheet, row);
  });

  var msg = '新レイアウト(横展開)への移行が完了しました: ' + records.length + '行。';
  if (droppedStores.length > 0) {
    msg += ' 監視対象から外れたため破棄した列: ' + droppedStores.join(', ') + '。';
  }
  Logger.log(msg);
  return msg;
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

// 新規商品行を1行分作成し、数式・書式を適用する(値の書き込みは呼び出し元で行う)
function initNewRow_(sheet, row, item) {
  sheet.getRange(row, COL.GENRE).setValue(item.genre || '一般');
  sheet.getRange(row, COL.MAKER).setValue(item.maker || '-');
  sheet.getRange(row, COL.NAME).setValue(item.product_name);
  sheet.getRange(row, COL.SPEC).setValue(item.spec || '-');
  sheet.getRange(row, COL.UNIT).setValue(item.unit || '-');

  setRowFormulas_(sheet, row);
  applyRowStyle_(sheet, row);
}

// 実店舗最安単価・エリア最安店舗・ランキングの数式を1行分設定する。
// 店舗の「換算単価」列は STORE_START から1列おきに並ぶ非連続列のため、
// 単純な範囲参照ではなく配列リテラル({A1,C1,E1,...})を組み立てて参照する。
function setRowFormulas_(sheet, row) {
  var unitRefs = [];
  for (var i = 0; i < STORES.length; i++) {
    unitRefs.push(columnToLetter_(getStoreUnitColumn_(i)) + row);
  }
  var minArgs = unitRefs.join(',');
  var unitArray = '{' + minArgs + '}';
  var nameArray = '{' + STORES.map(function (s) {
    return '"' + String(s).replace(/"/g, '""') + '"';
  }).join(',') + '}';

  sheet.getRange(row, COL.MIN_PRICE).setFormula('=IFERROR(MIN(' + minArgs + '), "-")');
  sheet.getRange(row, COL.BEST_STORE).setFormula(
    '=IFERROR(INDEX(' + nameArray + ', MATCH(MIN(' + minArgs + '), ' + unitArray + ', 0)), "-")'
  );
  sheet.getRange(row, COL.RANKING).setFormula(
    '=IF(COUNT(' + minArgs + ')=0, "データなし", ' +
    '"🥇 " & INDEX(' + nameArray + ', MATCH(SMALL(' + unitArray + ',1), ' + unitArray + ', 0)) &' +
    'IF(COUNT(' + minArgs + ')>1, "  |  🥈 " & INDEX(' + nameArray + ', MATCH(SMALL(' + unitArray + ',2), ' + unitArray + ', 0)), "") &' +
    'IF(COUNT(' + minArgs + ')>2, "  |  🥉 " & INDEX(' + nameArray + ', MATCH(SMALL(' + unitArray + ',3), ' + unitArray + ', 0)), ""))'
  );
}

// 1行分の見た目(行高・フォント・数値書式・ゼブラ等)をまとめて適用する
function applyRowStyle_(sheet, row) {
  sheet.setRowHeight(row, 32);
  sheet.getRange(row, 1, 1, MASTER_HEADER_COUNT)
    .setVerticalAlignment('middle')
    .setFontFamily(THEME.fontFamily)
    .setFontSize(9.5);

  sheet.getRange(row, COL.AMAZON_PRICE).setHorizontalAlignment('center');
  sheet.getRange(row, COL.AMAZON_UNIT).setNumberFormat('¥#,##0.0').setHorizontalAlignment('right');
  for (var i = 0; i < STORES.length; i++) {
    sheet.getRange(row, getStorePriceColumn_(i)).setHorizontalAlignment('center');
    sheet.getRange(row, getStoreUnitColumn_(i)).setNumberFormat('¥#,##0.0').setHorizontalAlignment('right');
  }
  sheet.getRange(row, COL.MIN_PRICE).setNumberFormat('¥#,##0.0').setHorizontalAlignment('right');
  sheet.getRange(row, COL.GENRE, 1, 2).setHorizontalAlignment('center');
  sheet.getRange(row, COL.SPEC, 1, 2).setHorizontalAlignment('center');
  sheet.getRange(row, COL.BEST_STORE).setHorizontalAlignment('center').setFontWeight('bold').setFontColor(THEME.bestPriceFont);

  if (row % 2 === 0) {
    sheet.getRange(row, 1, 1, MASTER_HEADER_COUNT).setBackground(THEME.zebraEven);
  } else {
    sheet.getRange(row, 1, 1, MASTER_HEADER_COUNT).setBackground(null);
  }
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
    initNewRow_(sheet, targetRow, item);
  }

  if (typeof item.unit_price === 'number') {
    sheet.getRange(targetRow, getStoreUnitColumn_(storeIndex)).setValue(item.unit_price);
  }
  if (typeof item.raw_price === 'number') {
    var priceLabel = item.raw_price + '円' + (item.spec && item.spec !== '-' ? '(' + item.spec + ')' : '');
    sheet.getRange(targetRow, getStorePriceColumn_(storeIndex)).setValue(priceLabel);
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
