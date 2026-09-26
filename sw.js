/* 恐惧的标价 — Service Worker
 * 策略（为"每天盘后更新"的静态站设计）：
 *  - data/*.json：网络优先（每日数据永远新鲜），断网回退缓存
 *  - 页面导航与其余同源静态资源：缓存优先 + 后台刷新（秒开外壳；改动下次启动生效。
 *    页面外壳只在部署时变、且资源带 ?v= 版本号，旧壳引用旧资源自洽，不会错配）
 *  - 静态 CDN（jsdelivr 的 ECharts/opencc、Google Fonts）：同样缓存优先 + 后台刷新——
 *    这些在部分网络极慢/不可达，是冷启动白屏主因；opaque 响应也落缓存
 *  - 其余跨域（parqet 直连兜底）：不拦截，交给浏览器（TradingView iframe 已于 2026-07-26 §45 撤下）
 * 改缓存策略时 bump CACHE 版本号即可让旧缓存整体作废。
 * 🚨 两条硬规矩（2026-07-26）：
 *  1) 改价 / 改 Paddle priceId 必须同次 bump CACHE —— 外壳是缓存优先，回访者拿到的是上一次
 *     访问时的旧壳，旧壳内联的是旧 priceId：不 bump 就是放任回访者按旧价结账（账实不符）。
 *  2) 验收站上改动必须硬刷新或无痕窗口 —— 回访看到的外壳永远滞后一次部署，
 *     正常刷新看到旧版不是部署失败（07-26 已实际骗过一次验收）。 */
// 2026-08-03 → mc-v6：定价三档等宽修复。不属于上面 §11 的「改价必 bump」，是主动 bump——
// 外壳缓存优先，不 bump 的话回访者还要再看一次错版（Klay 是当 bug 报的，不能等下一次访问）。
// 代价只有一次冷加载。
// 2026-09-26 → mc-v32：Pro 年付 $590 接 Paddle（新 priceId），按硬规矩 1 同次 bump；
// 09-25 的定价改版（0d590478）漏了这一步，回访者多看了一次旧定价，本次一并作废。
// 2026-09-26 → mc-v33：Pro 权益改为只写公共领域部分（售卖页承诺变了，回访者不能再看一次旧承诺）。
// 2026-09-26 → mc-v34：定价三卡加宽、间距 28px、不再单字换行（Klay 当 bug 报的，同 mc-v6 先例主动 bump）。
// 2026-09-26 → mc-v35：定价页整页内容栏放宽到 1040 与卡片对齐，Pro 副标删「自己用这把尺子」。
const CACHE = "mc-v36";
const CDN_HOSTS = new Set(["cdn.jsdelivr.net", "fonts.googleapis.com", "fonts.gstatic.com"]);

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) {
    if (CDN_HOSTS.has(url.hostname)) e.respondWith(staleWhileRevalidate(req));
    return; // 其余跨域不碰
  }

  const networkFirst = url.pathname.includes("/data/");
  e.respondWith(networkFirst ? fromNetwork(req) : staleWhileRevalidate(req));
});

// no-cors 拿到的跨域脚本/字体是 opaque（status 0，res.ok=false）但内容有效，同样落缓存
const cacheable = (res) => res && (res.ok || res.type === "opaque");

async function fromNetwork(req) {
  const cache = await caches.open(CACHE);
  try {
    const res = await fetch(req);
    if (cacheable(res)) cache.put(req, res.clone());
    return res;
  } catch {
    const hit = await cache.match(req);
    if (hit) return hit;
    throw new Error("offline & uncached: " + req.url);
  }
}

async function staleWhileRevalidate(req) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(req);
  const refresh = fetch(req)
    .then((res) => { if (cacheable(res)) cache.put(req, res.clone()); return res; })
    .catch(() => hit);
  return hit || refresh;
}
