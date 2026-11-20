/**
 * あんぜんねっと 予備配信（Google Apps Script・30分おきの時間トリガー）
 *
 * 自宅Mac・GitHub Actionsのどちらも止まっているときに、公式Mastodon（@SaitamaSafety）から
 * 県央3市（北本市・鴻巣市・桶川市）の災害発生情報を届ける。
 *
 * 設計の要点（既存の anzn_local.py / anzn_mastodon_only.py と同じ規則）
 * - 重複キーは「anzn:地域コード:記事ID」（記事URL由来）。Mastodon投稿IDは使わない。
 * - 既送信の判定は、Mac・Actionsの既存記録（GitHub上の公開JSON・読み取りのみ）と、GAS自身の記録を合わせて行う。
 *   GitHubへは書き込まない（トークン不要）。
 * - 投稿から30分未満の投稿は送らない（自宅Macが15分おきに先に送れるようにする）。
 * - 送信が2xx（200/204）で成功した記事だけ記録する。失敗した記事は次回再送。
 * - 既存の記録を読めなかった回は送らない（重複より、読めない時の誤送信を避ける）。
 * - 費用0円（個人のGoogleアカウント内。Mastodon公開API・Discord Webhookも無料）。
 *
 * 初回の設定（あなたが行う作業）
 *  1. script.google.com で新規プロジェクトを作り、このファイルの中身を貼り付けて保存する。
 *  2. 左の歯車（プロジェクトの設定）→ スクリプト プロパティ に
 *       DISCORD_WEBHOOK_LOCAL = #webhook_local のWebhook URL
 *     を追加する（値はチャットやコードに書かないこと）。
 *  3. 関数 dryRunAnznFallback を実行し、ログ（表示 → 実行ログ）で候補を確認する（送信しない）。
 *  4. 問題なければ関数 installTrigger30Min を1回実行する（30分おきの定期実行を登録）。
 */

var CONFIG = {
  STATUSES_URL: "https://mastodon.social/api/v1/accounts/112359439018179544/statuses",
  PAGE_LIMIT: 40,              // Mastodonの1回の取得件数
  PAGES: 2,                    // 40件×2ページ（30分おきなので十分な余裕）
  TARGET_CITIES: ["北本市", "鴻巣市", "桶川市"],
  GRACE_MINUTES: 30,           // 投稿からこの時間未満の投稿は次回に回す
  MAX_SEND_PER_RUN: 25,        // Discord Embedのfields上限に合わせる
  OWN_RECORD_KEY: "anzn_gas_sent",
  OWN_RECORD_MAX: 500,         // GAS自身の記録の上限件数（プロパティの容量対策）
  OWN_RECORD_RETENTION_DAYS: 14,
  // 既存の記録（読み取りのみ・公開リポジトリのraw）。Macの送信記録とActionsの送信記録
  REMOTE_RECORDS: [
    "https://raw.githubusercontent.com/c6cgv9cnj4-ops/wbc/main/state/anzn_mac_sent.json",
    "https://raw.githubusercontent.com/c6cgv9cnj4-ops/wbc/main/state/news_seen.json"
  ],
  EMBED_COLOR: 0xE53E3E
};

// ---------- 純粋なロジック（テスト可能） ----------

/** 記事URL（?地域コード&i=記事ID）→ 共通キー "anzn:地域:記事ID"。取れなければ null。 */
function anznKeyFromUrl_(url) {
  var m = /anzn\.net\/sp\/\?([0-9A-Za-z]+)&(?:amp;)?i=(\d+)/.exec(url || "");
  return m ? "anzn:" + m[1] + ":" + m[2] : null;
}

/** HTMLタグを除き、主な実体参照を戻す（Python版の html.unescape の代替。Mastodonの本文で使う範囲）。 */
function plainText_(html) {
  return String(html || "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&quot;/g, "\"").replace(/&#39;/g, "'")
    .replace(/\s+/g, " ")
    .trim();
}

function pad2_(s) {
  return ("0" + s).slice(-2);
}

/** 県央3市の災害発生情報なら {key, datetime, city, summary, url, id, createdAt}。それ以外は null。 */
function parsePost_(status) {
  var raw = status.content || "";
  var text = plainText_(raw);
  if (text.indexOf("災害発生情報") < 0) return null;
  var mc = /^#\s*(\S+?[市町村])\s+(\S+)/.exec(text);
  if (!mc || CONFIG.TARGET_CITIES.indexOf(mc[1]) < 0) return null;
  var city = mc[1];
  var kind = mc[2];
  // 先に生のHTML（hrefの記事URL）を見る。表示テキストは途中で省略されることがあるため後回し
  var key = anznKeyFromUrl_(String(raw).replace(/&amp;/g, "&")) || anznKeyFromUrl_(text.replace(/ /g, ""));
  var md = /(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})/.exec(text);
  var when = md ? pad2_(md[1]) + "-" + pad2_(md[2]) + " " + pad2_(md[3]) + ":" + md[4] + "頃" : "";
  var mp = /、(\S+?)地内/.exec(text);
  var summary = mp ? kind + " " + mp[1] : kind;
  var url = null;
  if (key) {
    var p = key.split(":");
    url = "https://anzn.net/sp/?" + p[1] + "&i=" + p[2];
  }
  return {
    key: key || ("mastodon:" + status.id),
    datetime: when,
    city: city,
    summary: summary,
    url: url,
    id: String(status.id),
    createdAt: status.created_at
  };
}

/** Mastodonの投稿IDは数字の文字列。桁数→辞書順で比較する（古い順）。 */
function cmpId_(a, b) {
  a = String(a); b = String(b);
  if (a.length !== b.length) return a.length - b.length;
  return a < b ? -1 : (a > b ? 1 : 0);
}

/**
 * 送る候補（古い順・最大 maxItems 件）を返す。
 * posts: Mastodonの投稿配列 / knownKeys: 既送信キーの集合（オブジェクト） / nowMs: 現在時刻（ミリ秒）
 */
function selectToSend_(posts, knownKeys, nowMs, graceMs, maxItems) {
  var sorted = posts.slice().sort(function (a, b) { return cmpId_(a.id, b.id); });
  var out = [];
  for (var i = 0; i < sorted.length; i++) {
    var p = sorted[i];
    var created = Date.parse(p.created_at);
    if (isNaN(created)) created = nowMs; // 日時不明は新しい投稿とみなす（Python版と同じ）
    if (created > nowMs - graceMs) break; // 猶予中。以降はさらに新しいので打ち切る
    var item = parsePost_(p);
    if (!item) continue;
    if (knownKeys[item.key]) continue;
    var dup = false;
    for (var j = 0; j < out.length; j++) {
      if (out[j].key === item.key) { dup = true; break; }
    }
    if (dup) continue;
    if (out.length >= maxItems) break;
    out.push(item);
  }
  return out;
}

/** Discord Embed（既存の火災速報と同じ見た目）を組み立てる。 */
function buildEmbed_(items, nowMs) {
  var fields = items.map(function (it) {
    var value = it.summary;
    if (it.url) value += "\n[詳細を見る](" + it.url + ")";
    return {
      name: "🚒 " + it.datetime + "［" + it.city + "］",
      value: value.slice(0, 1024),
      inline: false
    };
  });
  var jst = Utilities.formatDate(new Date(nowMs), "Asia/Tokyo", "yyyy-MM-dd HH:mm");
  return {
    embeds: [{
      title: "🚨 北本市安全安心情報(新着) — 火災・消防出動速報",
      color: CONFIG.EMBED_COLOR,
      fields: fields,
      footer: { text: "あんぜんねっと(埼玉県央)・公式Mastodon経由(予備配信/GAS) / " + jst + " JST時点" }
    }]
  };
}

// ---------- 外部I/O ----------

/** 公開JSON（既存の記録）を読む。失敗・形式不正は例外（呼び出し側で送信を止める）。 */
function fetchRemoteKeys_() {
  var keys = {};
  CONFIG.REMOTE_RECORDS.forEach(function (url) {
    var r = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
    if (r.getResponseCode() !== 200) throw new Error("既存記録の取得失敗 HTTP " + r.getResponseCode());
    var data = JSON.parse(r.getContentText());
    if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("既存記録の形式が想定外です");
    Object.keys(data).forEach(function (k) {
      if (k.indexOf("anzn:") === 0) keys[k] = true;
    });
  });
  return keys;
}

/** 直近の投稿を2ページ分取得する。失敗は例外。 */
function fetchPosts_() {
  var posts = [];
  var maxId = null;
  for (var i = 0; i < CONFIG.PAGES; i++) {
    var url = CONFIG.STATUSES_URL + "?limit=" + CONFIG.PAGE_LIMIT + (maxId ? "&max_id=" + maxId : "");
    var r = UrlFetchApp.fetch(url, { muteHttpExceptions: true, headers: { "User-Agent": "wbc-anzn-safety-gas/1.0" } });
    if (r.getResponseCode() !== 200) throw new Error("Mastodon HTTP " + r.getResponseCode());
    var page = JSON.parse(r.getContentText());
    if (!Array.isArray(page) || page.length === 0) break;
    posts = posts.concat(page);
    maxId = page[page.length - 1].id;
  }
  return posts;
}

/** GAS自身の送信記録（スクリプトプロパティ）。保持期間と件数で刈り込む。 */
function loadOwnRecord_() {
  var raw = PropertiesService.getScriptProperties().getProperty(CONFIG.OWN_RECORD_KEY);
  try {
    return raw ? JSON.parse(raw) : {};
  } catch (e) {
    throw new Error("GAS側の送信記録が壊れています。送信を止めます: " + e.message);
  }
}

function saveOwnRecord_(record, nowMs) {
  var cutoff = new Date(nowMs - CONFIG.OWN_RECORD_RETENTION_DAYS * 86400000).toISOString();
  var entries = Object.keys(record)
    .filter(function (k) { return record[k] >= cutoff; })
    .sort(function (a, b) { return record[a] < record[b] ? 1 : (record[a] > record[b] ? -1 : 0); })
    .slice(0, CONFIG.OWN_RECORD_MAX);
  var trimmed = {};
  entries.forEach(function (k) { trimmed[k] = record[k]; });
  PropertiesService.getScriptProperties().setProperty(CONFIG.OWN_RECORD_KEY, JSON.stringify(trimmed));
}

function sendToDiscord_(webhook, payload) {
  var r = UrlFetchApp.fetch(webhook, {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });
  var code = r.getResponseCode();
  if (code !== 200 && code !== 204) {
    console.log("[WARN] Discord送信失敗 HTTP " + code + "（記録せず次回再送）");
    return false;
  }
  return true;
}

// ---------- 実行入口 ----------

/** 本番: 30分トリガーから呼ばれる。 */
function runAnznFallback() {
  runInternal_(false);
}

/** 試運転: 候補をログに出すだけ（送信も記録もしない）。 */
function dryRunAnznFallback() {
  runInternal_(true);
}

function runInternal_(dryRun) {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) {
    console.log("[SKIP] 別の実行が動いているため、今回は終了します");
    return;
  }
  try {
    var nowMs = Date.now();
    var webhook = PropertiesService.getScriptProperties().getProperty("DISCORD_WEBHOOK_LOCAL");
    if (!webhook && !dryRun) throw new Error("スクリプト プロパティ DISCORD_WEBHOOK_LOCAL が未設定です");

    // 既存記録が読めない回は送らない（重複を避けるため。次回に回す）
    var known = fetchRemoteKeys_();
    var own = loadOwnRecord_();
    Object.keys(own).forEach(function (k) { known[k] = true; }); // GAS自身の記録（mastodon:補完キーも含む）

    var posts = fetchPosts_();
    var send = selectToSend_(posts, known, nowMs, CONFIG.GRACE_MINUTES * 60000, CONFIG.MAX_SEND_PER_RUN);
    console.log("[INFO] 取得" + posts.length + "件 / 送信候補" + send.length + "件" + (dryRun ? "（試運転・送信なし）" : ""));
    send.forEach(function (it) {
      console.log("  - " + it.datetime + " [" + it.city + "] " + it.summary + " " + (it.url || ""));
    });
    if (dryRun || send.length === 0) return;

    if (!sendToDiscord_(webhook, buildEmbed_(send, nowMs))) return;
    var stamp = new Date(nowMs).toISOString();
    send.forEach(function (it) { own[it.key] = stamp; });
    saveOwnRecord_(own, nowMs);
    console.log("[INFO] " + send.length + "件を送信し、記録しました");
  } catch (err) {
    console.log("[ERROR] " + err.message + "（送信・記録は行っていません）");
  } finally {
    lock.releaseLock();
  }
}

/** 30分おきの時間トリガーを登録する（既存の同名トリガーは消してから作る）。 */
function installTrigger30Min() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === "runAnznFallback") ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger("runAnznFallback").timeBased().everyMinutes(30).create();
  console.log("[INFO] 30分おきのトリガーを登録しました");
}

// ---------- ローカルテスト用（GASでは無視される） ----------
if (typeof module !== "undefined") {
  module.exports = {
    CONFIG: CONFIG, anznKeyFromUrl_: anznKeyFromUrl_, parsePost_: parsePost_,
    selectToSend_: selectToSend_, buildEmbed_: buildEmbed_, cmpId_: cmpId_
  };
}
