// 看板判据 vs 共享样本（零网络）。CI 每天先跑它；与 python --selftest 读同一份 witness_fixtures.json。
const fs = require("fs"), path = require("path");
const { anchorVerdict } = require("./witness_verdict.js");
const fx = JSON.parse(fs.readFileSync(path.join(__dirname, "witness_fixtures.json"), "utf8"));
const SLA_ANCHOR = 4;   // 与看板 SLA.anchor、python SLA["anchor_log"] 同值
let bad = 0;
for (const c of fx.cases) {
  const r = anchorVerdict(c.rec, c.age, SLA_ANCHOR);
  let ok = r.status === c.expect;
  if (ok && c.expect_named !== undefined) ok = r.names.length === c.expect_named;
  console.log((ok ? "  ✅ " : "  ❌ ") + c.name + (ok ? "" : `\n       实得 ${r.status} · 点名 ${r.names.length} · ${r.detail.slice(0, 140)}`));
  if (!ok) bad++;
}
console.log(`\n看板判据自检（node）：${fx.cases.length - bad} 过 / ${bad} 败`);
process.exit(bad ? 1 : 0);
