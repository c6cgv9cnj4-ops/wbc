// 実行: node gas/anzn_fallback.test.js （GASの外で、純粋ロジックだけを検証。通信なし）
const assert = require("assert");
const m = require("./anzn_fallback.gs");

const KITA = { id: "117646000000000001", created_at: "2026-11-20T01:00:00.000Z",
  content: "<p># 北本市 災害発生情報 11月20日 10:30 北本市本町1丁目地内で火災が発生しました。<br><a href=\"https://anzn.net/sp/?11217F&amp;i=231\">anzn.net/sp/?11217F&amp;i=231</a></p>" };
const OTHER_CITY = { id: "117646000000000002", created_at: "2026-11-20T01:00:00.000Z",
  content: "<p># 越谷市 災害発生情報 11月20日 10:30 越谷市地内 anzn.net/sp/?11217F&amp;i=999</p>" };
const CRIME = { id: "117646000000000003", created_at: "2026-11-20T01:00:00.000Z",
  content: "<p># 北本市 犯罪情報官NEWS(不審者) 11月20日 10:30 北本市地内 anzn.net/sp/?11217F&amp;i=998</p>" };

let passed = 0;
function t(name, fn) { fn(); passed++; console.log("ok -", name); }

t("記事URLから共通キーを作る（&amp; と & の両方）", () => {
  assert.strictEqual(m.anznKeyFromUrl_("https://anzn.net/sp/?11217F&amp;i=231"), "anzn:11217F:231");
  assert.strictEqual(m.anznKeyFromUrl_("https://anzn.net/sp/?11217F&i=231&gist=北"), "anzn:11217F:231");
  assert.strictEqual(m.anznKeyFromUrl_("https://example.com/"), null);
});

t("北本市の災害発生情報を解析する（キー・市・概要・URL）", () => {
  const it = m.parsePost_(KITA);
  assert.strictEqual(it.key, "anzn:11217F:231");
  assert.strictEqual(it.city, "北本市");
  assert.strictEqual(it.datetime, "11-20 10:30頃");
  assert.strictEqual(it.url, "https://anzn.net/sp/?11217F&i=231");
});

t("対象外（他市・防犯情報）は送信対象にしない", () => {
  assert.strictEqual(m.parsePost_(OTHER_CITY), null);
  assert.strictEqual(m.parsePost_(CRIME), null);
});

t("投稿IDは桁数→辞書順で古い順に並ぶ", () => {
  const ids = ["999", "1000", "20", "100"];
  ids.sort(m.cmpId_);
  assert.deepStrictEqual(ids, ["20", "100", "999", "1000"]);
});

t("猶予30分未満の投稿は送らず、既送信は除外する", () => {
  const now = Date.parse("2026-11-20T02:00:00.000Z");
  const recent = Object.assign({}, KITA, { id: "117646000000000010", created_at: "2026-11-20T01:45:00.000Z",
    content: KITA.content.replace("i=231", "i=232") });   // 15分前 → 猶予中
  const old = Object.assign({}, KITA, { id: "117646000000000005", created_at: "2026-11-20T01:00:00.000Z" }); // 60分前
  const sent = m.selectToSend_([recent, old], {}, now, 30 * 60000, 25);
  assert.deepStrictEqual(sent.map(x => x.key), ["anzn:11217F:231"]);

  const known = { "anzn:11217F:231": true };
  assert.deepStrictEqual(m.selectToSend_([old], known, now, 30 * 60000, 25), []);
});

t("同じ記事キーは1件にまとめ、上限を超えない", () => {
  const now = Date.parse("2026-11-20T03:00:00.000Z");
  const dup = Object.assign({}, KITA, { id: "117646000000000021" }); // 同じ記事（同じキー）
  const other = Object.assign({}, KITA, { id: "117646000000000022", content: KITA.content.replace("i=231", "i=233") });
  const sent = m.selectToSend_([KITA, dup, other], {}, now, 30 * 60000, 25);
  assert.deepStrictEqual(sent.map(x => x.key), ["anzn:11217F:231", "anzn:11217F:233"]);
  assert.strictEqual(m.selectToSend_([KITA, other], {}, now, 30 * 60000, 1).length, 1);
});

console.log(`\n${passed} 件 OK`);
