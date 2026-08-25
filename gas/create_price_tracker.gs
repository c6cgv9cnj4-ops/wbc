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
 *    - STORES配列(監視対象店舗)を追加・削除・並び替えた直後は、必ず
 *      migrateStoreListChange を1回だけ実行する(店舗名をヘッダーのテキストで
 *      照合し、既存の各店舗の実売価格・換算単価データを新しい列位置へ書き直す。
 *      STORES配列から無くなった店舗の列は破棄される)。
 *    - 現在のシートが「2行階層ヘッダー・サマリー列が末尾(店舗の右側)」の状態の場合は
 *      migrateSummaryColumnsToFront を1回だけ実行する(サマリー列(実店舗最安単価・
 *      エリア最安店舗・ランキング)が商品名のすぐ右側へ移動し、固定列も拡大される。
 *      既存データはそのまま新しい列位置へ引き継がれる)。
 *    - さらに古い「1行ヘッダー・横展開レイアウト」から移行する場合は、先に
 *      migrateToTwoRowHeader を実行してから migrateSummaryColumnsToFront を実行する。
 *    - さらにさらに古い「1店舗1列(換算単価のみ)」レイアウトから移行する場合は、
 *      最初に migrateToDualColumnLayout を実行してから上記の順で進める。
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

// 追跡対象店舗(計6店舗。ヨークマート・業務スーパーは監視対象から削除した
// (業務スーパーは公式サイト・トクバイともに取得手段が無いため2026-08-26に除外)。
// ウエルシアは生鮮・米を除く「ペーパー類・洗剤・調味料・飲料」のみが対象。
// scripts/fetch_deals.py の DEALS_STORES の category_scope と対応させる)
var STORES = ['ロヂャース北本店', 'マルサン桶川店', 'ヤオコー', 'ベルク', 'とりせん', 'ウエルシア'];

var THEME = {
  headerBg: '#1E293B',        // スレートネイビー
  headerFont: '#FFFFFF',
  zebraEven: '#F8FAFC',       // ゼブラ背景(偶数行)
  bestPriceFont: '#166534',
  fontFamily: 'Roboto, "Noto Sans JP", sans-serif',
};

// ダッシュボードシートの列。
// 商品名のすぐ右側にサマリー列(実店舗最安単価・エリア最安店舗・ランキング)を配置し、
// その右にAmazon基準単価・各店舗の実売価格(内容量)/換算単価が続く横展開レイアウト。
// サマリー列までを固定表示(FROZEN_COLUMNS)することで、店舗別の詳細を横スクロールで
// 見ている間も「結局どこが一番安いか」が常に視界に残るようにしている。
// ヘッダーは2行構成(1行目=親見出し(店舗名等、横結合/縦結合)、2行目=子見出し
// (実売価格(内容量)/換算単価))で、データは3行目から始まる。
var COL = {
  GENRE: 1, MAKER: 2, NAME: 3, SPEC: 4, UNIT: 5,
  MIN_PRICE: 6, BEST_STORE: 7, RANKING: 8,
  AMAZON_PRICE: 9, AMAZON_UNIT: 10,
  STORE_START: 11, // K列から店舗数×2列ぶん
};
COL.STORE_END = COL.STORE_START + STORES.length * 2 - 1;
var TOTAL_COLS = COL.STORE_END;      // ダッシュボードの総列数(店舗ブロックが最終列)
var FROZEN_COLUMNS = COL.RANKING;    // ジャンル〜ランキングまでを固定表示

var HEADER_ROWS = 2;      // ヘッダーが占める行数(1行目=親見出し、2行目=子見出し)
var DATA_START_ROW = 3;   // 商品データの開始行

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

// 2行階層ヘッダー(1行目=親見出し、2行目=子見出し)を構築する。
// 呼び出すたびに冪等(既存のヘッダー2行を一旦解除・クリアしてから再構築する)。
// データ行(3行目以降)には触れない(数値書式の再適用のみ行う)。
function buildMasterDashboardSheet_(sheet) {
  sheet.setTabColor('#0284C7');

  // セル結合の前に固定行・固定列を一旦全解除する。
  // (固定行/列の境界をまたぐセル結合はGASでエラーになるため、旧レイアウトの
  // setFrozenRows(1)等が残ったまま1〜2行目を結合しようとすると
  // 「固定されている行と固定されていない行は結合できません」で失敗する)
  sheet.setFrozenRows(0);
  sheet.setFrozenColumns(0);

  // 既存のヘッダー2行分の結合・内容を一旦リセットしてから組み直す(冪等にするため)
  var headerBlock = sheet.getRange(1, 1, HEADER_ROWS, TOTAL_COLS);
  headerBlock.breakApart();
  headerBlock.clearContent();

  // --- 固定項目(ジャンル〜単位): 1〜2行目を縦結合して1つの見出しにする ---
  var fixedLabels = [
    { col: COL.GENRE, label: 'ジャンル' },
    { col: COL.MAKER, label: 'メーカー' },
    { col: COL.NAME, label: '商品名' },
    { col: COL.SPEC, label: '規格・容量' },
    { col: COL.UNIT, label: '単位' },
  ];
  fixedLabels.forEach(function (f) {
    sheet.getRange(1, f.col, HEADER_ROWS, 1).merge().setValue(f.label);
  });

  // --- サマリー項目(実店舗最安単価・エリア最安店舗・ランキング): 商品名のすぐ右側、縦結合 ---
  var summaryLabels = [
    { col: COL.MIN_PRICE, label: '実店舗最安単価' },
    { col: COL.BEST_STORE, label: 'エリア最安店舗' },
    { col: COL.RANKING, label: '買い推奨・価格順位ランキング' },
  ];
  summaryLabels.forEach(function (f) {
    sheet.getRange(1, f.col, HEADER_ROWS, 1).merge().setValue(f.label);
  });

  // --- Amazon基準単価: 1行目を横結合して親見出し、2行目に子見出し2つ ---
  sheet.getRange(1, COL.AMAZON_PRICE, 1, 2).merge().setValue('Amazon基準単価');
  sheet.getRange(2, COL.AMAZON_PRICE).setValue('実売価格(内容量)');
  sheet.getRange(2, COL.AMAZON_UNIT).setValue('換算単価');
  sheet.getRange(2, COL.AMAZON_PRICE).setNote(
    '自動取得していません。マスタデータ(基準価格・容量)として、ご自身で確認した実売価格と' +
    '内容量を手動入力してください(例: 1,280円(500ml))。空欄なら実店舗内ランキングのみ表示されます。'
  );
  sheet.getRange(2, COL.AMAZON_UNIT).setNote(
    '左のAmazon実売価格(内容量)から算出した100g/100mlあたりの換算単価を手動入力してください。' +
    'この列の数値が実店舗の換算単価と比較され、ランキングに反映されます。'
  );

  // --- 各店舗: 1行目を横結合して店舗名、2行目に子見出し2つ ---
  STORES.forEach(function (s, i) {
    var priceCol = getStorePriceColumn_(i);
    sheet.getRange(1, priceCol, 1, 2).merge().setValue(s);
    sheet.getRange(2, priceCol).setValue('実売価格(内容量)');
    sheet.getRange(2, priceCol + 1).setValue('換算単価');
  });

  headerBlock.setBackground(THEME.headerBg)
    .setFontColor(THEME.headerFont)
    .setFontWeight('bold')
    .setFontSize(9.5)
    .setVerticalAlignment('middle')
    .setHorizontalAlignment('center')
    .setWrap(true);

  sheet.setRowHeight(1, 28);
  sheet.setRowHeight(2, 34);
  sheet.setFrozenRows(HEADER_ROWS);
  sheet.setFrozenColumns(FROZEN_COLUMNS); // ジャンル〜ランキングまで固定(商品名側にサマリーを寄せたため)

  var widths = [110, 110, 220, 100, 70]; // ジャンル/メーカー/商品名/規格・容量/単位
  for (var i = 0; i < widths.length; i++) {
    sheet.setColumnWidth(i + 1, widths[i]);
  }
  sheet.setColumnWidth(COL.MIN_PRICE, 110);
  sheet.setColumnWidth(COL.BEST_STORE, 130);
  sheet.setColumnWidth(COL.RANKING, 320);
  sheet.setColumnWidth(COL.AMAZON_PRICE, 90);
  sheet.setColumnWidth(COL.AMAZON_UNIT, 85);
  for (var s = 0; s < STORES.length; s++) {
    sheet.setColumnWidth(getStorePriceColumn_(s), 90);
    sheet.setColumnWidth(getStoreUnitColumn_(s), 85);
  }

  // 既存データ行(3行目以降)があれば、換算単価列の書式(3桁カンマ+右寄せ)を再適用する
  var lastRow = sheet.getLastRow();
  if (lastRow >= DATA_START_ROW) {
    var rows = lastRow - DATA_START_ROW + 1;
    sheet.getRange(DATA_START_ROW, COL.AMAZON_UNIT, rows, 1).setNumberFormat('¥#,##0.0').setHorizontalAlignment('right');
    for (var u = 0; u < STORES.length; u++) {
      sheet.getRange(DATA_START_ROW, getStoreUnitColumn_(u), rows, 1).setNumberFormat('¥#,##0.0').setHorizontalAlignment('right');
      sheet.getRange(DATA_START_ROW, getStorePriceColumn_(u), rows, 1).setHorizontalAlignment('center');
    }
    sheet.getRange(DATA_START_ROW, COL.MIN_PRICE, rows, 1).setNumberFormat('¥#,##0.0').setHorizontalAlignment('right');
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
 * 【1回だけ実行する移行関数・その1】
 * 旧レイアウト(店舗1列=換算単価のみ、ヨークマート列を含む、1行ヘッダー)のダッシュボードから、
 * 横展開レイアウト(店舗2列=実売価格(内容量)+換算単価、ヨークマート除外、1行ヘッダー、
 * サマリー列は末尾)へデータを移行する。
 *
 * ※現在のシートが既に横展開レイアウトになっている場合は、安全のため何もせず終了する
 * (2行階層ヘッダーやサマリー列の位置変更は、この後 migrateToTwoRowHeader /
 * migrateSummaryColumnsToFront を続けて実行すること)。
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

  var headerRow1 = sheet.getRange(1, 1, 1, lastCol).getValues()[0];
  if (headerRow1.indexOf('Amazon実売価格(内容量)') !== -1 || headerRow1.indexOf('Amazon基準単価') !== -1) {
    Logger.log('既に横展開レイアウトのため、移行をスキップしました。');
    return;
  }

  // この時点での旧レイアウト(1店舗1列)は、固定項目1〜5列・Amazon6列目・店舗7列目以降・
  // サマリー3列が末尾、という並びだった。ヘッダーのテキストを見て動的に対応させる。
  var fixedHeaders = ['ジャンル', 'メーカー', '商品名', '規格・容量', '単位', 'Amazon基準単価'];
  var summaryHeaders = ['実店舗最安単価', 'エリア最安店舗', '買い推奨・価格順位ランキング'];
  var oldAmazonCol = headerRow1.indexOf('Amazon基準単価') + 1; // 見つからなければ0

  var oldStoreCols = []; // { name: '店舗名', col: 列番号 }
  for (var c = 1; c <= lastCol; c++) {
    var h = headerRow1[c - 1];
    if (!h || fixedHeaders.indexOf(h) !== -1 || summaryHeaders.indexOf(h) !== -1) continue;
    oldStoreCols.push({ name: h, col: c });
  }

  var droppedStores = oldStoreCols
    .map(function (sc) { return sc.name; })
    .filter(function (name) { return STORES.indexOf(name) === -1; });

  var dataRange = sheet.getRange(2, 1, lastRow - 1, lastCol).getValues();
  var records = dataRange.map(function (row) {
    var rec = {
      genre: row[0], maker: row[1], name: row[2], spec: row[3], unit: row[4],
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
  buildMasterDashboardSheet_(sheet); // 現行のCOL配置(サマリーが商品名側)で組み直される

  records.forEach(function (rec, i) {
    if (!rec.name) return; // 空行はスキップ
    var row = i + 2; // この移行元は1行ヘッダー前提のため、データはまだ2行目から書き込む
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

  var msg = '横展開レイアウトへの移行が完了しました: ' + records.length + '行。';
  if (droppedStores.length > 0) {
    msg += ' 監視対象から外れたため破棄した列: ' + droppedStores.join(', ') + '。';
  }
  msg += ' 続けて migrateToTwoRowHeader → migrateSummaryColumnsToFront の順で実行してください。';
  Logger.log(msg);
  return msg;
}

/**
 * 【1回だけ実行する移行関数・その2】
 * 「横展開レイアウト・1行ヘッダー(データは2行目から、サマリー列は末尾)」のダッシュボードを、
 * 「横展開レイアウト・2行階層ヘッダー(データは3行目から、サマリー列はまだ末尾)」へ移行する。
 * この段階では列の意味・並び順は一切変わらないため、既存データ行を1行下へずらす
 * (insertRowBefore)だけでよく、値の再配置は不要。
 *
 * 【注意】この関数は「サマリー列が末尾にある」時点のCOL配置を前提にした過渡的な移行関数。
 * 現行バージョンのCOLはサマリー列が商品名側に既に移動済みのため、この関数の後は
 * 必ず migrateSummaryColumnsToFront を実行すること(実行し忘れると、店舗データが
 * サマリー列の位置に誤って表示される)。
 */
function migrateToTwoRowHeader() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(MASTER_SHEET_NAME);
  if (!sheet) {
    throw new Error('シートが見つかりません: ' + MASTER_SHEET_NAME);
  }

  // 「サマリー列が末尾にある」旧配置での店舗開始列(8列目)を基準に判定する
  var LEGACY_STORE_START = 8;
  if (sheet.getRange(2, LEGACY_STORE_START).getValue() === '実売価格(内容量)') {
    Logger.log('既に2行階層ヘッダーのため、移行をスキップしました。続けて migrateSummaryColumnsToFront を実行してください。');
    return;
  }

  var lastRow = sheet.getLastRow();

  if (lastRow >= 2) {
    // 1行目(旧ヘッダー)の下に空行を1行挿入し、既存データ(旧2行目〜)を1行下へずらす。
    // Google Sheetsは行挿入時にセル内の数式の行参照を自動追従させるため、
    // 各行の実店舗最安単価等の数式は書き換え不要(再設定は安全のための保険)。
    sheet.insertRowBefore(2);
  }

  buildMasterDashboardSheet_(sheet); // 1〜2行目を2行階層ヘッダーとして組み直す(現行のCOL配置になる点に注意)

  var newLastRow = sheet.getLastRow();
  if (newLastRow >= DATA_START_ROW) {
    var names = sheet.getRange(DATA_START_ROW, COL.NAME, newLastRow - DATA_START_ROW + 1, 1).getValues();
    for (var i = 0; i < names.length; i++) {
      if (!names[i][0]) continue;
      var row = i + DATA_START_ROW;
      setRowFormulas_(sheet, row);
      applyRowStyle_(sheet, row);
    }
  }

  Logger.log('2行階層ヘッダーへの移行が完了しました。データは' + DATA_START_ROW + '行目から始まります。続けて migrateSummaryColumnsToFront を実行してください。');
}

/**
 * 【1回だけ実行する移行関数・その3】
 * 「2行階層ヘッダー・サマリー列が末尾(店舗の右側)」のダッシュボードを、
 * 「2行階層ヘッダー・サマリー列が商品名のすぐ右側」の現行レイアウトへ移行する。
 * 固定項目(1〜5列)とデータ開始行(3行目)は変わらないため、Amazon・各店舗のセル値を
 * 旧列位置から読み出して新しい列位置へ書き直し、サマリー列は数式を再設定する。
 *
 * 既に現行レイアウトの場合は、安全のため何もせず終了する。
 */
function migrateSummaryColumnsToFront() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(MASTER_SHEET_NAME);
  if (!sheet) {
    throw new Error('シートが見つかりません: ' + MASTER_SHEET_NAME);
  }

  if (sheet.getRange(1, COL.MIN_PRICE).getValue() === '実店舗最安単価') {
    Logger.log('既にサマリー列が商品名側にあるため、移行をスキップしました。');
    return;
  }

  var lastRow = sheet.getLastRow();
  if (lastRow < DATA_START_ROW) {
    buildMasterDashboardSheet_(sheet);
    Logger.log('データ行が無いため、新レイアウトでの初期化のみ実行しました。');
    return;
  }

  // 「サマリー列が末尾にある」旧配置での列番号(固定項目1〜5列は現行と共通)
  var OLD_AMAZON_PRICE = 6, OLD_AMAZON_UNIT = 7, OLD_STORE_START = 8;

  var lastCol = sheet.getLastColumn();
  var dataRange = sheet.getRange(DATA_START_ROW, 1, lastRow - DATA_START_ROW + 1, lastCol).getValues();

  var records = dataRange.map(function (row) {
    var rec = {
      genre: row[COL.GENRE - 1],
      maker: row[COL.MAKER - 1],
      name: row[COL.NAME - 1],
      spec: row[COL.SPEC - 1],
      unit: row[COL.UNIT - 1],
      amazonPrice: row[OLD_AMAZON_PRICE - 1],
      amazonUnit: row[OLD_AMAZON_UNIT - 1],
      storePrices: [],
      storeUnits: [],
    };
    for (var i = 0; i < STORES.length; i++) {
      rec.storePrices.push(row[(OLD_STORE_START + i * 2) - 1]);
      rec.storeUnits.push(row[(OLD_STORE_START + i * 2 + 1) - 1]);
    }
    return rec;
  });

  sheet.getRange(DATA_START_ROW, 1, lastRow - DATA_START_ROW + 1, lastCol).clearContent().clearFormat();
  buildMasterDashboardSheet_(sheet); // 現行のCOL配置(サマリーが商品名側)でヘッダー・幅を組み直す

  records.forEach(function (rec, i) {
    if (!rec.name) return; // 空行はスキップ
    var row = i + DATA_START_ROW;
    sheet.getRange(row, COL.GENRE).setValue(rec.genre || '一般');
    sheet.getRange(row, COL.MAKER).setValue(rec.maker || '-');
    sheet.getRange(row, COL.NAME).setValue(rec.name);
    sheet.getRange(row, COL.SPEC).setValue(rec.spec || '-');
    sheet.getRange(row, COL.UNIT).setValue(rec.unit || '-');
    if (rec.amazonPrice) {
      sheet.getRange(row, COL.AMAZON_PRICE).setValue(rec.amazonPrice);
    }
    if (typeof rec.amazonUnit === 'number') {
      sheet.getRange(row, COL.AMAZON_UNIT).setValue(rec.amazonUnit);
    }
    for (var si = 0; si < STORES.length; si++) {
      if (rec.storePrices[si]) {
        sheet.getRange(row, getStorePriceColumn_(si)).setValue(rec.storePrices[si]);
      }
      if (typeof rec.storeUnits[si] === 'number') {
        sheet.getRange(row, getStoreUnitColumn_(si)).setValue(rec.storeUnits[si]);
      }
    }
    setRowFormulas_(sheet, row);
    applyRowStyle_(sheet, row);
  });

  Logger.log(
    'サマリー列(実店舗最安単価・エリア最安店舗・ランキング)を商品名側へ移動する移行が完了しました: ' +
    records.length + '行。固定列も' + FROZEN_COLUMNS + '列目まで拡大されています。'
  );
}

/**
 * 【STORES配列を変更した直後に実行する汎用移行関数】
 * STORES配列(監視対象店舗)に対して店舗の追加・削除・並び替えを行った後、
 * 既存データを新しい店舗構成に合わせて列を再構築する。
 *
 * 現在のシート上のヘッダーの店舗名テキストを見て、旧データを店舗名でマッチングし、
 * 新しいSTORES配列の並びで書き直す(STORES配列に無くなった店舗のデータは破棄され、
 * 新しく追加された店舗の列は空欄で用意される)。列の総数・レイアウト自体(2行階層
 * ヘッダー・サマリー列の位置)は変更しない。
 *
 * 既にヘッダーの店舗名構成がSTORES配列と完全一致している場合は、安全のため
 * 何もせず終了する。
 */
function migrateStoreListChange() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(MASTER_SHEET_NAME);
  if (!sheet) {
    throw new Error('シートが見つかりません: ' + MASTER_SHEET_NAME);
  }

  var lastRow = sheet.getLastRow();
  var lastCol = sheet.getLastColumn();

  if (lastRow < DATA_START_ROW) {
    buildMasterDashboardSheet_(sheet);
    Logger.log('データ行が無いため、新しい店舗構成での初期化のみ実行しました。');
    return;
  }

  // 現在の店舗ブロック(STORE_START列以降、2列おき)を、店舗名ごとの価格/単価列に
  // マッピングする(店舗名はヘッダー1行目の結合セルの左上アンカーから読み取る)
  var oldStoreCols = {}; // { '店舗名': { priceCol: 列番号, unitCol: 列番号 } }
  for (var c = COL.STORE_START; c <= lastCol; c += 2) {
    var storeName = sheet.getRange(1, c).getValue();
    if (storeName) {
      oldStoreCols[storeName] = { priceCol: c, unitCol: c + 1 };
    }
  }

  var currentNames = Object.keys(oldStoreCols);
  var sameOrder = currentNames.length === STORES.length &&
    currentNames.every(function (name, i) { return STORES[i] === name; });
  if (sameOrder) {
    Logger.log('店舗構成は既に最新のため、移行をスキップしました。');
    return;
  }

  var droppedStores = currentNames.filter(function (name) { return STORES.indexOf(name) === -1; });

  var dataRange = sheet.getRange(DATA_START_ROW, 1, lastRow - DATA_START_ROW + 1, lastCol).getValues();
  var records = dataRange.map(function (row) {
    var rec = {
      genre: row[COL.GENRE - 1],
      maker: row[COL.MAKER - 1],
      name: row[COL.NAME - 1],
      spec: row[COL.SPEC - 1],
      unit: row[COL.UNIT - 1],
      amazonPrice: row[COL.AMAZON_PRICE - 1],
      amazonUnit: row[COL.AMAZON_UNIT - 1],
      storePrices: {},
      storeUnits: {},
    };
    currentNames.forEach(function (name) {
      var cols = oldStoreCols[name];
      rec.storePrices[name] = row[cols.priceCol - 1];
      rec.storeUnits[name] = row[cols.unitCol - 1];
    });
    return rec;
  });

  sheet.getRange(DATA_START_ROW, 1, lastRow - DATA_START_ROW + 1, lastCol).clearContent().clearFormat();
  buildMasterDashboardSheet_(sheet); // 新STORES配列でヘッダー・列幅を再構築

  records.forEach(function (rec, i) {
    if (!rec.name) return; // 空行はスキップ
    var row = i + DATA_START_ROW;
    sheet.getRange(row, COL.GENRE).setValue(rec.genre || '一般');
    sheet.getRange(row, COL.MAKER).setValue(rec.maker || '-');
    sheet.getRange(row, COL.NAME).setValue(rec.name);
    sheet.getRange(row, COL.SPEC).setValue(rec.spec || '-');
    sheet.getRange(row, COL.UNIT).setValue(rec.unit || '-');
    if (rec.amazonPrice) {
      sheet.getRange(row, COL.AMAZON_PRICE).setValue(rec.amazonPrice);
    }
    if (typeof rec.amazonUnit === 'number') {
      sheet.getRange(row, COL.AMAZON_UNIT).setValue(rec.amazonUnit);
    }
    STORES.forEach(function (storeName, si) {
      var price = rec.storePrices[storeName];
      var unit = rec.storeUnits[storeName];
      if (price) {
        sheet.getRange(row, getStorePriceColumn_(si)).setValue(price);
      }
      if (typeof unit === 'number') {
        sheet.getRange(row, getStoreUnitColumn_(si)).setValue(unit);
      }
    });
    setRowFormulas_(sheet, row);
    applyRowStyle_(sheet, row);
  });

  var msg = '店舗構成の変更を反映しました: ' + records.length + '行。';
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
  sheet.getRange(row, 1, 1, TOTAL_COLS)
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
    sheet.getRange(row, 1, 1, TOTAL_COLS).setBackground(THEME.zebraEven);
  } else {
    sheet.getRange(row, 1, 1, TOTAL_COLS).setBackground(null);
  }
}

function updateMasterRecord_(sheet, item, storeIndex) {
  var lastRow = sheet.getLastRow();
  var productNames = lastRow >= DATA_START_ROW
    ? sheet.getRange(DATA_START_ROW, COL.NAME, lastRow - DATA_START_ROW + 1, 1).getValues()
    : [];
  var targetRow = -1;

  for (var i = 0; i < productNames.length; i++) {
    if (productNames[i][0] === item.product_name) {
      targetRow = i + DATA_START_ROW;
      break;
    }
  }

  if (targetRow === -1) {
    targetRow = Math.max(lastRow + 1, DATA_START_ROW);
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
