/* 见证链看板 · 「锚定日志」判据（2026-09-23 建 · 与 scripts/check_witness_health.py::anchor_verdict 同源同样本）
 *
 * 为什么单独成文件：这条判据此前在看板 HTML 里内联、在 CI 里另写一遍，两处分叉——
 * 09-03 看板改读聚合，CI 没跟着改，于是 CI 连报 8 天假绿。
 * 现在：本文件被生成器**内联**进看板（页面仍是单文件、零外链），
 *       同时被 scripts/test_witness_fixtures.js 用 node 拿 scripts/witness_fixtures.json 逐条验。
 * 🔑 改判据的顺序：先改 witness_fixtures.json（加一条负向样本），再同时改这里和 python，两边都过才算改完。
 * 🚫 本文件不许出现 script 的闭合标签（要被内联进 HTML；生成器有断言——初版这行注释自己就写了那个标签，被断言当场拦下）。
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.witnessVerdict = factory();
})(typeof self !== "undefined" ? self : this, function () {
  function anchorVerdict(rec, age, slaAnchor) {
    if (age === null || age === undefined)
      return { status: "unknown", detail: "anchor_log 最后一条无法解析日期", names: [] };
    if (age > slaAnchor)
      return { status: "bad", detail: `锚定日志最后一条 ${rec.date}（${age} 天前）`, names: [] };
    const res = rec.results || [];
    const nOk = rec.within_sla ?? null, nOut = rec.out_of_sla ?? null, nUnk = rec.not_probed ?? null;
    if (nOut === null && nUnk === null)   // 旧格式无聚合字段 ⇒ 没测到，不是没问题
      return { status: "unknown", detail: `${rec.date} 这条记录没有聚合字段，无法判定全部探针`, names: [] };
    // 分类三分支逐字照抄生产端 anchor_wayback.py：probe unknown ⇒ 未测到；否则 within_sla 假 ⇒ 超期（含 none）
    const isUnk = r => r.probe === "unknown";
    const isStale = r => !isUnk(r) && !r.within_sla;
    // ① 矛盾检测：明细重算 vs 顶层聚合，不一致本身就是红，不管谁对
    if (res.length) {
      const rUnk = res.filter(isUnk).length, rOut = res.filter(isStale).length, rOk = res.length - rUnk - rOut;
      if (rOk !== (nOk || 0) || rOut !== (nOut || 0) || rUnk !== (nUnk || 0))
        return { status: "bad", names: [],
          detail: `${rec.date} 记录自相矛盾：顶层聚合 SLA内${nOk}/超期${nOut}/未测${nUnk}，按明细重算 ${rOk}/${rOut}/${rUnk} —— 生产端或判据漂移，先查谁改了` };
    }
    const nm = r => {
      let s = (r.url || "?").replace("https://", "").slice(0, 52);
      if (r.spn_status === "success" && r.spn_timestamp) s += `（当轮已存 ${r.spn_timestamp}·等 IA 索引，非漏存）`;
      return s;
    };
    const saveNote = () => {
      const m = rec.save_mode, ok = rec.save_ok, t = rec.save_tried;
      if (m === undefined || m === null) return "";
      if (ok === 0 && t) return `（⚠️ 但本轮 ${t} 次存档提交一个都没成功·模式 ${m}${m === "anon" ? "：匿名提交已失效，快照全靠 IA 爬虫运气，去配 IA 密钥" : ""}）`;
      return `（本轮提交 ${ok}/${t} 成功·模式 ${m}）`;
    };
    if ((nOut || 0) > 0) {
      const names = res.filter(isStale).map(nm);
      return { status: "bad", names,
        detail: `${rec.date} 锚定：${nOut} 个存档超期（SLA 内 ${nOk} · 未测到 ${nUnk}）—— 超期的是：${names.join("、") || "（记录里没有逐条明细）"}` };
    }
    if ((nUnk || 0) > 0) {
      const names = res.filter(isUnk).map(nm);
      return { status: "unknown", names,
        detail: `${rec.date} 锚定：${nUnk} 个未能查证（IA 限流）· SLA 内 ${nOk} —— 未测到的是：${names.join("、") || "（记录里没有逐条明细）"}` };
    }
    // 2026-09-25：计数全 0／无明细原先落到 ok（「锚了零个」也是绿）；与 check_witness_health.py 同改
    if (!(nOk || 0)) {
      return { status: "unknown", names: [],
        detail: `${rec.date} 锚定记录里一个在 SLA 内的存档都没有（计数全 0 或无明细）—— 判不了，不算正常` };
    }
    return { status: "ok", names: [], detail: `${rec.date} 锚定正常，${nOk} 个存档全部在 SLA 内` + saveNote() };
  }
  return { anchorVerdict };
});
