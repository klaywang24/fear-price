/* 恐惧的标价 — 前端逻辑：tab 切换、日夜主题、ECharts 渲染 */
(function () {
  "use strict";

  // ---------------- 主题 ----------------
  const rootEl = document.documentElement;
  const toggleBtn = document.getElementById("theme-toggle");

  function isDark() { return rootEl.classList.contains("dark-mode"); }
  function syncToggleIcon() { toggleBtn.textContent = isDark() ? "☾" : "☀"; }

  toggleBtn.addEventListener("click", () => {
    rootEl.classList.toggle("dark-mode");
    localStorage.setItem("mc-theme", isDark() ? "dark" : "light");
    syncToggleIcon();
    rebuildAll();
  });
  syncToggleIcon();

  function pal() {
    const s = getComputedStyle(rootEl);
    const v = (name) => s.getPropertyValue(name).trim();
    return {
      ink: v("--ink"), inkSoft: v("--ink-soft"), muted: v("--ink-muted"),
      accent: v("--accent"), accentDeep: v("--accent-deep"),
      gold: v("--gold"), moss: v("--moss"), blue: v("--blue"),
      teal: v("--teal"), purple: v("--purple"), danger: v("--danger"),
      cmpRed: v("--cmp-red"), cmpBlue: v("--cmp-blue"),
      cmpPurple: v("--cmp-purple"), cmpGreen: v("--cmp-green"),
      grid: v("--grid"), gridStrong: v("--grid-strong"),
      card: v("--bg-card"), border: v("--border"),
    };
  }

  // ---------------- 数据 ----------------
  const cache = {};
  async function load(name) {
    if (!cache[name]) {
      cache[name] = fetch("data/" + name + ".json").then((r) => {
        if (!r.ok) throw new Error(name + " " + r.status);
        return r.json();
      });
    }
    return cache[name];
  }

  // ---------------- 图表注册表 ----------------
  // panel -> [{el, build}]；懒加载：首次进入 tab 才渲染
  const registry = { pulse: [], kindex: [], spy: [], qqq: [], tech: [], fin: [], consumer: [], luxury: [], macro: [], leaps: [],
    // 页脚静态页（无图表，空数组即可，让路由识别并显示对应 panel）
    about: [], contact: [], privacy: [], terms: [], refunds: [], pricing: [], methodology: [] };
  const built = new Map(); // el id -> echarts instance

  function chart(panel, elId, build) {
    registry[panel].push({ elId, build });
  }

  // ECharts option 内的中文（series 名/轴名/图例/标注）按当前语言翻译
  function i18nOption(o) {
    if (!window.MC_I18N || MC_I18N.lang() === "zh" || o == null) return o;
    // ⚠️ 数组元素若是字符串，必须在这里翻译：不能只 forEach(i18nOption)，
    // 因为 i18nOption 对字符串是空操作（它只处理对象/数组），于是「类目轴的
    // data 数组」「legend 的 data 数组」这类纯字符串数组永远不会被翻译。
    // 2026-07-18 由波动率家族横条图暴露：EN 下 y 轴仍是「高盛/苹果/谷歌/亚马逊」，
    // 而同图的 xAxis.name 与 markLine.formatter（对象上的字符串属性）却翻对了。
    // 这是通用漏洞，不限于该图；修在此处，所有类目轴图表一并受益。
    if (Array.isArray(o)) {
      o.forEach((v, i) => {
        if (typeof v === "string" && /[一-鿿]/.test(v)) o[i] = MC_I18N.translate(v);
        else i18nOption(v);
      });
      return o;
    }
    if (typeof o === "object") {
      for (const k of Object.keys(o)) {
        const v = o[k];
        if (typeof v === "string" && /[一-鿿]/.test(v)) o[k] = MC_I18N.translate(v);
        else if (v && typeof v === "object") i18nOption(v);
      }
    }
    return o;
  }

  /* 🔴 2026-08-02 修：图表内容画到绘图区外面（Klay 截图点名，恐惧的标价台账那张
     前向段整条 15 个点全在 grid 之外，末点溢出 219px；杠杆基金净头寸等多张同病）。

     **不是样式问题，是 ECharts 记的容器宽度是旧的。** 实测：`inst.getWidth()` = 1086，
     而 `el.clientWidth` = 867 —— 坐标系按 1086 算，画出来自然冲出右边界 219px（1086/867 ≈ 1.25）。

     站上本来就有两处 resize（切面板后 app.js:200、window.resize app.js:254），为什么不管用：
     两处都是**在某个时刻主动调一次**，而容器宽度是在那之后才稳定下来的（tab 从 display:none
     切出来、字体加载、滚动揭示的过渡、侧栏/目录出现都会改宽度）。**时点式的 resize 治不了
     时点之后发生的布局变化。**

     正解是让它**由尺寸变化本身驱动**：给每个图表容器挂 ResizeObserver，宽度一变就重算。
     两个护栏：① `clientWidth > 0` —— 面板 display:none 时宽度为 0，此时 resize 会把图表
     压成 0 宽，切回来就是空白；② 差值 > 1px 才动 —— 否则 resize 引起的重绘可能反过来
     再触发观察者，形成循环。 */
  const sizeObs = new Map();      // elId -> ResizeObserver

  function observeSize(elId, el) {
    if (!window.ResizeObserver) return;      // 老浏览器退回原有的两处 resize
    if (sizeObs.has(elId)) { sizeObs.get(elId).disconnect(); }
    let raf = 0;
    const obs = new ResizeObserver(() => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const c = built.get(elId);
        if (!c || c.isDisposed()) return;
        if (el.clientWidth > 0 && Math.abs(c.getWidth() - el.clientWidth) > 1) c.resize();
      });
    });
    obs.observe(el);
    sizeObs.set(elId, obs);
  }

  /* 兜底：页面从后台切回前台时，把所有图表的尺寸校一遍。
     Why：`requestAnimationFrame` 在页面隐藏时被浏览器整体暂停（实测 `visibilityState:"hidden"`
     下 rAF 与 ResizeObserver 回调都不执行）。如果用户在别的标签页时这边完成了布局变化，
     观察者的那次回调可能排在队列里没跑完；切回来时尺寸已经不再变化，观察者也就不会再响。
     这一条与 ResizeObserver 是两层保险，谁先到算谁的（差值 ≤1px 时两者都不会白做功）。 */
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    built.forEach((c, id) => {
      const el = document.getElementById(id);
      if (el && el.clientWidth > 0 && !c.isDisposed() && Math.abs(c.getWidth() - el.clientWidth) > 1) c.resize();
    });
  });

  async function buildOne(elId, build) {
    const el = document.getElementById(elId);
    if (!el) return;
    if (built.has(elId)) { built.get(elId).dispose(); }
    if (sizeObs.has(elId)) { sizeObs.get(elId).disconnect(); sizeObs.delete(elId); }
    try {
      const option = i18nOption(await build(pal()));
      const inst = echarts.init(el, null, { renderer: "canvas" });
      built.set(elId, inst);
      inst.setOption(option);
      observeSize(elId, el);
    } catch (e) {
      // 数据尚未生成/拉取失败：给出可见占位，而不是无声空白
      built.delete(elId);
      el.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--ink-muted);font-size:13px">数据更新中，稍后自动出现 · data updating</div>';
    }
  }

  /* ---------------- 滚动揭示（2026-07-19） ----------------
     🚨 三条安全约束，改这段前先读：
     1) 内容默认可见（CSS 侧不给初始 opacity:0），只有这里确认 IO 可用后才加
        <html>.rv-on。任何一环失败 → 没动画，而不是没内容。
     2) 站上是 tab 路由，面板 display:none 时元素**零尺寸**，IO 不会把它们判为进入视口。
        所以每次面板构建完必须重扫；且**读数卡（.stat 等）是 JS 动态生成的**，
        一次性扫 DOM 会全部漏掉：用户点名要效果的那四处正好都是这种。
     3) 一律交给 IO，不做「首屏直接就位」的捷径：见 rvScan 内注释（依赖布局已稳定，
        而扫描时图表尚未渲染，该假设不成立）。 */
  const RV_SEL = ".card, .stat, .senti-card, .ledger-card, .lg-ladder, .chapter-head";
  const rvSeen = new WeakSet();
  let rvArmed = false;

  /* 点亮当前视口内（含下方一点余量）尚未点亮的元素。
     ⚠️ 这里**不用 IntersectionObserver**。用过，踩了两个坑：
       ① 隐藏面板里的元素 rect 全为 0，而 IO 会把「零面积且坐标在视口内」判为已进入
          → 一观察就全亮；
       ② 换成只观察非零尺寸元素后，IO 在本页结构下干脆不触发，实测视口内 3 张卡长期
          停在 opacity:0 ： 正是最不能接受的「内容看不见」。
     39 个元素逐个算 rect 的开销可以忽略，换来的是**行为完全可预测**。 */
  function rvTick() {
    if (!rvArmed) return;
    const h = innerHeight;
    document.querySelectorAll(".rv:not(.rv-in)").forEach((n) => {
      const r = n.getBoundingClientRect();
      if (r.bottom > 0 && r.top < h * 0.94) n.classList.add("rv-in");
    });
  }

  function rvInit() {
    if (rvArmed) return;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    rvArmed = true;
    document.documentElement.classList.add("rv-on");
    /* ⚠️ 节流**不要用 requestAnimationFrame 做闸**。
       第一版写的是 `if (raf) return; raf = requestAnimationFrame(...)`，
       在不触发 rAF 的渲染上下文里 raf 永远保持真值 → 之后每一次滚动都被吞掉，
       实测滚了 10800px 一张卡都没再点亮。（同一个坑昨夜在预览页的数字滚动上踩过一次。）
       改用时间戳节流，不依赖任何回调是否被调度。 */
    let last = 0;
    const onScroll = () => {
      const now = Date.now();
      if (now - last < 80) return;
      last = now;
      rvScan();   // 顺带补扫：切面板/异步渲染出来的新卡片在这里也能接住
      rvTick();
    };
    addEventListener("scroll", onScroll, { passive: true });
    addEventListener("resize", onScroll, { passive: true });
    /* 兜底：绝不让内容因动画机制卡住而消失。
       ⚠️ 第一版是「3 秒后无差别点亮全部」：那等于让揭示效果自己失效，
       而且把我自己的验收也污染了：每次测到的都是兜底后的「全亮」，
       连续三轮误判为「揭示没工作」，其实那三轮它一直是好的。 */
    setTimeout(rvTick, 1200);
    setTimeout(rvTick, 2600);

    /* 卡片几乎全是**异步**渲染的（render* 先 fetch JSON 再插 DOM），比 activatePanel 晚得多，
       所以不能只在面板激活时扫一次：实测那时扫到 0 个。
       ⚠️ 也不要用「每 150ms 轮询面板 scrollHeight 判断布局是否稳定」那套：
       读 scrollHeight 会**强制重排**，在这种上万像素、十几张图的页面上开销可观，
       实测把浏览器拖到多次操作超时。
       ∴ 用固定几个时点补扫（已扫过的靠 rvSeen 跳过，重复调用几乎零成本）。
       扫描不能太早：太早时面板还塌缩着，整屏卡片都会落在首屏而被一次点亮。 */
    [900, 1800, 3200].forEach((ms) => setTimeout(() => { rvScan(); rvTick(); }, ms));
  }

  function rvScan(scope) {
    if (!rvArmed) return;
    const root = scope || document;
    root.querySelectorAll(RV_SEL).forEach((n) => {
      if (rvSeen.has(n) || n.closest(".pulse-base")) return;  // pulse 头部已有 heroRise，不叠
      const r = n.getBoundingClientRect();
      /* 🚨 隐藏面板（display:none）里的元素 rect 全为 0。而 IntersectionObserver 会把
         「零面积、坐标落在视口内」的目标判为 isIntersecting=true，一观察就立刻点亮：
         实测因此 174 个元素在 1.4s 内全亮，滚动揭示形同虚设。
         ∴ 零尺寸的**跳过且不记入 rvSeen**，等它所在面板显示后（activatePanel 会再扫）
         再处理。这也顺带保证了不会给看不见的东西挂一堆观察器。 */
      if (r.width === 0 && r.height === 0) return;
      rvSeen.add(n);
      n.classList.add("rv");
    });
  }

  // 收进「百年档案」下拉的七个面板（2026-07-25 导航精简）。与 index.html #archive-menu 保持一致。
  // 2026-08-31（§71）：删掉 "macro"。它 08-20 已从本下拉提到顶层成为独立 tab，
  // 但这个集合没跟着改 ⇒ 停在宏观页时「宏观」和「百年档案」**同时**带下划线（Klay 截图）。
  // 🔑 同族第 N 例：改导航结构时，"谁属于哪一组"的判定表是第二处实现，不会自己跟着改。
  const ARCHIVE_PANELS = new Set(["spy", "qqq", "tech", "fin", "consumer", "luxury"]);

  const panelDone = new Set();
  async function activatePanel(name) {
    const target = document.getElementById("panel-" + name);
    // §63 瘦身路由页只带自己的 panel：目标不在 DOM（popstate 等旁路进来）→ 整页导航
    if (!target) { location.href = urlFor(name, ""); return; }
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    target.classList.add("active");
    document.querySelectorAll(".tab").forEach((t) =>
      t.classList.toggle("active", t.dataset.panel === name));
    // 档案下拉：组内任一面板激活时，触发器一并高亮（否则进了档案页顶栏看不出身在何处）
    const archTrigEl = document.getElementById("archive-trigger");
    if (archTrigEl) archTrigEl.classList.toggle("active", ARCHIVE_PANELS.has(name));
    if (!panelDone.has(name)) {
      panelDone.add(name);
      for (const { elId, build } of registry[name]) await buildOne(elId, build);
    }
    // 切换显示后需要 resize（display:none 时初始化的尺寸不对）
    registry[name].forEach(({ elId }) => built.get(elId) && built.get(elId).resize());
    rvInit();
    // 切面板后补扫：延迟到图表画完再扫，否则面板还塌缩着，整屏卡片会被一次点亮
    [700, 1600].forEach((ms) => setTimeout(() => { rvScan(); rvTick(); }, ms));
  }

  async function rebuildAll() {
    for (const name of panelDone) {
      for (const { elId, build } of registry[name]) await buildOne(elId, build);
    }
    if (currentStock) showStock(currentStock.basket, currentStock.safe);
  }

  // §63：tab 从 <button> 换成 <a href> 后，顶栏点击统一走下方的全站链接委托
  // （一个机制管所有站内链接，不再单设通道）。此处不再绑定。

  // ---------------- 百年档案下拉（2026-07-25 导航精简 10 → 4） ----------------
  // 七个静态档案面板收进下拉；选中态与开合状态分开管理：
  // .active 由 activatePanel 打（表示"你在这组里"），aria-expanded 只表示"菜单开着"。
  {
    const grp = document.getElementById("archive-group");
    const trig = document.getElementById("archive-trigger");
    const menu = document.getElementById("archive-menu");
    if (grp && trig && menu) {
      // 顶栏在窄屏会换行，触发器可能落在任意位置：打开时把菜单横向夹进视口，
      // 否则纯 CSS 定位会在某些排布下把菜单推出屏幕（实测 390px 换行后 right:0 → left:-47）。
      const place = () => {
        const g = trig.getBoundingClientRect();          // 菜单是 fixed：坐标直接用视口系
        menu.style.top = Math.round(g.bottom + 6) + "px";
        const w = menu.offsetWidth || 160, pad = 8;
        let left = g.left;
        if (left + w > window.innerWidth - pad) left = window.innerWidth - pad - w;  // 右侧越界回拉
        if (left < pad) left = pad;                                                   // 左侧越界推回
        menu.style.left = Math.round(left) + "px";
      };
      const setOpen = (open) => {
        menu.hidden = !open;
        trig.setAttribute("aria-expanded", String(open));
        // ≤820px 的 .tabs 带 mask-image 渐隐，会把这个 fixed 菜单一起裁掉；开着时摘遮罩（style.css .tabs.menu-open）
        const tabsEl = document.getElementById("tabs");
        if (tabsEl) tabsEl.classList.toggle("menu-open", open);
        if (open) place();
      };
      trig.addEventListener("click", (e) => { e.stopPropagation(); setOpen(menu.hidden); });
      // 选完即关：点的是 .tab，hash 变化由 #tabs 的委托处理，这里只负责收起菜单
      menu.addEventListener("click", () => setOpen(false));
      document.addEventListener("click", (e) => { if (!grp.contains(e.target)) setOpen(false); });
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && !menu.hidden) { setOpen(false); trig.focus(); }
      });
      // fixed 定位不跟随滚动，滚动时收起，免得菜单僵在半空
      window.addEventListener("scroll", () => { if (!menu.hidden) setOpen(false); }, { passive: true });
      window.addEventListener("resize", () => { if (!menu.hidden) place(); });
    }
  }
  window.addEventListener("resize", () => built.forEach((c) => c.resize()));

  // ---------------- 篮子板块配置（与 build_data.py 的 BASKETS 保持一致） ----------------
  const BASKET_CFG = {
    tech: {
      anchorLabel: "科技总览",   // 2026-07-16：原「QQQ 总览」名实不符：TSM 挂 NYSE，进不了纳斯达克 100，用站上自己那张 103 只成分股表一对就穿帮。这一栏的用途是「我盯的这些票」不是复刻指数，故改标题、留台积电（AI 硬件链最要紧的一环）。
      // ⚠️ 与 scripts/build_data.py 的 BASKETS["tech"] 必须同步；分组标签保持 ≤5 字
      //（.pill-group:only-child .pill-group-label 的 min-width:5.4em 按 5 字量的，超了就不对齐了）
      rows: [
        [["半导体", ["NVDA", "AVGO", "TSM", "AMD"]]],
        [["存储", ["MU", "SNDK"]]],
        [["平台·软件", ["MSFT", "GOOGL", "META", "AMZN"]]],
        [["硬件·终端", ["AAPL", "TSLA"]]],
      ],
      members: [["NVDA", "英伟达"], ["AVGO", "博通"], ["TSM", "台积电"], ["AMD", "AMD"],
                ["MU", "美光"], ["SNDK", "闪迪"],
                ["MSFT", "微软"], ["GOOGL", "谷歌"], ["META", "Meta"], ["AMZN", "亚马逊"],
                ["AAPL", "苹果"], ["TSLA", "特斯拉"]],
    },
    fin: {
      anchorLabel: "XLF 总览",
      // rows：两行分组布局（左侧锚胶囊纵跨两行）；members 顺序 = 上一只/下一只的翻页顺序
      rows: [
        [["银行", ["JPM", "BAC"]], ["卡组织", ["V", "MA", "AXP"]]],
        [["券商", ["SCHW", "IBKR"]], ["投行", ["GS", "MS"]]],
        [["资管", ["BLK"]], ["保险", ["BRK.B"]]],
        [["加密·稳定币", ["COIN", "HOOD", "CRCL"]]],
      ],
      members: [["JPM", "摩根大通"], ["BAC", "美国银行"], ["V", "Visa"], ["MA", "万事达"],
                ["AXP", "美国运通"], ["SCHW", "嘉信理财"], ["IBKR", "盈透证券"],
                ["GS", "高盛"], ["MS", "摩根士丹利"], ["BLK", "贝莱德"], ["BRK.B", "伯克希尔"],
                ["COIN", "Coinbase"], ["HOOD", "Robinhood"], ["CRCL", "Circle"]],
    },
    consumer: {
      anchorLabel: "XLP·XLY 总览",
      rows: [
        [["必需", ["KO", "WMT", "COST"]]],
        [["可选", ["HD", "TJX", "MCD"]]],
      ],
      members: [["KO", "可口可乐"], ["WMT", "沃尔玛"], ["COST", "好市多"],
                ["HD", "家得宝"], ["TJX", "TJX"], ["MCD", "麦当劳"]],
    },
    luxury: {
      anchorLabel: "组合总览",
      members: [["MC.PA", "LVMH"], ["RMS.PA", "爱马仕"], ["RACE", "法拉利"]],
    },
  };
  const safeTicker = (t) => t.toLowerCase().replace(".", "-");

  // 标点也得跟着语言走：全角括号/顿号/全角空格只属于中文，英文态必须半角。
  // 2026-07-26 四层扫描（执行 formatter 看输出那一层）抓到 EN 下真渲染出「1（20%）」
  // 与全角空格 —— 这类字符藏在 formatter 函数体和 textContent 的拼接胶水里，
  // i18nOption 只翻译字符串值、D 字典也扫不进函数体，所以只能在这里按语言选。
  const enPunct = () => !!(window.MC_I18N && MC_I18N.lang && MC_I18N.lang() === "en");
  const paren = (s) => (enPunct() ? ` (${s})` : `（${s}）`);
  const joinList = (a) => a.join(enPunct() ? ", " : "、");
  const wideGap = () => (enPunct() ? "  " : "　");

  // logo 三级加载链：自托管 logos/（scripts/fetch_logos.py 每周同步，与站点同源、
  // 不受客户端拦截影响：iPad 曾整站拦掉 parqet 直连）→ parqet 直连（新 ticker 兜底）→ 首字母圆章
  window.__logoErr = function (img) {
    const t = img.dataset.t || "";
    if (!img.dataset.step) {
      img.dataset.step = "1";
      img.src = "https://assets.parqet.com/logos/symbol/" + encodeURIComponent(t) + "?format=png&size=64";
    } else {
      const s = document.createElement("span");
      s.className = img.className + " logo-mono";
      s.textContent = (t || "?").charAt(0);
      img.replaceWith(s);
    }
  };

  let currentStock = null; // {basket, safe}

  function renderSubnav(basket) {
    const cfg = BASKET_CFG[basket];
    const cur = currentStock && currentStock.basket === basket ? currentStock.safe : null;
    const nameOf = Object.fromEntries(cfg.members);
    const pill = (t) => {
      const s = safeTicker(t);
      const code = t.split(".")[0];
      // 中文名与代码相同时（AMD / TJX 这类没有通行中文名的）不重复渲染，否则胶囊显示成「AMD AMD」
      const zh = nameOf[t] === code ? "" : ` <span class="zh">${nameOf[t]}</span>`;
      // 全站链接一律写相对路径（不带前导斜杠）：自有域名与 github.io 子目录镜像都能用
      return `<a class="pill ${cur === s ? "active" : ""}" href="${basket}?s=${s}">${code}${zh}</a>`;
    };
    const group = ([label, ticks]) =>
      `<span class="pill-group"><span class="pill-group-label">${label}</span>${ticks.map(pill).join("")}</span>`;
    let html;
    if (cfg.rows) {
      html = `<a class="pill pill-anchor ${cur ? "" : "active"}" href="${basket}"><span class="zh">${cfg.anchorLabel}</span></a>` +
        `<div class="pill-rows">` +
        cfg.rows.map((row) => `<div class="pill-row">${row.map(group).join("")}</div>`).join("") +
        `</div>`;
    } else {
      const anchor = `<a class="pill ${cur ? "" : "active"}" href="${basket}"><span class="zh">${cfg.anchorLabel}</span></a>`;
      html = anchor + (cfg.groups ? cfg.groups.map(group).join("") : cfg.members.map(([t]) => pill(t)).join(""));
    }
    document.getElementById("subnav-" + basket).innerHTML = html;
  }

  function showOverview(basket) {
    currentStock = null;
    document.getElementById(basket + "-overview").style.display = "";
    document.getElementById(basket + "-stock").style.display = "none";
    renderSubnav(basket);
    registry[basket].forEach(({ elId }) => built.get(elId) && built.get(elId).resize());
  }

  async function showStock(basket, safe) {
    const cfg = BASKET_CFG[basket];
    const idx = cfg.members.findIndex(([t]) => safeTicker(t) === safe);
    // 查无此股（成分股换过、或有人手打了 ?s=）：退回总览，并把地址栏一起收干净，
    // 免得留下一个打不开任何东西的 URL 被转发出去
    if (idx < 0) { history.replaceState({}, "", urlFor(basket, "")); return showOverview(basket); }
    const [ticker, name] = cfg.members[idx];
    currentStock = { basket, safe };
    renderSubnav(basket);
    document.getElementById(basket + "-overview").style.display = "none";
    const host = document.getElementById(basket + "-stock");
    host.style.display = "";
    const p = "s_" + safe;
    const prev = cfg.members[(idx + cfg.members.length - 1) % cfg.members.length];
    const next = cfg.members[(idx + 1) % cfg.members.length];
    host.innerHTML = `
      <div class="stock-hero">
        <div class="kicker">${basket.toUpperCase()} · ${ticker}</div>
        <h1><img class="stock-logo" data-t="${ticker}" src="logos/${safeTicker(ticker)}.png"
             referrerpolicy="no-referrer" onerror="__logoErr(this)" alt="">${name}${
             name === ticker.split(".")[0] ? "" : `<span class="ticker">${ticker}</span>`}</h1>
        <div class="stat-strip" id="${basket}-sd-stats"></div>
      </div>
      <div class="chapter">
        <div class="chapter-head"><span class="chapter-no">第一章</span><h2>上市以来</h2></div>
        <div class="card">
          <h3>走势（月线 · 对数坐标 · 复权价）</h3>
          <div class="sub">和谁比一比？： <span class="cmp-chips" id="${basket}-cmp"></span></div>
          <div class="chart" id="${basket}-sd-century"></div>
          <p class="footnote" id="${basket}-cmp-note" style="display:none">对比模式：全部序列在共同起点归一化为 100（对数坐标），跑赢基准 = 长期真正的好公司。</p>
        </div>
      </div>
      <div class="chapter">
        <div class="chapter-head"><span class="chapter-no">第二章</span><h2>回报的形状</h2></div>
        <div class="grid-2">
          <div class="card"><h3>年度回报</h3><div class="chart short" id="${basket}-sd-annual"></div></div>
          <div class="card"><h3>年内最大回撤 vs 全年收益</h3><div class="chart short" id="${basket}-sd-intra"></div></div>
        </div>
      </div>
      <div class="chapter">
        <div class="chapter-head"><span class="chapter-no">第三章</span><h2>大跌纪年</h2></div>
        <div class="card"><h3>历史回撤曲线</h3><div class="chart short" id="${basket}-sd-dd"></div></div>
        <div class="card"><h3>深度回撤一览（≥10%）</h3><div class="table-wrap"><table id="${basket}-sd-ddtable"></table></div></div>
      </div>
      <div class="chapter">
        <div class="chapter-head"><span class="chapter-no">第四章</span><h2>时间的纹理</h2></div>
        <div class="grid-2">
          <div class="card"><h3>滚动 5 年年化</h3><div class="chart short" id="${basket}-sd-roll"></div></div>
          <div class="card"><h3>十二个月的脾气</h3><div class="chart short" id="${basket}-sd-season"></div></div>
        </div>
        <div class="card"><h3>已实现波动率（20 日年化）</h3><div class="chart short" id="${basket}-sd-vol"></div></div>
      </div>
      <div class="chapter" id="${basket}-fd-dash">
        <div class="chapter-head"><span class="chapter-no"></span><h2>关键指标仪表盘</h2></div>
        <p class="chapter-q">巴菲特们打开报表前先看的一屏。</p>
        <div class="stat-strip" id="${basket}-fd-dash-cards"></div>
      </div>
      <div class="chapter" id="${basket}-fd-profit">
        <div class="chapter-head"><span class="chapter-no"></span><h2>利润这条线</h2></div>
        <p class="chapter-q">股价背后，利润跟上了吗？</p>
        <div class="card"><h3>EPS（TTM · 季频）</h3><div class="chart short" id="${basket}-fd-eps"></div></div>
        <div class="grid-2">
          <div class="card"><h3>营业收入（近四财年 · 十亿美元）</h3><div class="chart short" id="${basket}-fd-rev"></div></div>
          <div class="card"><h3>净利润（近四财年 · 十亿美元）</h3><div class="chart short" id="${basket}-fd-ni"></div></div>
        </div>
      </div>
      <div class="chapter" id="${basket}-fd-capital">
        <div class="chapter-head"><span class="chapter-no"></span><h2>资本效率</h2></div>
        <p class="chapter-q">一块钱资本，赚回多少？（芒格：长期回报趋近 ROIC）</p>
        <div class="grid-2">
          <div class="card"><h3>ROE × ROIC（TTM）</h3><div class="chart short" id="${basket}-fd-roe"></div></div>
          <div class="card"><h3>自由现金流（年度 · 十亿美元）</h3><div class="chart short" id="${basket}-fd-fcf"></div></div>
        </div>
      </div>
      <div class="chapter" id="${basket}-fd-valuation">
        <div class="chapter-head"><span class="chapter-no"></span><h2>估值的位置</h2></div>
        <p class="chapter-q">现在的价格，在自己的历史里算贵吗？</p>
        <div class="k-status" id="${basket}-fd-valstats"></div>
        <div class="grid-2">
          <div class="card"><h3>PE（TTM · 含历史中位数）</h3><div class="chart short" id="${basket}-fd-pe"></div></div>
          <div class="card"><h3 id="${basket}-fd-ps-title">PS（TTM）</h3><div class="chart short" id="${basket}-fd-ps"></div></div>
        </div>
      </div>
      <div class="chapter" id="${basket}-fd-driver">
        <div class="chapter-head"><span class="chapter-no"></span><h2>估值驱动 vs EPS 驱动</h2></div>
        <p class="chapter-q">每年的涨跌，是利润挣来的，还是估值给的？</p>
        <div class="card"><h3>年度回报分解</h3><div class="sub">(1+回报) = (1+EPS变化) × (1+估值变化)，年末对年末</div><div class="chart short" id="${basket}-fd-driver-ch"></div></div>
      </div>
      <div class="chapter" id="${basket}-fd-payout">
        <div class="chapter-head"><span class="chapter-no"></span><h2>股东回报</h2></div>
        <p class="chapter-q">分红有没有年年长大？</p>
        <div class="card"><h3>每股分红（年度合计 · 各自币种）</h3><div class="chart short" id="${basket}-fd-div"></div></div>
      </div>
      <div class="chapter" id="${basket}-fd-peers">
        <div class="chapter-head"><span class="chapter-no"></span><h2>同业对比</h2></div>
        <p class="chapter-q">放回同一个篮子里看，贵还是便宜、强还是弱？</p>
        <div class="card"><h3>估值与质量 vs 同篮子</h3><div class="chart short" id="${basket}-fd-peers-ch"></div></div>
      </div>
      <div class="stock-nav">
        <a href="${basket}?s=${safeTicker(prev[0])}">← <span>${prev[1]}</span> ${prev[0]}</a>
        <a href="${basket}"><span>回到</span><span>${cfg.anchorLabel}</span></a>
        <a href="${basket}?s=${safeTicker(next[0])}"><span>${next[1]}</span> ${next[0]} →</a>
      </div>`;

    // 关键数据条
    load(basket + "_table").then((d) => {
      const r = d.rows.find((x) => x.ticker === ticker);
      // 新成员上线当天：app.js 带 ?v= 会立刻更新、data JSON 不带（sw 是 stale-while-revalidate），
      // 于是回访者第一次点新票时表里还没有它的行。别静默留白：说清楚，下次刷新就好了。
      if (!r) {
        document.getElementById(basket + "-sd-stats").innerHTML =
          '<div class="stat"><div class="label">关键数据</div><div class="value" style="font-size:14px">' +
          '数据更新中，刷新后出现 · data updating</div></div>';
        return;
      }
      const f = (v) => v == null ? "--" : (v > 0 ? "+" : "") + v.toFixed(1) + "%";
      const cls = (v) => v == null ? "" : v >= 0 ? "pos" : "neg";
      const totalPct = r.total_mult ? ((r.total_mult - 1) * 100).toLocaleString("en-US", { maximumFractionDigits: 0 }) : null;
      document.getElementById(basket + "-sd-stats").innerHTML = [
        ["上市数据起点", r.start_full.slice(0, 7), ""],
        ["上市以来总回报", r.total_mult ? "×" + r.total_mult.toLocaleString("en-US", { maximumFractionDigits: 1 }) : "--",
         "pos", totalPct ? "+" + totalPct + "%" : ""],
        ["YTD", f(r.ytd), cls(r.ytd)],
        ["5 年年化", f(r.y5), cls(r.y5)],
        ["10 年年化", f(r.y10), cls(r.y10)],
        ["上市以来年化", f(r.since_full), cls(r.since_full)],
        ["历史最大回撤", r.max_dd_full + "%", "neg"],
      ].map(([l, v, c, note]) =>
        `<div class="stat"><div class="label">${l}</div><div class="value ${c}">${v}</div>` +
        (note ? `<div class="note">${note}</div>` : "") + "</div>"
      ).join("");
    });

    setupCmp(basket, safe, ticker, name);
    await buildStockCentury(basket, safe, name);
    await buildOne(basket + "-sd-annual", annualChart(p + "_annual"));
    await buildOne(basket + "-sd-intra", intraChart(p + "_intrayear"));
    await buildOne(basket + "-sd-dd", ddChart(p + "_drawdowns"));
    await buildOne(basket + "-sd-roll", rollChart(p + "_rolling5y"));
    await buildOne(basket + "-sd-season", seasonChart(p + "_seasonality"));
    await buildOne(basket + "-sd-vol", volChart(p + "_volatility"));
    renderDDTable(p + "_drawdowns", basket + "-sd-ddtable");
    await renderFund(basket, safe, ticker);
    stampSources();
    window.scrollTo(0, 0);
  }

  // ---------------- 个股回报对比（vs 标普/纳指/行业ETF） ----------------
  const XLP_MEMBERS = new Set(["KO", "WMT", "COST"]);
  // 对比四色：个股深红 / 标普 Chase 蓝 / 纳指深紫 / 行业ETF 凯尔特人绿
  function cmpDefs(basket, ticker) {
    const defs = [["sp500_century", "标普 500", "cmpBlue"], ["ndx_century", "纳指 100", "cmpPurple"]];
    if (basket === "tech") defs.push(["s_xlk_century", "XLK 科技", "cmpGreen"]);
    else if (basket === "fin") defs.push(["s_xlf_century", "XLF 金融", "cmpGreen"]);
    else if (basket === "consumer") defs.push(XLP_MEMBERS.has(ticker)
      ? ["s_xlp_century", "XLP 必需消费", "cmpGreen"] : ["s_xly_century", "XLY 可选消费", "cmpGreen"]);
    return defs;
  }

  let cmpSel = new Set();
  let cmpStockKey = null; // 换股时重置选择

  function setupCmp(basket, safe, ticker, name) {
    const key = basket + "/" + safe;
    if (key !== cmpStockKey) { cmpSel = new Set(); cmpStockKey = key; }
    const host = document.getElementById(basket + "-cmp");
    const defs = cmpDefs(basket, ticker);
    host.innerHTML = defs.map(([ds, label]) =>
      `<button class="pill cmp-chip ${cmpSel.has(ds) ? "active" : ""}" data-ds="${ds}">${label}</button>`).join("");
    host.onclick = async (e) => {
      const chip = e.target.closest(".cmp-chip");
      if (!chip) return;
      cmpSel.has(chip.dataset.ds) ? cmpSel.delete(chip.dataset.ds) : cmpSel.add(chip.dataset.ds);
      chip.classList.toggle("active");
      await buildStockCentury(basket, safe, name);
    };
  }

  async function buildStockCentury(basket, safe, name) {
    const elId = basket + "-sd-century";
    const note = document.getElementById(basket + "-cmp-note");
    if (cmpSel.size === 0) {
      if (note) note.style.display = "none";
      return buildOne(elId, centuryChart(null, [{ ds: "s_" + safe + "_century", name }]));
    }
    if (note) note.style.display = "";
    const defs = cmpDefs(basket, BASKET_CFG[basket].members.find(([t]) => safeTicker(t) === safe)[0]);
    await buildOne(elId, async (p) => {
      const stock = await load("s_" + safe + "_century");
      const selected = defs.filter(([ds]) => cmpSel.has(ds));
      const comps = await Promise.all(selected.map(([ds]) => load(ds)));
      const all = [
        { name, dates: stock.dates, values: stock.close, color: p.cmpRed, width: 2.2 },
        ...comps.map((c, i) => ({
          name: selected[i][1], dates: c.dates, values: c.close,
          color: p[selected[i][2]], width: 1.2,
        })),
      ];
      // 共同起点 = 最晚起始的序列；全部归一化为 100
      const start = all.map((s) => s.dates[0]).sort().slice(-1)[0];
      const series = all.map((s) => {
        const i0 = s.dates.findIndex((d) => d >= start);
        const base = s.values[i0];
        return {
          name: s.name, type: "line", showSymbol: false,
          data: s.dates.slice(i0).map((d, j) => [d, Math.round(s.values[i0 + j] / base * 1000) / 10]),
          lineStyle: { color: s.color, width: s.width }, itemStyle: { color: s.color },
        };
      });
      return {
        tooltip: tip(p),
        legend: { textStyle: { color: p.muted, fontSize: 11 }, top: 0 },
        grid: { left: 64, right: 24, top: 32, bottom: 60 },
        xAxis: timeX(p),
        yAxis: Object.assign({ type: "log", name: "起点=100" }, baseAxis(p)),
        dataZoom: [{ type: "inside" }, { type: "slider", bottom: 6, height: 18,
          borderColor: p.border, fillerColor: "rgba(160,57,47,0.08)",
          handleStyle: { color: p.accent }, textStyle: { color: p.muted, fontSize: 10 } }],
        series,
      };
    });
  }

  // ---------------- 个股基本面章节（数据可用才显示对应章） ----------------
  const CN_NUM = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二", "十三", "十四", "十五"];

  function renumberChapters(scope) {
    // 2026-07-20：只重排「第N章」式标签；HTML 里精心写的描述性标签（口径实验室/当前横截面/
    // 单票下钻/历史参照…，多为分节内小标题，信息量远大于「第N章」）必须保留，别拍平。
    // renumber 每次 buildToc 都跑、首次就覆盖原文，故首次把原标签缓存进 dataset 再据此判定。
    // 输出走 translate 以匹配当前语言（自定义标签都有 i18n D 键）。
    const T = (s) => (window.MC_I18N ? MC_I18N.translate(s) : s);
    let n = 0;
    [...scope.querySelectorAll(".chapter")].forEach((ch) => {
      const no = ch.querySelector(".chapter-no");
      if (!no) return;
      if (no.dataset.orig === undefined) no.dataset.orig = no.textContent.trim();
      const orig = no.dataset.orig;
      // 「第N章」与**空号**都按出现次序连续编号（空号如 spy「估值的弹性」子节，保持老行为不回归）；
      // 只有**非空的自定义描述性标签**（口径实验室/当前横截面…，作者故意写、有 i18n 键）才保留。
      if (/^第.+章$/.test(orig) || orig === "") { n += 1; no.textContent = T("第" + (CN_NUM[n - 1] || n) + "章"); }
      else no.textContent = T(orig);
    });
  }

  async function renderFund(basket, safe, ticker) {
    let fund = null, peers = null;
    try { fund = await load("s_" + safe + "_fund"); } catch (e) {}
    try { peers = await load(basket + "_peers"); } catch (e) {}
    const host = document.getElementById(basket + "-stock");
    const drop = (key) => { const el = document.getElementById(basket + "-fd-" + key); if (el) el.remove(); };

    const snap = fund && fund.snapshot;
    if (!snap) drop("dash");
    if (!fund || !(fund.eps || fund.income4)) drop("profit");
    if (!fund || !(fund.roe || fund.roic || fund.fcf)) drop("capital");
    if (!fund || !(fund.pe || fund.ps || fund.pb_hist)) drop("valuation");
    if (!fund || !fund.driver || fund.driver.length < 4) drop("driver");
    if (!fund || !fund.dividends) drop("payout");
    if (!peers || !peers.rows || peers.rows.length < 2) drop("peers");
    renumberChapters(host);
    buildToc();
    if (!fund && !peers) return;

    const fmt = (v, d, suffix) => v == null ? "--" : v.toFixed(d) + (suffix || "");
    if (snap) {
      const cards = [
        ["市值", snap.market_cap ? "$" + (snap.market_cap / 1e9).toFixed(0) + "B" : "--"],
        ["PE (TTM)", fmt(snap.pe, 1)], ["远期 PE", fmt(snap.fwd_pe, 1)],
        ["PS", fmt(snap.ps, 1)], ["PB", fmt(snap.pb, 1)],
        ["ROE", fmt(snap.roe, 1, "%")],
        ["毛利率", fmt(snap.gross_margin, 1, "%")], ["净利率", fmt(snap.net_margin, 1, "%")],
        ["股息率", fmt(snap.div_yield, 2, "%")], ["派息率", fmt(snap.payout, 0, "%")],
        ["自由现金流", snap.fcf ? "$" + (snap.fcf / 1e9).toFixed(1) + "B" : "--"],
        ["Beta", fmt(snap.beta, 2)],
      ];
      const el = document.getElementById(basket + "-fd-dash-cards");
      if (el) el.innerHTML = cards.map(([l, v]) =>
        `<div class="stat"><div class="label">${l}</div><div class="value" style="font-size:19px">${v}</div></div>`).join("");
    }

    const line = (data, name, colorKey, opts) => async (p) => {
      const s = {
        name, type: "line", showSymbol: false, data: zip(data.dates, data.values),
        lineStyle: { color: p[colorKey], width: 1.3 }, itemStyle: { color: p[colorKey] },
      };
      if (opts && opts.median) {
        const sorted = data.values.filter((v) => v != null).slice().sort((a, b) => a - b);
        const med = sorted[Math.floor(sorted.length / 2)];
        s.markLine = { silent: true, symbol: "none", lineStyle: { color: p.ink, type: "dashed" },
          label: { color: p.muted, formatter: "中位 " + med.toFixed(1), fontFamily: "JetBrains Mono" },
          data: [{ yAxis: med }] };
      }
      return { tooltip: tip(p), grid: { left: 54, right: 20, top: 20, bottom: 26 },
        xAxis: timeX(p), yAxis: Object.assign({ type: "value" }, baseAxis(p)), series: [s] };
    };
    const bars = (cats, vals, colorKey, signColor) => async (p) => ({
      tooltip: tip(p, { axisPointer: { type: "shadow" } }),
      grid: { left: 54, right: 20, top: 20, bottom: 26 },
      xAxis: Object.assign({ type: "category", data: cats, splitLine: { show: false } }, baseAxis(p)),
      yAxis: Object.assign({ type: "value" }, baseAxis(p)),
      series: [{ type: "bar", barCategoryGap: "30%",
        data: vals.map((v) => ({ value: v, itemStyle: { color: signColor && v < 0 ? p.danger : p[colorKey] } })) }],
    });

    if (fund) {
      if (fund.eps) await buildOne(basket + "-fd-eps", line(fund.eps, "EPS TTM", "moss"));
      else { const c = document.getElementById(basket + "-fd-eps"); if (c) c.closest(".card").remove(); }
      if (fund.income4 && fund.income4.revenue) {
        await buildOne(basket + "-fd-rev", bars(fund.income4.years, fund.income4.revenue, "blue"));
        await buildOne(basket + "-fd-ni", bars(fund.income4.years, fund.income4.net_income, "moss", true));
      }
      if (!fund.roe && !fund.roic) {
        const c = document.getElementById(basket + "-fd-roe");
        if (c) c.closest(".card").remove();
      }
      if (fund.roe || fund.roic) {
        await buildOne(basket + "-fd-roe", async (p) => ({
          tooltip: tip(p), legend: { textStyle: { color: p.muted, fontSize: 11 }, top: 0 },
          grid: { left: 54, right: 20, top: 28, bottom: 26 },
          xAxis: timeX(p),
          yAxis: Object.assign({ type: "value", axisLabel: { formatter: "{value}%", color: p.muted, fontFamily: "JetBrains Mono", fontSize: 11 } }, baseAxis(p)),
          series: [fund.roe && { name: "ROE", type: "line", showSymbol: false, data: zip(fund.roe.dates, fund.roe.values),
              lineStyle: { color: p.accent, width: 1.3 }, itemStyle: { color: p.accent } },
            fund.roic && { name: "ROIC", type: "line", showSymbol: false, data: zip(fund.roic.dates, fund.roic.values),
              lineStyle: { color: p.teal, width: 1.3 }, itemStyle: { color: p.teal } }].filter(Boolean),
        }));
      }
      if (fund.fcf) await buildOne(basket + "-fd-fcf",
        bars(fund.fcf.dates, fund.fcf.values.map((v) => v == null ? null : Math.round(v / 100) / 10), "gold", true));
      else { const c = document.getElementById(basket + "-fd-fcf"); if (c) c.closest(".card").remove(); }
      if (fund.pe) {
        await buildOne(basket + "-fd-pe", line(fund.pe, "PE", "accent", { median: true }));
        const vs = document.getElementById(basket + "-fd-valstats");
        if (vs) {
          const cur = fund.pe.values[fund.pe.values.length - 1];
          const med = median(fund.pe.values);
          const pct = percentile(fund.pe.values, cur);
          const snapFwd = fund.snapshot && fund.snapshot.fwd_pe;
          vs.innerHTML = [
            ["当前 PE (TTM)", cur.toFixed(1), `自身 ${fund.pe.dates[0].slice(0, 4)}→ 第 ${pct} 百分位`, pct > 90],
            ["历史中位数", med.toFixed(1), "约 " + fund.pe.dates[0].slice(0, 4) + " 年以来", false],
            ["远期 PE", snapFwd ? snapFwd.toFixed(1) : "--", "yfinance 快照", false],
            ["相对中位溢价", ((cur / med - 1) * 100).toFixed(0) + "%", cur > med ? "贵于历史中枢" : "低于历史中枢", false],
          ].map(([l, v, n, hot]) =>
            `<div class="stat ${hot ? "signal-on" : ""}"><div class="label">${l}</div><div class="value" style="font-size:19px">${v}</div><div class="note">${n}</div></div>`).join("");
        }
      }
      const psData = fund.ps || fund.pb_hist;
      if (psData) {
        const t = document.getElementById(basket + "-fd-ps-title");
        if (t) t.textContent = fund.ps ? "PS（TTM）" : "PB（银行类更看市净率）";
        await buildOne(basket + "-fd-ps", line(psData, fund.ps ? "PS" : "PB", "gold", { median: true }));
      } else { const c = document.getElementById(basket + "-fd-ps"); if (c) c.closest(".card").remove(); }
      if (fund.driver && fund.driver.length >= 4) {
        await buildOne(basket + "-fd-driver-ch", async (p) => ({
          tooltip: tip(p, { axisPointer: { type: "shadow" } }),
          legend: { textStyle: { color: p.muted, fontSize: 11 }, top: 0 },
          grid: { left: 54, right: 20, top: 28, bottom: 26 },
          xAxis: Object.assign({ type: "category", data: fund.driver.map((d) => d.year), splitLine: { show: false } }, baseAxis(p)),
          yAxis: Object.assign({ type: "value", axisLabel: { formatter: "{value}%", color: p.muted, fontFamily: "JetBrains Mono", fontSize: 11 } }, baseAxis(p)),
          series: [
            { name: "EPS 变化", type: "bar", stack: "drv", data: fund.driver.map((d) => d.eps_chg), itemStyle: { color: p.moss, opacity: 0.85 } },
            { name: "估值变化", type: "bar", stack: "drv", data: fund.driver.map((d) => d.pe_chg), itemStyle: { color: p.gold, opacity: 0.85 } },
            { name: "全年回报", type: "line", symbolSize: 5, data: fund.driver.map((d) => d.price_ret),
              lineStyle: { color: p.ink, width: 1.6 }, itemStyle: { color: p.ink } },
          ],
        }));
      }
      if (fund.dividends) await buildOne(basket + "-fd-div",
        bars(fund.dividends.years, fund.dividends.amounts, "moss"));
    }

    if (peers && peers.rows && peers.rows.length > 1) {
      await buildOne(basket + "-fd-peers-ch", async (p) => ({
        tooltip: tip(p, { axisPointer: { type: "shadow" } }),
        legend: { textStyle: { color: p.muted, fontSize: 11 }, top: 0 },
        grid: { left: 54, right: 20, top: 28, bottom: 26 },
        xAxis: Object.assign({ type: "category", data: peers.rows.map((r) => r.ticker.split(".")[0]), splitLine: { show: false } }, baseAxis(p)),
        yAxis: Object.assign({ type: "value" }, baseAxis(p)),
        series: [
          ["PE (TTM)", "pe", "accent"], ["ROE %", "roe", "teal"], ["净利率 %", "net_margin", "gold"],
        ].map(([n, k, c]) => ({
          name: n, type: "bar",
          data: peers.rows.map((r) => ({
            value: r[k] == null ? null : Math.round(r[k] * 10) / 10,
            itemStyle: { color: p[c], opacity: r.ticker === ticker ? 1 : 0.35 },
          })),
        })),
      }));
    }
  }

  // ---------------- 真实路径路由（2026-07-26：由 #hash 改造而来） ----------------
  // 为什么改：站是单页应用，此前切板块不改 URL，于是 Cloudflare Web Analytics 里
  // 所有访问都只记成一条 `/` —— 有人把标普十几章翻完，数据里也看不出来。
  // 每个板块有了自己的路径，才分得开访问量，也才能被单独分享、被搜索引擎收录。
  //
  // 🚨 只用「一段」路径（/spy、/kindex），个股走 query（/spy?s=AAPL），**不做** /spy/AAPL：
  //    站内资源全是相对路径（css/style.css、fetch("data/…")、register("sw.js")），
  //    路径多一层目录后它们会去请求 /spy/css/…，而 Cloudflare Pages 对未命中的路径
  //    一律回 200 + 整份 index.html —— 资源拿到的是 HTML，解析失败却不报 404，
  //    控制台干干净净、页面样式全丢。一段路径的 base 目录仍是站根，天然避开这个坑。
  //    （同理，绝不能往仓库根加 404.html：那会关掉 Pages 的这个兜底，深链当场全挂。）
  //
  // ROOT = 站根。取 pathname 最后一个「/」及其之前的部分：
  //   /spy → "/"   /index.html → "/"   /market-chronicle/spy → "/market-chronicle/"
  //   最后这条让 github.io 子目录镜像照常可用（全站不出现绝对路径）。
  const ROOT = location.pathname.slice(0, location.pathname.lastIndexOf("/") + 1);
  const DOC_PANELS = ["about", "contact", "privacy", "terms", "refunds", "pricing", "methodology"];

  // 组 URL。首页归一到 ROOT 而不是 /pulse，否则统计里首页会裂成两条。
  // 除 s 之外的既有 query 一律保留 —— ?code=forever99（创始价带码链接）靠它活着。
  function urlFor(panel, stock) {
    const q = new URLSearchParams(location.search);
    q.delete("s");
    if (stock) q.set("s", stock);
    const qs = q.toString();
    return ROOT + (panel === "pulse" ? "" : panel) + (qs ? "?" + qs : "");
  }

  // 读 URL。末段必须**精确等于**某个 panel 名才算命中，
  // 这样 /welcome/、/pay/ 这类真实目录不会被误判成板块。
  function readUrl() {
    const rest = location.pathname.startsWith(ROOT) ? location.pathname.slice(ROOT.length) : "";
    // 个股代码按 safeTicker 的口径归一（小写、点改横杠）：
    // 路径 URL 是要被手打和转发的，?s=NVDA 与 ?s=BRK.B 都得能打开
    const s = new URLSearchParams(location.search).get("s") || "";
    return {
      panel: registry[rest] ? rest : "pulse",
      stock: s.toLowerCase().replace(".", "-"),
    };
  }

  // 站内跳转：写地址栏 + 重路由。整页刷新由调用方 preventDefault 拦掉。
  function navigate(panel, stock) {
    const url = urlFor(panel, stock || "");
    if (url !== location.pathname + location.search) history.pushState({}, "", url);
    route();
  }

  // 规范链接跟着路由走（个股不单独立 canonical：它只是同一页里的一个视图）
  function syncCanonical(panel) {
    const href = "https://chronicle.klay-wang.com/" + (panel === "pulse" ? "" : panel);
    document.querySelector('link[rel="canonical"]')?.setAttribute("href", href);
    document.querySelector('meta[property="og:url"]')?.setAttribute("content", href);
  }

  function route() {
    const { panel, stock } = readUrl();
    activatePanel(panel).then(() => {
      if (BASKET_CFG[panel]) {
        if (stock) showStock(panel, stock);
        else showOverview(panel);
      }
      buildToc();
    });
    syncCanonical(panel);
    // 页脚静态页从顶部看起
    if (DOC_PANELS.includes(panel)) window.scrollTo(0, 0);
  }

  // 进站归一（同步执行，早于页尾的 Paddle 内联脚本与 beacon，故两者读到的都是最终 URL）：
  //  ① 旧的 #spy / #tech/NVDA 链接站外和 Wayback 快照里还有，转成路径；
  //  ② /pulse 与任何不认识的路径收敛到站根，免得统计里长出一堆同义或垃圾路径。
  {
    const [hp, hs] = decodeURIComponent(location.hash.slice(1)).split("/");
    if (hp && registry[hp]) history.replaceState({}, "", urlFor(hp, hs || ""));
    else if (readUrl().panel === "pulse" && location.pathname !== ROOT)
      history.replaceState({}, "", urlFor("pulse", readUrl().stock));
  }
  window.addEventListener("popstate", route);
  // 兜底：万一还有代码/外链在页内改 hash，照样接住
  window.addEventListener("hashchange", () => {
    const [hp, hs] = decodeURIComponent(location.hash.slice(1)).split("/");
    if (hp && registry[hp]) { history.replaceState({}, "", urlFor(hp, hs || "")); route(); }
  });

  // 站内链接统一走 pushState（否则每次点击整页刷新，SPA 体验与已建好的图表全丢）。
  // 判据是「解析后的路径末段精确等于某个 panel 名」——
  // /pay/、/welcome/ 这类真实目录与站外链接一律放行给浏览器。
  document.addEventListener("click", (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = e.target.closest("a[href]");
    if (!a || a.target === "_blank" || a.hasAttribute("download")) return;
    if (a.getAttribute("href") === "#") return;              // #pay-btn 这类占位链接，交给它自己的处理器
    const u = new URL(a.href, location.href);
    if (u.origin !== location.origin || !u.pathname.startsWith(ROOT)) return;
    const rest = u.pathname.slice(ROOT.length);
    if (rest !== "" && !registry[rest]) return;
    // §63：瘦身路由页 registry 仍是全量，但 DOM 只有本页 panel——
    // 目标面板不在 DOM 就放行给浏览器整页导航到对应路由页
    if (!document.getElementById("panel-" + (rest || "pulse"))) return;
    e.preventDefault();
    navigate(rest || "pulse", new URLSearchParams(u.search).get("s") || "");
  });

  // 点击站名 = 回首页并滚到顶部（回首页本身由上面的链接委托完成，这里只管滚动；
  // tab 切换不受影响，仍保留各自滚动位置）
  document.querySelector(".brand")?.addEventListener("click", () => window.scrollTo(0, 0));

  // ---------------- 左侧悬浮章节目录 ----------------
  const ROMAN = ["Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "Ⅴ", "Ⅵ", "Ⅶ", "Ⅷ", "Ⅸ", "Ⅹ", "Ⅺ", "Ⅻ", "ⅩⅢ", "ⅩⅣ", "ⅩⅤ"];
  const tocEl = document.getElementById("toc");
  let tocChapters = [];

  function highlightToc() {
    if (!tocChapters.length) return;
    let current = tocChapters[0];
    for (const c of tocChapters) {
      if (c.getBoundingClientRect().top <= 150) current = c;
    }
    let activeLink = null;
    tocEl.querySelectorAll("a").forEach((a) => {
      const on = a.dataset.target === current.id;
      a.classList.toggle("active", on);
      if (on) activeLink = a;
    });
    // 2026-07-26 目录跟着读者走：章节多时（标普 14 章）目录列表本身会溢出，
    // 当前章滚出可视区就看不见了 —— 高亮等于白高亮。这里把目录容器滚到当前项可见。
    // 只在真的超出时滚，且用 scrollTop 直接改（不用 scrollIntoView：那会连带滚动整个页面）。
    if (activeLink && tocEl.scrollHeight > tocEl.clientHeight + 2) {
      const top = activeLink.offsetTop, bottom = top + activeLink.offsetHeight;
      const viewTop = tocEl.scrollTop, viewBottom = viewTop + tocEl.clientHeight;
      const pad = 24;
      if (top < viewTop + pad) tocEl.scrollTop = Math.max(0, top - pad);
      else if (bottom > viewBottom - pad) tocEl.scrollTop = bottom - tocEl.clientHeight + pad;
    }
  }
  // 目录跟随：固定目录默认悬在视口(top 由 CSS 的 .toc{top} 决定)，内容短时会飘到页脚旁的空白里。
  // 🚨 别把这个值写死：2026-07-26 把 .toc 的 top 从 116 下移到 168 时，这里若仍按 116 算，
  //    目录会提前 52px 顶起来。改为运行时读实际 top，CSS 再调也不用同步改这里。
  // 2026-07-20 用户要求「和底部表格对齐，不要到底部」：滚到内容尾部时把目录整体顶上去，
  // 使其底缘 = 正文底缘（.container 底），不再越过内容飘向页脚。
  const containerEl = document.querySelector(".container");
  function positionToc() {
    // 只在桌面固定态生效；窄屏是抽屉，别动它的 transform
    if (!window.matchMedia("(min-width: 1280px)").matches || tocEl.classList.contains("open")) {
      tocEl.style.transform = ""; return;
    }
    if (!containerEl) return;
    const contentBottom = containerEl.getBoundingClientRect().bottom;
    const tocTop = parseFloat(getComputedStyle(tocEl).top) || 116;   // 与 CSS 保持单一真源
    const overshoot = (tocTop + tocEl.offsetHeight) - (contentBottom - 8); // 目录底超出正文底多少
    tocEl.style.transform = overshoot > 0 ? `translateY(${-overshoot}px)` : "";
  }
  let tocTick = false;
  window.addEventListener("scroll", () => {
    if (tocTick) return;
    tocTick = true;
    setTimeout(() => { highlightToc(); positionToc(); tocTick = false; }, 80);
  }, { passive: true });
  window.addEventListener("resize", positionToc, { passive: true });
  setInterval(() => { highlightToc(); positionToc(); }, 500); // 兜底，保证高亮与跟随永远跟手

  function buildToc() {
    const panel = document.querySelector(".panel.active");
    tocChapters = [];
    if (!panel) { tocEl.innerHTML = ""; return; }
    let scope = panel;
    const stockView = panel.querySelector(".basket-stock");
    if (stockView && stockView.style.display !== "none") scope = stockView;
    else {
      const ov = panel.querySelector(".basket-overview");
      if (ov) scope = ov;
    }
    const heads = [...scope.querySelectorAll(".chapter-head h2")];
    const chapters = heads.map((h, i) => {
      const ch = h.closest(".chapter");
      if (!ch.id) ch.id = panel.id + "-ch" + i; // 已有 id（如 fd-* 章节）保留，供数据裁剪定位
      return ch;
    });
    // 标题单独包 span，文本节点独立才能命中 i18n 词典
    tocEl.innerHTML = heads.map((h, i) =>
      `<a data-target="${chapters[i].id}" title="${h.textContent}">${ROMAN[i] || i + 1} · <span>${h.textContent.split("：")[0]}</span></a>`
    ).join("");
    renumberChapters(scope); // 插入/裁剪章节后重排"第N章"标签
    tocChapters = chapters;
    // 无章节的页面（「今日」头版就是）不该继续为目录预留左栏：
    // ≥1280px 时 .container 的 margin-left 固定让位 214px，而目录是空的 →
    // 实测 1600px 下左边距 254 / 右边距 225，内容被推偏、左侧 216px 是死白。
    document.documentElement.classList.toggle("no-toc", chapters.length === 0);
    document.documentElement.classList.toggle("has-toc", chapters.length > 0);
    /* TOC 让位切换是**已知的**容器宽度变更点：从无目录页（「今日」头版）切进有目录页时，
       图表在 .then(buildToc) 之前就建完了，宽度是让位前的旧值。平时靠 ResizeObserver 的
       rAF 追平；但 rAF 在 headless 虚拟时间下会被饿死（2026-08-11 CI 溢出闸两连红、
       且只红 pulse 之后第一个有目录的面板＝本机制的指纹）。这里已确知尺寸变了，
       同步校一遍，不等下一帧。与 visibilitychange 兜底同款判据。 */
    built.forEach((c, id) => {
      const cel = document.getElementById(id);
      if (cel && cel.clientWidth > 0 && !c.isDisposed() && Math.abs(c.getWidth() - cel.clientWidth) > 1) c.resize();
    });
    highlightToc();
    positionToc();
  }
  tocEl.addEventListener("click", (e) => {
    const a = e.target.closest("a");
    if (!a) { tocEl.classList.remove("open"); return; } // 点遮罩空白处即收起抽屉
    const target = document.getElementById(a.dataset.target);
    if (target) {
      const y = target.getBoundingClientRect().top + window.scrollY - 120;
      window.scrollTo({ top: y, behavior: "smooth" });
    }
    if (window.innerWidth < 1560) tocEl.classList.remove("open");
  });
  document.getElementById("toc-toggle").addEventListener("click", () =>
    tocEl.classList.toggle("open"));

  // ---------------- 通用 option 片段 ----------------
  function baseAxis(p) {
    return {
      axisLine: { lineStyle: { color: p.gridStrong } },
      axisLabel: { color: p.muted, fontFamily: "JetBrains Mono", fontSize: 11 },
      splitLine: { lineStyle: { color: p.grid } },
    };
  }
  function timeX(p, extra) {
    return Object.assign({ type: "time", splitLine: { show: false } }, baseAxis(p), extra || {});
  }
  function tip(p, extra) {
    return Object.assign({
      trigger: "axis",
      backgroundColor: p.card,
      borderColor: p.border,
      textStyle: { color: p.ink, fontSize: 12, fontFamily: "JetBrains Mono" },
      axisPointer: { type: "cross", label: { backgroundColor: p.inkSoft } },
    }, extra || {});
  }
  function zip(dates, vals) {
    return dates.map((d, i) => [d, vals[i]]);
  }
  const pct = (v) => (v == null ? "--" : (v > 0 ? "+" : "") + v.toFixed(1) + "%");

  // ================= K 指数 =================
  chart("kindex", "ch-kindex", async (p) => {
    const [kd, sig] = await Promise.all([load("kindex"), load("kindex_signals")]);
    const areas = sig.signals.map((s) => [
      { xAxis: s.start, itemStyle: { color: p.accent, opacity: 0.10 } },
      { xAxis: s.end },
    ]);
    return {
      tooltip: tip(p),
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      grid: [
        { left: 58, right: 64, top: 28, height: "38%" },
        { left: 58, right: 64, top: "56%", height: "30%" },
      ],
      xAxis: [
        Object.assign(timeX(p), { gridIndex: 0, axisLabel: { show: false } }),
        Object.assign(timeX(p), { gridIndex: 1 }),
      ],
      yAxis: [
        Object.assign({ type: "value", name: "CNN / VIX", min: 0, max: 100, gridIndex: 0 }, baseAxis(p)),
        Object.assign({ type: "log", name: "NDX", gridIndex: 0, position: "right", splitLine: { show: false } }, baseAxis(p)),
        Object.assign({ type: "value", name: "K 指数", min: 0, max: 12, interval: 2, gridIndex: 1 }, baseAxis(p)),
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: [0, 1] },
        { type: "slider", xAxisIndex: [0, 1], bottom: 6, height: 18,
          borderColor: p.border, backgroundColor: "transparent",
          fillerColor: "rgba(160,57,47,0.08)", handleStyle: { color: p.accent },
          textStyle: { color: p.muted, fontSize: 10 } },
      ],
      series: [
        { name: "CNN 恐贪", type: "line", xAxisIndex: 0, yAxisIndex: 0, showSymbol: false,
          data: zip(kd.dates, kd.cnn), lineStyle: { color: p.moss, width: 1.4 }, itemStyle: { color: p.moss } },
        { name: "VIX", type: "line", xAxisIndex: 0, yAxisIndex: 0, showSymbol: false,
          data: zip(kd.dates, kd.vix), lineStyle: { color: p.danger, width: 1.4 }, itemStyle: { color: p.danger },
          markArea: { silent: true, data: areas } },
        { name: "纳指 100", type: "line", xAxisIndex: 0, yAxisIndex: 1, showSymbol: false,
          data: zip(kd.dates, kd.ndx), lineStyle: { color: p.blue, width: 1.6 }, itemStyle: { color: p.blue } },
        { name: "K 指数", type: "line", xAxisIndex: 1, yAxisIndex: 2, showSymbol: false,
          data: zip(kd.dates, kd.k), lineStyle: { color: p.purple, width: 1.6 }, itemStyle: { color: p.purple },
          markLine: { silent: true, symbol: "none",
            lineStyle: { color: p.ink, type: "dashed", width: 1.2 },
            label: { color: p.ink, formatter: "K = 1", fontFamily: "JetBrains Mono" },
            data: [{ yAxis: 1 }] },
          markArea: { silent: true, data: areas } },
      ],
    };
  });

  async function renderKStatus() {
    const [kd, sig] = await Promise.all([load("kindex"), load("kindex_signals")]);
    const cur = kd.current;
    const on = cur.k < 1;
    const last = sig.signals[sig.signals.length - 1];
    const el = document.getElementById("k-status");
    if (!el) return;  // §63 瘦身路由页：kindex 面板不在本页
    el.innerHTML = `
      <div class="stat ${on ? "signal-on" : ""}">
        <div class="label">今日 K 指数（${cur.date.replace(/-/g, "\u2011")}）</div>
        <div class="value">${cur.k.toFixed(2)}</div>
        <div class="note">${on ? "★ 金风玉露相逢 — 信号触发" : "未触发（K ≥ 1）"}</div>
      </div>
      <div class="stat"><div class="label">CNN 恐惧贪婪</div><div class="value">${cur.cnn.toFixed(0)}</div><div class="note">${cur.rating || ""}</div></div>
      <div class="stat"><div class="label">VIX</div><div class="value">${cur.vix.toFixed(1)}</div><div class="note">恐慌保险的价格 · K = CNN ÷ VIX</div></div>
      <div class="stat"><div class="label">最近一次信号</div><div class="value" style="font-size:18px">${last.start.replace(/-/g, "\u2011")}</div><div class="note">至今 ${pct(last.fwd_to_date)}</div></div>`;

    const tbl = document.getElementById("k-table");
    const cell = (v) => v == null ? "<td>--</td>" :
      `<td class="${v >= 0 ? "pos" : "neg"}">${pct(v)}</td>`;
    tbl.innerHTML =
      "<tr><th>#</th><th>信号首日</th><th>持续(日)</th><th>最低 K</th>" +
      "<th><span>纳指</span> +20d</th><th><span>纳指</span> +40d</th><th><span>纳指</span> +60d</th>" +
      "<th><span>标普</span> +60d</th><th><span>纳指</span><span>至今</span></th><th><span>标普</span><span>至今</span></th></tr>" +
      sig.signals.map((s, i) =>
        `<tr><td>${i + 1}</td><td>${s.start}</td><td>${s.days_below}</td>` +
        `<td class="k-min">${s.min_k.toFixed(2)}</td>` +
        cell(s.fwd20) + cell(s.fwd40) + cell(s.fwd60) + cell(s.spx_fwd60) + cell(s.fwd_to_date) + cell(s.spx_to_date) + "</tr>"
      ).join("");

    // 持有期矩阵：五个视界 × 双锚的胜率与中位收益（120/250 视界未到期的信号不计入）
    const mtEl = document.getElementById("k-matrix");
    if (mtEl) {
      const HZ = [20, 40, 60, 120, 250];
      const median = (a) => { const b = [...a].sort((x, y) => x - y); const m = b.length >> 1; return b.length % 2 ? b[m] : (b[m - 1] + b[m]) / 2; };
      const rowFor = (prefix) => {
        const wins = [], avgs = [], meds = [];
        for (const h of HZ) {
          const vals = sig.signals.map((s) => s[`${prefix}fwd${h}`]).filter((v) => v != null);
          wins.push(vals.length ? `${vals.filter((v) => v > 0).length}/${vals.length}` : "--");
          avgs.push(vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null);
          meds.push(vals.length ? median(vals) : null);
        }
        return { wins, avgs, meds };
      };
      const nd = rowFor(""), sp = rowFor("spx_");
      const medCell = (v) => v == null ? "<td>--</td>" : `<td class="${v >= 0 ? "pos" : "neg"}">${pct(v)}</td>`;
      mtEl.innerHTML =
        `<tr><th></th>${HZ.map((h) => `<th>+${h}d</th>`).join("")}</tr>` +
        `<tr><td>纳指 胜率</td>${nd.wins.map((w) => `<td>${w}</td>`).join("")}</tr>` +
        `<tr><td>纳指 平均收益</td>${nd.avgs.map(medCell).join("")}</tr>` +
        `<tr><td>纳指 中位收益</td>${nd.meds.map(medCell).join("")}</tr>` +
        `<tr><td>标普 胜率</td>${sp.wins.map((w) => `<td>${w}</td>`).join("")}</tr>` +
        `<tr><td>标普 平均收益</td>${sp.avgs.map(medCell).join("")}</tr>` +
        `<tr><td>标普 中位收益</td>${sp.meds.map(medCell).join("")}</tr>`;
    }

    const n = sig.signals.length;
    const win60 = sig.signals.filter((s) => s.fwd60 != null && s.fwd60 > 0).length;
    const has60 = sig.signals.filter((s) => s.fwd60 != null).length;
    const winS = sig.signals.filter((s) => s.spx_fwd60 != null && s.spx_fwd60 > 0).length;
    const hasS = sig.signals.filter((s) => s.spx_fwd60 != null).length;
    document.getElementById("k-verdict").textContent =
      `实证结论：2011 年以来共 ${n} 次信号。60 个交易日窗口胜率：标普 ${winS}/${hasS}、纳指 ${win60}/${has60}` +
      `（V 形回调中几乎必胜；2021 末—2022 的持续熊市中信号会连续触发、短期窗口为负）。` +
      `所有信号持有至今全部为正。历史规律不保证未来。`;
  }

  // ================= 通用面板构建 =================
  function centuryChart(dsName, seriesDefs) {
    return async (p) => {
      const colors = [p.blue, p.accent, p.gold];
      const datasets = await Promise.all(seriesDefs.map((s) => load(s.ds)));
      return {
        tooltip: tip(p),
        legend: seriesDefs.length > 1 ? { textStyle: { color: p.muted, fontSize: 11 }, top: 0 } : undefined,
        grid: { left: 70, right: 24, top: 30, bottom: 60 },
        xAxis: timeX(p),
        yAxis: Object.assign({ type: "log", splitLine: { lineStyle: { color: pal().grid } } }, baseAxis(p)),
        dataZoom: [{ type: "inside" }, { type: "slider", bottom: 6, height: 18,
          borderColor: p.border, fillerColor: "rgba(160,57,47,0.08)",
          handleStyle: { color: p.accent }, textStyle: { color: p.muted, fontSize: 10 } }],
        series: datasets.map((d, i) => ({
          name: seriesDefs[i].name, type: "line", showSymbol: false,
          data: zip(d.dates, d.close),
          lineStyle: { color: colors[i], width: 1.5 }, itemStyle: { color: colors[i] },
        })),
      };
    };
  }

  function annualChart(dsName) {
    return async (p) => {
      const d = await load(dsName);
      return {
        tooltip: tip(p, { axisPointer: { type: "shadow" } }),
        grid: { left: 54, right: 20, top: 24, bottom: 28 },
        xAxis: Object.assign({ type: "category", data: d.years, splitLine: { show: false } }, baseAxis(p)),
        yAxis: Object.assign({ type: "value", axisLabel: { formatter: "{value}%", color: p.muted, fontFamily: "JetBrains Mono", fontSize: 11 } }, baseAxis(p)),
        series: [{
          type: "bar", data: d.returns.map((v) => ({
            value: v, itemStyle: { color: v >= 0 ? p.moss : p.danger },
          })),
          barCategoryGap: "25%",
        }],
      };
    };
  }

  function ddChart(dsName) {
    return async (p) => {
      const d = await load(dsName);
      return {
        tooltip: tip(p),
        grid: { left: 54, right: 20, top: 20, bottom: 60 },
        dataZoom: [{ type: "inside" }, { type: "slider", bottom: 6, height: 18,
          borderColor: p.border, fillerColor: "rgba(160,57,47,0.08)",
          handleStyle: { color: p.accent }, textStyle: { color: p.muted, fontSize: 10 } }],
        xAxis: timeX(p),
        yAxis: Object.assign({ type: "value", max: 0, axisLabel: { formatter: "{value}%", color: p.muted, fontFamily: "JetBrains Mono", fontSize: 11 } }, baseAxis(p)),
        series: [{
          name: "距前高", type: "line", showSymbol: false, data: zip(d.dates, d.dd),
          lineStyle: { color: p.accent, width: 1 },
          areaStyle: { color: p.accent, opacity: 0.25 }, itemStyle: { color: p.accent },
        }],
      };
    };
  }

  async function renderDDTable(dsName, tableId) {
    const d = await load(dsName);
    const top = d.episodes.slice(0, 10);
    const _host1 = document.getElementById(tableId);
    if (!_host1) return;  // §63 瘦身路由页：该面板不在本页，跳过填充
    _host1.innerHTML =
      "<tr><th>峰值</th><th>谷底</th><th>深度</th><th>下跌(天)</th><th>修复(天)</th></tr>" +
      top.map((e) =>
        `<tr><td>${e.peak}</td><td>${e.trough}</td><td class="neg">${e.depth}%</td>` +
        `<td>${e.days_down}</td><td>${e.days_recover == null ? "未修复" : e.days_recover}</td></tr>`
      ).join("");
  }

  function intraChart(dsName) {
    return async (p) => {
      const d = await load(dsName);
      const years = d.rows.map((r) => r.year);
      return {
        tooltip: tip(p, { axisPointer: { type: "shadow" } }),
        legend: { textStyle: { color: p.muted, fontSize: 11 }, top: 0 },
        grid: { left: 54, right: 20, top: 30, bottom: 28 },
        xAxis: Object.assign({ type: "category", data: years, splitLine: { show: false } }, baseAxis(p)),
        yAxis: Object.assign({ type: "value", axisLabel: { formatter: "{value}%", color: p.muted, fontFamily: "JetBrains Mono", fontSize: 11 } }, baseAxis(p)),
        series: [
          { name: "全年收益", type: "bar", color: p.moss,
            data: d.rows.map((r) => ({ value: r.ret, itemStyle: { color: r.ret >= 0 ? p.moss : p.danger, opacity: 0.85 } })) },
          { name: "年内最大回撤", type: "scatter", symbolSize: 5,
            data: d.rows.map((r) => r.intra_dd), itemStyle: { color: p.accentDeep } },
        ],
      };
    };
  }

  function volChart(dsName) {
    return async (p) => {
      const d = await load(dsName);
      const series = [{
        name: "已实现波动率(20d)", type: "line", showSymbol: false,
        data: zip(d.dates, d.vol20), lineStyle: { color: p.teal, width: 1 }, itemStyle: { color: p.teal },
      }];
      if (d.vol60) series.push({
        name: "已实现波动率(60d)", type: "line", showSymbol: false,
        data: zip(d.dates, d.vol60), lineStyle: { color: p.moss, width: 1.1 }, itemStyle: { color: p.moss },
      });
      if (d.vol_index) series.push({
        name: d.vol_index_name, type: "line", showSymbol: false,
        data: zip(d.dates, d.vol_index), lineStyle: { color: p.accent, width: 1 }, itemStyle: { color: p.accent },
        markLine: { silent: true, symbol: "none",
          lineStyle: { color: p.danger, type: "dashed", width: 1.2 },
          label: { color: p.danger, formatter: d.vol_index_name + " = 30 · 保费警戒线", fontFamily: "JetBrains Mono", fontSize: 10 },
          data: [{ yAxis: 30 }] },
      });
      return {
        tooltip: tip(p),
        legend: { textStyle: { color: p.muted, fontSize: 11 }, top: 0 },
        grid: { left: 54, right: 20, top: 30, bottom: 28 },
        xAxis: timeX(p),
        yAxis: Object.assign({ type: "value" }, baseAxis(p)),
        series,
      };
    };
  }

  function rollChart(dsName) {
    return async (p) => {
      const d = await load(dsName);
      return {
        tooltip: tip(p),
        grid: { left: 54, right: 20, top: 20, bottom: 28 },
        xAxis: timeX(p),
        yAxis: Object.assign({ type: "value", axisLabel: { formatter: "{value}%", color: p.muted, fontFamily: "JetBrains Mono", fontSize: 11 } }, baseAxis(p)),
        series: [{
          name: "滚动5年年化", type: "line", showSymbol: false, data: zip(d.dates, d.cagr),
          lineStyle: { color: p.blue, width: 1.3 }, itemStyle: { color: p.blue },
          markLine: { silent: true, symbol: "none", lineStyle: { color: p.ink, type: "dashed" },
            label: { color: p.muted, formatter: "0%" }, data: [{ yAxis: 0 }] },
        }],
      };
    };
  }

  function seasonChart(dsName) {
    return async (p) => {
      const d = await load(dsName);
      const months = ["1月","2月","3月","4月","5月","6月","7月","8月","9月","10月","11月","12月"];
      return {
        tooltip: tip(p, { axisPointer: { type: "shadow" } }),
        legend: { textStyle: { color: p.muted, fontSize: 11 }, top: 0 },
        grid: { left: 54, right: 50, top: 30, bottom: 28 },
        xAxis: Object.assign({ type: "category", data: months, splitLine: { show: false } }, baseAxis(p)),
        yAxis: [
          Object.assign({ type: "value", name: "平均涨跌", axisLabel: { formatter: "{value}%", color: p.muted, fontFamily: "JetBrains Mono", fontSize: 11 } }, baseAxis(p)),
          Object.assign({ type: "value", name: "上涨概率", min: 0, max: 100, position: "right", splitLine: { show: false },
            axisLabel: { formatter: "{value}%", color: p.muted, fontFamily: "JetBrains Mono", fontSize: 11 } }, baseAxis(p)),
        ],
        series: [
          { name: "平均涨跌", type: "bar", color: p.moss,
            data: d.rows.map((r) => ({ value: r.avg, itemStyle: { color: r.avg >= 0 ? p.moss : p.danger } })) },
          { name: "上涨概率", type: "line", yAxisIndex: 1, symbolSize: 5,
            data: d.rows.map((r) => r.win), lineStyle: { color: p.gold, width: 1.4 }, itemStyle: { color: p.gold } },
        ],
      };
    };
  }

  function capeChart() {
    return async (p) => {
      const d = await load("sp500_cape");
      const avg = d.cape.reduce((a, b) => a + b, 0) / d.cape.length;
      return {
        tooltip: tip(p),
        grid: { left: 54, right: 20, top: 20, bottom: 28 },
        xAxis: timeX(p),
        yAxis: Object.assign({ type: "value" }, baseAxis(p)),
        series: [{
          name: "CAPE", type: "line", showSymbol: false, data: zip(d.dates, d.cape),
          lineStyle: { color: p.gold, width: 1.3 }, itemStyle: { color: p.gold },
          markLine: { silent: true, symbol: "none",
            lineStyle: { color: p.ink, type: "dashed" },
            label: { color: p.muted, formatter: "历史均值 " + avg.toFixed(1), fontFamily: "JetBrains Mono" },
            data: [{ yAxis: avg }] },
        }],
      };
    };
  }

  // ---------------- 个股篮子 ----------------
  function basketPalette(p) {
    return [p.blue, p.accent, p.gold, p.moss, p.teal, p.danger,
            "#7D6A9E", "#5E7C8A", "#A8783C", p.accentDeep];
  }

  function basketGrowthChart(prefix) {
    return async (p) => {
      const d = await load(prefix + "_growth");
      const colors = basketPalette(p);
      const series = d.series.map((s, i) => ({
        name: s.name, type: "line", showSymbol: false,
        data: zip(d.dates, s.values),
        lineStyle: { color: colors[i % colors.length], width: 1.1 },
        itemStyle: { color: colors[i % colors.length] },
      }));
      series.push({
        name: "等权组合", type: "line", showSymbol: false,
        data: zip(d.ew.dates, d.ew.values),
        lineStyle: { color: p.ink, width: 2.4 }, itemStyle: { color: p.ink },
        z: 10,
      });
      return {
        tooltip: tip(p),
        legend: { textStyle: { color: p.muted, fontSize: 11 }, top: 0, type: "scroll" },
        grid: { left: 64, right: 24, top: 36, bottom: 60 },
        xAxis: timeX(p),
        yAxis: Object.assign({ type: "log" }, baseAxis(p)),
        dataZoom: [{ type: "inside" }, { type: "slider", bottom: 6, height: 18,
          borderColor: p.border, fillerColor: "rgba(160,57,47,0.08)",
          handleStyle: { color: p.accent }, textStyle: { color: p.muted, fontSize: 10 } }],
        series,
      };
    };
  }

  // 七巨头等权指数（2026-07-20）：等权指数粗线 + 7 成员细线，对数坐标。
  // 复用 basketGrowthChart 的模式，但数据形状是 {index, members}，且突出指数线。
  function mag7Chart() {
    return async (p) => {
      const d = await load("m7_index");
      const colors = basketPalette(p);
      const T = (x) => (window.MC_I18N ? MC_I18N.translate(x) : x);
      const series = d.members.map((m, i) => ({
        // 成员名走 translate（数据文件里是中文，EN 态直接拼会泄漏）
        name: `${T(m.name)} ${m.mult}×`, type: "line", showSymbol: false,
        data: zip(d.dates, m.values),
        lineStyle: { color: colors[i % colors.length], width: 1.1 },
        itemStyle: { color: colors[i % colors.length] },
      }));
      series.push({
        name: `${T("七巨头等权指数")} ${d.index_mult}×`, type: "line", showSymbol: false,
        data: zip(d.index.dates, d.index.values),
        lineStyle: { color: p.ink, width: 2.6 }, itemStyle: { color: p.ink }, z: 10,
      });
      return {
        tooltip: tip(p),
        legend: { textStyle: { color: p.muted, fontSize: 11 }, top: 0, type: "scroll" },
        grid: { left: 64, right: 24, top: 36, bottom: 60 },
        xAxis: timeX(p),
        yAxis: Object.assign({ type: "log" }, baseAxis(p)),
        dataZoom: [{ type: "inside" }, { type: "slider", bottom: 6, height: 18,
          borderColor: p.border, fillerColor: "rgba(160,57,47,0.08)",
          handleStyle: { color: p.accent }, textStyle: { color: p.muted, fontSize: 10 } }],
        series,
      };
    };
  }

  async function renderBasketTable(prefix, tableId) {
    const d = await load(prefix + "_table");
    const c = (v, suffix) => v == null ? "<td>--</td>" :
      `<td class="${v >= 0 ? "pos" : "neg"}">${(v > 0 ? "+" : "") + v.toFixed(1)}${suffix}</td>`;
    const tbl = document.getElementById(tableId);
    if (!tbl) return;  // §63 瘦身路由页：该板块面板不在本页
    tbl.innerHTML =
      '<tr><th>代码</th><th class="left has-logo">名称</th><th>市值 ($B)</th><th>YTD</th><th>1年</th><th>3年年化</th><th>5年年化</th><th>10年年化</th><th>共同起点年化</th><th>最大回撤</th></tr>' +
      d.rows.map((r) =>
        `<tr class="clickable" data-stock="${r.safe}"><td>${r.ticker}</td>` +
        `<td class="left-col">${tblLogo(r.ticker)}${r.name}</td>` +
        `<td>${r.mcap ? Math.round(r.mcap).toLocaleString("en-US") : "--"}</td>` +
        c(r.ytd, "%") + c(r.y1, "%") + c(r.y3, "%") + c(r.y5, "%") + c(r.y10, "%") + c(r.since, "%") +
        `<td class="neg">${r.max_dd}%</td></tr>`
      ).join("");
    tbl.addEventListener("click", (e) => {
      const tr = e.target.closest("tr.clickable");
      if (tr) navigate(prefix, tr.dataset.stock);   // tr 不是链接，走不到链接委托
    });
  }

  // ---------------- 扩容章节构建器 ----------------
  function distChart(dsName) {
    return async (p) => {
      const d = await load(dsName);
      return {
        tooltip: tip(p, {
          axisPointer: { type: "shadow" },
          formatter: (params) => {
            const b = d.buckets[params[0].dataIndex];
            const yrs = [];
            for (let i = 0; i < b.years.length; i += 6) yrs.push(b.years.slice(i, i + 6).join(" "));
            // 函数体内的中文走 translate（JSON.stringify(getOption()) 扫不进函数体）
            const T = (s) => (window.MC_I18N ? MC_I18N.translate(s) : s);
            return `<b>${b.label}</b> · ${T(`${b.count} 年`)}<br/>${yrs.join("<br/>") || "--"}`;
          },
        }),
        grid: { left: 54, right: 20, top: 24, bottom: 28 },
        xAxis: Object.assign({ type: "category", data: d.buckets.map((b) => b.label), splitLine: { show: false } }, baseAxis(p)),
        yAxis: Object.assign({ type: "value", name: "年数" }, baseAxis(p)),
        series: [{
          type: "bar", barCategoryGap: "20%",
          data: d.buckets.map((b, i) => ({ value: b.count, itemStyle: { color: i < 4 ? p.danger : p.moss } })),
        }],
      };
    };
  }

  function holdingChart(dsName) {
    return async (p) => {
      const d = await load(dsName);
      return {
        tooltip: tip(p, {
          axisPointer: { type: "shadow" },
          formatter: (params) => {
            const r = d.rows[params[0].dataIndex];
            // 函数体内的中文走 translate（JSON.stringify(getOption()) 扫不进函数体）
            const T = (s) => (window.MC_I18N ? MC_I18N.translate(s) : s);
            return `${T(`持有 ${r.years} 年`)}<br/>${T(`胜率 ${r.win}%`)}<br/>`
              + T(`年化中位 ${r.median}% · 最差 ${r.worst}% · 最好 ${r.best}%`);
          },
        }),
        grid: { left: 54, right: 20, top: 24, bottom: 28 },
        xAxis: Object.assign({ type: "category", data: d.rows.map((r) => "持有" + r.years + "年"), splitLine: { show: false } }, baseAxis(p)),
        yAxis: Object.assign({ type: "value", max: 100, axisLabel: { formatter: "{value}%", color: p.muted, fontFamily: "JetBrains Mono", fontSize: 11 } }, baseAxis(p)),
        series: [{ type: "bar", barCategoryGap: "30%",
          data: d.rows.map((r) => ({ value: r.win, itemStyle: { color: p.moss } })),
          label: { show: true, position: "top", color: p.inkSoft, fontFamily: "JetBrains Mono", fontSize: 11, formatter: "{c}%" } }],
      };
    };
  }

  async function renderHoldingTable(dsName, tableId) {
    const d = await load(dsName);
    const f = (v) => `<td class="${v >= 0 ? "pos" : "neg"}">${(v > 0 ? "+" : "") + v.toFixed(1)}%</td>`;
    const _host2 = document.getElementById(tableId);
    if (!_host2) return;  // §63 瘦身路由页：该面板不在本页，跳过填充
    _host2.innerHTML =
      "<tr><th>持有期</th><th>胜率</th><th>年化中位</th><th>最差年化</th><th>最好年化</th><th>样本</th></tr>" +
      d.rows.map((r) =>
        `<tr><td>${r.years} 年</td><td>${r.win}%</td>` + f(r.median) + f(r.worst) + f(r.best) +
        `<td>${r.samples}</td></tr>`).join("");
  }

  function rollMatrixChart(dsName) {
    return async (p) => {
      const d = await load(dsName);
      const defs = [["cagr5", "5 年", p.blue], ["cagr10", "10 年", p.gold], ["cagr20", "20 年", p.accent]];
      return {
        tooltip: tip(p),
        legend: { textStyle: { color: p.muted, fontSize: 11 }, top: 0 },
        grid: { left: 54, right: 20, top: 30, bottom: 60 },
        dataZoom: [{ type: "inside" }, { type: "slider", bottom: 6, height: 18,
          borderColor: p.border, fillerColor: "rgba(160,57,47,0.08)",
          handleStyle: { color: p.accent }, textStyle: { color: p.muted, fontSize: 10 } }],
        xAxis: timeX(p),
        yAxis: Object.assign({ type: "value", axisLabel: { formatter: "{value}%", color: p.muted, fontFamily: "JetBrains Mono", fontSize: 11 } }, baseAxis(p)),
        series: defs.filter(([k]) => d[k]).map(([k, n, c]) => ({
          name: n + "年化", type: "line", showSymbol: false, data: zip(d.dates, d[k]),
          lineStyle: { color: c, width: 1.3 }, itemStyle: { color: c },
        })).concat([{
          name: "零线", type: "line", showSymbol: false, data: [],
          markLine: { silent: true, symbol: "none", lineStyle: { color: p.ink, type: "dashed" },
            label: { show: false }, data: [{ yAxis: 0 }] },
        }]),
      };
    };
  }

  async function renderBullBearTable(dsName, tableId) {
    const d = await load(dsName);
    const _host3 = document.getElementById(tableId);
    if (!_host3) return;  // §63 瘦身路由页：该面板不在本页，跳过填充
    _host3.innerHTML =
      "<tr><th>阶段</th><th>起点</th><th>终点</th><th>涨跌</th><th>历时(天)</th></tr>" +
      d.cycles.slice().reverse().map((c) =>
        `<tr><td>${c.kind === "bull" ? "🐂 牛" : "🐻 熊"}</td><td>${c.start}</td><td>${c.end || "进行中"}</td>` +
        `<td class="${c.ret >= 0 ? "pos" : "neg"}">${(c.ret > 0 ? "+" : "") + c.ret}%</td><td>${c.days}</td></tr>`).join("");
  }

  // 牛熊周期山峦图（2026-07-20 重做）：每一段画的是那段时间的**真实价格路径**，
  // 不是一根柱——牛市累计涨幅向上（绿色山头）、熊市累计跌幅向下（红色山谷），归零在每段交界。
  // 为什么牛市对数压缩：实测标普牛市最大 +582%、纳指 +787%，熊市最深 −61.8%；同一线性轴上
  // 熊市会被压成看不见的一条，而「熊有多深」正是这张图的另一半。∴ 牛市 y=log10(1+r) 压缩、
  // 熊市 y=r 原样；每段在极值处标注真实数字（年月 / 月数 / 总涨跌 / 年化），两种刻度并存但
  // 副标题明写。价格取自 century 全史（标普 24752 天 / 纳指 10277 天）。
  const BULL_LOG_K = 68;   // 让最大牛市(+787%)的山头≈最深熊市(−61.8%)的谷底，两侧视觉平衡
  function bullBearChart(cycleDs, priceDs) {
    return async (p) => {
      const [d, pxRaw] = await Promise.all([load(cycleDs), load(priceDs)]);
      const cs = d.cycles;
      // 月线重采样：century 是日线（标普 24752 天），直接画又密又抖、熊市被拉成红钢针。
      // 每月取最后一个交易日 → ~1184 点，曲线平滑成真正的「山峦」，也让标注不再糊成一团。
      const mDates = [], mClose = [];
      let lastYm = "";
      for (let i = 0; i < pxRaw.dates.length; i++) {
        const ym = pxRaw.dates[i].slice(0, 7);
        if (ym !== lastYm) { mDates.push(pxRaw.dates[i]); mClose.push(pxRaw.close[i]); lastYm = ym; }
        else { mDates[mDates.length - 1] = pxRaw.dates[i]; mClose[mDates.length - 1] = pxRaw.close[i]; }
      }
      const px = { dates: mDates, close: mClose };
      const pxMap = Object.create(null);
      px.dates.forEach((dt, i) => { pxMap[dt] = px.close[i]; });
      // 日线查表（给短段兜底：月线下不足 2 点的段——如 2020 疫情熊市 33 天——退回日线画）
      const dayMap = Object.create(null);
      pxRaw.dates.forEach((dt, i) => { dayMap[dt] = pxRaw.close[i]; });
      const dayDates = pxRaw.dates;
      // 取某段的采样日期序列：优先月线，不足 2 点则用日线
      const segDatesOf = (start, end) => {
        const m = px.dates.filter((dt) => dt >= start && dt <= end);
        if (m.length >= 2) return { dates: m, map: pxMap };
        return { dates: dayDates.filter((dt) => dt >= start && dt <= end), map: dayMap };
      };
      const yPlot = (kind, r) => (kind === "bull"
        ? Math.log10(1 + r) * BULL_LOG_K   // 牛：累计涨幅对数压缩，向上
        : r * 100);                         // 熊：累计跌幅百分比原样，向下（r<0）

      // ⭐ x 轴 = 真实时间（月），宽度正比于持续时间：长牛市宽、短熊市窄——
      // 参考图正是这样（牛市是宽绿山、熊市是窄红尖）。x = 距全史起点的月数（含日内小数）。
      const ym2abs = (s) => { const [y, m] = s.split("-").map(Number); return y * 12 + (m - 1); };
      const gAbs = ym2abs(px.dates[0]);
      const xOf = (s) => { const [y, m, dd] = s.split("-").map(Number); return (y * 12 + (m - 1)) - gAbs + ((dd || 1) - 1) / 30.44; };
      const bullLine = [], bearLine = [], marks = [], segRanges = [];
      cs.forEach((c) => {
        const endDate = c.end || px.dates[px.dates.length - 1];
        // 切出本段的采样序列（月线优先，短段退日线）
        const { dates: seg, map: segMap } = segDatesOf(c.start, endDate);
        if (seg.length < 2) return;
        const base = segMap[seg[0]];
        const tgt = c.kind === "bull" ? bullLine : bearLine;
        const other = c.kind === "bull" ? bearLine : bullLine;
        let extreme = { y: 0, gx: xOf(seg[0]), r: 0 };
        seg.forEach((dt) => {
          const gx = xOf(dt);                 // 真实时间：距起点月数
          const r = segMap[dt] / base - 1;    // 累计涨跌（相对本段起点）
          const y = yPlot(c.kind, r);
          tgt.push([gx, y]);
          other.push([gx, null]);            // 对方 series 在这段留空
          if (Math.abs(y) >= Math.abs(extreme.y)) extreme = { y, gx, r };
        });
        const xEnd = xOf(endDate);
        segRanges.push({ x0: xOf(seg[0]), x1: xEnd, kind: c.kind });
        bullLine.push([xEnd + 0.01, null]); bearLine.push([xEnd + 0.01, null]);  // 段间断开
        // 标注只给「够大的段」：小段曲线照画、但不加文字，否则 50 个三行标注糊成一团。
        // 阈值取 |ret|>=33%（让 2020 疫情熊 −34%/33天 这种标志性短熊也进来）或 >=1 年。
        if (c.days < 365 && Math.abs(c.ret) < 33) return;
        const months = Math.round(c.days / 30.44);
        const yrsExact = c.days / 365.25;
        const annPct = Math.round(c.ret);
        const annYr = Math.round(((1 + c.ret / 100) ** (1 / Math.max(yrsExact, 0.25)) - 1) * 100);
        marks.push({
          coord: [extreme.gx, extreme.y],
          kind: c.kind, x: extreme.gx, absRet: Math.abs(c.ret),
          l1: `${c.start.slice(0, 4)}.${c.start.slice(5, 7)}`,
          l2: `${months}m · ${annPct > 0 ? "+" : ""}${annPct}%`,
          l3: `${annYr > 0 ? "+" : ""}${annYr}%/yr`,
        });
      });

      // 最小间距去重：同色标注若 x 挨得太近（早期大萧条一段十年八个周期），
      // 只留较大的那个——但 |ret|>=50% 的重大事件永远保留（1929/1987/2008…）。
      const declutter = (ms) => {
        const sorted = ms.slice().sort((a, b) => a.x - b.x);
        const kept = [];
        const MIN_GAP = 42;   // 月：约 3.5 年，够放下三行标注不打架
        for (const m of sorted) {
          const near = kept.find((k) => Math.abs(k.x - m.x) < MIN_GAP);
          if (!near) { kept.push(m); continue; }
          if (m.absRet >= 50 && m.absRet > near.absRet) {   // 重大事件挤掉较小的邻居
            kept[kept.indexOf(near)] = m;
          }
        }
        return kept;
      };

      // 牛熊分色：用两条独立 line（牛一条、熊一条），互相在对方的段上填 null。
      // ⚠️ 不用 visualMap piecewise 分色——本版 ECharts 对上万点的 line 配 piecewise
      //    visualMap 会崩（"Cannot read properties of undefined (reading 'coord')"），
      //    2026-07-20 逐项二分实测确认是 visualMap 单独致命，与 null 断点无关。
      //    两条 series 各带 markPoint（分开就不会跨段反查 coord 崩）。
      const T = (s) => (window.MC_I18N ? MC_I18N.translate(s) : s);
      const bullMarks = declutter(marks.filter((m) => m.kind === "bull"));
      const bearMarks = declutter(marks.filter((m) => m.kind === "bear"));
      const mkSeries = (data, color, ms, pos) => ({
        type: "line", data, showSymbol: false, connectNulls: false,
        lineStyle: { color, width: 1.4 }, areaStyle: { color, opacity: 0.13 },
        markPoint: {
          symbol: "circle", symbolSize: 1, silent: true,
          data: ms.map((m) => ({
            coord: m.coord, itemStyle: { color: "transparent" },
            label: {
              show: true, position: pos, formatter: `${m.l1}\n${m.l2}\n${m.l3}`,
              fontSize: 9, lineHeight: 12, color, fontFamily: "JetBrains Mono",
            },
          })),
        },
      });
      // tooltip 按真实 x（月数）落在哪一段判牛熊；x 轴按真实时间标年份
      const gEnd = px.dates[px.dates.length - 1];
      const xMax = xOf(gEnd);
      const startYear = +px.dates[0].slice(0, 4);
      const step = (+gEnd.slice(0, 4) - startYear) > 60 ? 16 : 8;  // 长史每 16 年一个刻度
      return {
        tooltip: tip(p, {
          trigger: "axis",
          formatter: (ps) => {
            const x = ps[0].data[0];
            const seg = segRanges.find((s) => x >= s.x0 && x <= s.x1);
            return seg ? T(seg.kind === "bull" ? "牛市" : "熊市") : "";
          },
        }),
        grid: { left: 46, right: 20, top: 40, bottom: 64 },
        xAxis: Object.assign({}, baseAxis(p), {
          type: "value", min: 0, max: xMax,
          interval: step * 12,            // 每 step 年一个刻度（月数）
          axisLabel: {
            color: p.muted, fontSize: 10,
            formatter: (v) => String(Math.round(startYear + v / 12)),
          },
          splitLine: { show: false },
        }),
        yAxis: Object.assign({}, baseAxis(p), {
          type: "value",
          axisLabel: { show: false }, splitLine: { show: false },
          axisLine: { show: false }, axisTick: { show: false },
        }),
        dataZoom: [{ type: "inside" }, {
          type: "slider", bottom: 8, height: 16,
          borderColor: p.border, fillerColor: "rgba(160,57,47,0.08)",
          handleStyle: { color: p.accent },
        }],
        series: [
          mkSeries(bullLine, p.moss, bullMarks, "top"),
          mkSeries(bearLine, p.danger, bearMarks, "bottom"),
        ],
      };
    };
  }

  function dailyHistChart(dsName) {
    return async (p) => {
      const d = await load(dsName);
      return {
        tooltip: tip(p, { axisPointer: { type: "shadow" } }),
        grid: { left: 60, right: 20, top: 24, bottom: 28 },
        xAxis: Object.assign({ type: "category", data: d.hist.map((h) => h.label), splitLine: { show: false } }, baseAxis(p)),
        yAxis: Object.assign({ type: "log", name: "天数(log)" }, baseAxis(p)),
        series: [{ type: "bar", barCategoryGap: "20%",
          data: d.hist.map((h, i) => ({ value: h.count || null, itemStyle: { color: i < 5 ? p.danger : p.moss } })) }],
      };
    };
  }

  async function renderExtremesTable(dsName, tableId) {
    const d = await load(dsName);
    const _host4 = document.getElementById(tableId);
    if (!_host4) return;  // §63 瘦身路由页：该面板不在本页，跳过填充
    _host4.innerHTML =
      "<tr><th>#</th><th>最差单日</th><th>跌幅</th><th>最好单日</th><th>涨幅</th></tr>" +
      d.worst.map((w, i) => {
        const b = d.best[i];
        return `<tr><td>${i + 1}</td><td>${w.date}</td><td class="neg">${w.ret}%</td>` +
               `<td>${b.date}</td><td class="pos">+${b.ret}%</td></tr>`;
      }).join("");
  }

  function simpleLine(dsName, name, colorKey, opts) {
    opts = opts || {};
    return async (p) => {
      const d = await load(dsName);
      const series = {
        name, type: "line", showSymbol: false, data: zip(d.dates, d.values),
        lineStyle: { color: p[colorKey], width: 1.3 }, itemStyle: { color: p[colorKey] },
      };
      if (opts.avgLine) {
        const avg = d.values.reduce((a, b) => a + b, 0) / d.values.length;
        series.markLine = { silent: true, symbol: "none", lineStyle: { color: p.ink, type: "dashed" },
          label: { color: p.muted, formatter: "均值 " + avg.toFixed(1), fontFamily: "JetBrains Mono" },
          data: [{ yAxis: avg }] };
      }
      return {
        tooltip: tip(p),
        grid: { left: 58, right: 20, top: 20, bottom: 28 },
        xAxis: timeX(p),
        yAxis: Object.assign({ type: opts.log ? "log" : "value" }, baseAxis(p)),
        series: [series],
      };
    };
  }

  function sectorChart(dsName) {
    return async (p) => {
      const d = await load(dsName);
      const s = d.sectors.slice().reverse();
      return {
        tooltip: tip(p, { axisPointer: { type: "shadow" } }),
        grid: { left: 150, right: 40, top: 10, bottom: 28 },
        xAxis: Object.assign({ type: "value", name: "家数" }, baseAxis(p)),
        yAxis: Object.assign({ type: "category", data: s.map((x) => x.sector), splitLine: { show: false },
          axisLabel: { color: p.inkSoft, fontSize: 11 } }, baseAxis(p)),
        series: [{ type: "bar", barCategoryGap: "30%",
          data: s.map((x) => ({ value: x.count, itemStyle: { color: p.teal } })),
          label: { show: true, position: "right", color: p.muted, fontFamily: "JetBrains Mono", fontSize: 11 } }],
      };
    };
  }

  // 行业暴露（按权重）：环形图，一眼看出集中度。头部行业（≥25%）猩红、其余按大小
  // 由深到浅的绿；中心留空放「头部行业 + 占比」。饼图读准确值不如横条，但看「一家独大」更直观。
  function sectorWeightChart(dsName) {
    return async (p) => {
      const d = await load(dsName);
      const T = (x) => (window.MC_I18N ? MC_I18N.translate(x) : x);
      const rows = d.rows.slice().sort((a, b) => b.weight - a.weight);
      const top = rows[0];
      // 绿色梯度：最大的深、往后渐浅；≥25% 的头部用猩红
      const greens = ["#1f7a4d", "#2e9d63", "#43b878", "#6cc796", "#96d6b4", "#bfe4d1", "#d8eee2"];
      return {
        tooltip: tip(p, { trigger: "item",
          formatter: (o) => `${o.name}<br/><b>${o.value}%</b>` }),
        // 中心大数字=头部行业；每块环外引线标「行业名 权重%」（去掉图例，信息全在图上）
        graphic: [{ type: "text", left: "center", top: "40%", style: {
          text: `{big|${top.weight}%}\n{small|${T(top.name)}}`,
          textAlign: "center",
          rich: {
            big: { fontSize: 22, fontWeight: 700, lineHeight: 26, fill: top.weight >= 25 ? p.danger : p.ink },
            small: { fontSize: 10, lineHeight: 15, fill: p.muted },
          },
        } }],
        series: [{
          type: "pie", radius: ["34%", "52%"], center: ["50%", "48%"],
          avoidLabelOverlap: true, minAngle: 3,
          label: { show: true, color: p.inkSoft, fontSize: 10,
            formatter: (o) => `${o.name} ${o.value}%` },
          labelLine: { show: true, length: 8, length2: 12, lineStyle: { color: p.border } },
          data: rows.map((x, i) => ({ name: x.name, value: x.weight,
            itemStyle: { color: x.weight >= 25 ? p.danger : greens[Math.min(i, greens.length - 1)] } })),
        }],
      };
    };
  }

  const tblLogo = (ticker) =>
    `<img class="tbl-logo" loading="lazy" data-t="${ticker}" src="logos/${safeTicker(ticker)}.png" referrerpolicy="no-referrer" onerror="__logoErr(this)" alt="">`;

  // renderTopTable 已随两张「前二十大持仓」卡下线删除（2026-07-20）

  async function renderConstituents(dsName, tableId) {
    const d = await load(dsName);
    const hasAdded = d.rows[0] && d.rows[0].added !== undefined;
    const _host5 = document.getElementById(tableId);
    if (!_host5) return;  // §63 瘦身路由页：该面板不在本页，跳过填充
    _host5.innerHTML =
      `<tr><th>#</th><th>代码</th><th class="left center-col">公司</th><th class="left center-col">${hasAdded ? "GICS 行业" : "行业"}</th>${hasAdded ? "<th>纳入日期</th>" : ""}</tr>` +
      d.rows.map((r, i) =>
        `<tr><td>${i + 1}</td><td>${r.ticker}</td><td class="center-col"><span class="co-wrap">${tblLogo(r.ticker)}${r.name}</span></td>` +
        `<td class="center-col" style="font-family:'Noto Sans SC',sans-serif">${r.sector}</td>` +
        (hasAdded ? `<td>${r.added || "--"}</td>` : "") + "</tr>").join("");
  }

  // ---------------- 估值的弹性（SPY） ----------------
  const median = (xs) => {
    const s = xs.filter((v) => v != null).slice().sort((a, b) => a - b);
    return s[Math.floor(s.length / 2)];
  };
  const percentile = (xs, cur) => {
    const s = xs.filter((v) => v != null);
    return Math.round(s.filter((v) => v <= cur).length / s.length * 1000) / 10;
  };

  async function spyValStats() {
    const pe = await load("sp500_pe_ttm");
    const now = pe.dates[pe.dates.length - 1];
    const y50 = (parseInt(now.slice(0, 4)) - 50) + now.slice(4);
    const sel = (from) => pe.values.filter((v, i) => pe.dates[i] >= from);
    return {
      cur: pe.values[pe.values.length - 1],
      medAll: median(pe.values), med50: median(sel(y50)), med2010: median(sel("2010")),
      pct: percentile(pe.values, pe.values[pe.values.length - 1]),
      since: pe.dates[0].slice(0, 4), asof: now,
    };
  }

  chart("spy", "ch-spy-val", async (p) => {
    const [pe, st] = await Promise.all([load("sp500_pe_ttm"), spyValStats()]);
    const ml = (v, label, color) => ({
      yAxis: v, lineStyle: { color, type: "dashed", width: 1.2 },
      label: { color, formatter: label + " " + v.toFixed(1), fontFamily: "JetBrains Mono", fontSize: 10 },
    });
    return {
      tooltip: tip(p),
      grid: { left: 54, right: 96, top: 20, bottom: 28 },
      xAxis: timeX(p),
      yAxis: Object.assign({ type: "value" }, baseAxis(p)),
      series: [{
        name: "PE(TTM)", type: "line", showSymbol: false, data: zip(pe.dates, pe.values),
        lineStyle: { color: p.accent, width: 1.2 }, itemStyle: { color: p.accent },
        markLine: { silent: true, symbol: "none", data: [
          ml(st.medAll, "全历史中位", p.ink),
          ml(st.med50, "近50年中位", p.gold),
          ml(st.med2010, "2010→中位", p.moss),
        ] },
      }],
    };
  });

  async function renderValCards() {
    try {
      const [st, cape, iv] = await Promise.all([spyValStats(), load("sp500_cape"), load("index_val")]);
      const curCape = cape.cape[cape.cape.length - 1];
      const capePct = percentile(cape.cape, curCape);
      const fwd = iv.SPY && iv.SPY.forward_pe;
      const _host6 = document.getElementById("spy-val-cards");
      if (!_host6) return;  // §63 瘦身路由页：该面板不在本页，跳过填充
      _host6.innerHTML = [
        ["当前 PE (TTM)", st.cur.toFixed(1), `全历史第 ${st.pct} 百分位`, st.pct > 90],
        ["席勒 CAPE", curCape.toFixed(1), `1871 年来第 ${capePct} 百分位`, capePct > 90],
        ["远期 PE (SPY 口径)", fwd ? fwd.toFixed(1) : "--", fwd ? "" : "免费源暂缺，参考 TTM", false],
        ["三条中位数锚", `${st.medAll.toFixed(0)} / ${st.med50.toFixed(0)} / ${st.med2010.toFixed(1)}`, "全历史 / 近50年 / 2010→", false],
      ].map(([l, v, n, hot]) =>
        `<div class="stat ${hot ? "signal-on" : ""} ${String(v).length > 8 ? "compact" : ""}"><div class="label">${l}</div><div class="value">${v}</div><div class="note">${n}</div></div>`).join("");
      document.getElementById("spy-val-note").textContent =
        `口径说明：PE(TTM) 与 CAPE 来自 multpl/席勒月度数据（${st.since} 年起），百分位为当前值在全部历史读数中的位置；` +
        `三条中位数是三个不同时代的"估值重力"：离哪条锚越远，弹性拉得越满。数据截至 ${st.asof}。`;
      const qv = iv.QQQ || {};
      const _host7 = document.getElementById("qqq-val-cards");
      if (!_host7) return;  // §63 瘦身路由页：该面板不在本页，跳过填充
      _host7.innerHTML = [
        ["QQQ PE (TTM · ETF 口径)", qv.trailing_pe ? qv.trailing_pe.toFixed(1) : "--", "持仓加权"],
        ["QQQ 远期 PE", qv.forward_pe ? qv.forward_pe.toFixed(1) : "--", qv.forward_pe ? "" : "免费源暂缺"],
        ["SPY PE 对照", iv.SPY && iv.SPY.trailing_pe ? iv.SPY.trailing_pe.toFixed(1) : "--", "同为 ETF 口径"],
      ].map(([l, v, n]) =>
        `<div class="stat"><div class="label">${l}</div><div class="value">${v}</div><div class="note">${n}</div></div>`).join("");
    } catch (e) { /* 数据缺失时留空 */ }
  }

  // ---------------- 宏观（FRED） ----------------
  function macroLines(keys, opts) {
    opts = opts || {};
    return async (p) => {
      const d = await load("macro");
      const colors = [p.cmpBlue, p.cmpRed, p.cmpGreen, p.cmpPurple];
      const series = keys.filter(([k]) => d[k]).map(([k, label], i) => ({
        name: label, type: opts.bar ? "bar" : "line", showSymbol: false,
        data: zip(d[k].dates, d[k].values),
        lineStyle: { color: colors[i % 4], width: 1.3 }, itemStyle: { color: colors[i % 4] },
        yAxisIndex: opts.dualAxis && i === keys.length - 1 ? 1 : 0,
      }));
      if (opts.zeroLine || opts.markValue != null) {
        series[0].markLine = { silent: true, symbol: "none",
          lineStyle: { color: p.danger, type: "dashed", width: 1.2 },
          label: { color: p.danger, formatter: opts.markLabel || "", fontFamily: "JetBrains Mono", fontSize: 10 },
          data: [{ yAxis: opts.markValue != null ? opts.markValue : 0 }] };
      }
      const yAxes = [Object.assign({ type: "value", name: opts.yName || "" }, baseAxis(p))];
      if (opts.dualAxis) yAxes.push(Object.assign({ type: "value", name: opts.y2Name || "", position: "right", splitLine: { show: false } }, baseAxis(p)));
      return {
        tooltip: tip(p),
        legend: keys.length > 1 ? { textStyle: { color: p.muted, fontSize: 11 }, top: 0 } : undefined,
        grid: { left: 56, right: opts.dualAxis ? 56 : 20, top: keys.length > 1 ? 30 : 20, bottom: 28 },
        xAxis: timeX(p),
        yAxis: yAxes,
        series,
      };
    };
  }

  function macroBars(key, label) {
    return async (p) => {
      const d = await load("macro");
      return {
        tooltip: tip(p, { axisPointer: { type: "shadow" } }),
        grid: { left: 56, right: 20, top: 20, bottom: 28 },
        xAxis: timeX(p),
        yAxis: Object.assign({ type: "value" }, baseAxis(p)),
        series: [{
          name: label, type: "bar", barWidth: "60%",
          data: d[key].dates.map((dt, i) => ({
            value: [dt, d[key].values[i]],
            itemStyle: { color: d[key].values[i] >= 0 ? p.moss : p.danger },
          })),
        }],
      };
    };
  }

  chart("macro", "ch-macro-rates", macroLines([["sofr", "SOFR"], ["effr", "EFFR"], ["target", "目标上限"]], { yName: "%" }));
  chart("macro", "ch-macro-rrp", macroLines([["rrp", "ON RRP"]], { yName: "$B" }));
  chart("macro", "ch-macro-walcl", macroLines([["walcl", "Fed 资产负债表"]], { yName: "$T" }));
  chart("macro", "ch-macro-yields", macroLines([["dgs2", "2Y"], ["dgs10", "10Y"], ["dgs20", "20Y"], ["dgs30", "30Y"]], { yName: "%" }));
  chart("macro", "ch-macro-curve", macroLines([["t10y2y", "10Y−2Y"]], { markValue: 0, markLabel: "倒挂线", yName: "%" }));
  chart("macro", "ch-macro-credit", macroLines([["hy_oas", "高收益 OAS"], ["ig_oas", "投资级 OAS"]], { yName: "%" }));
  chart("macro", "ch-macro-inflation", macroLines([["cpi_yoy", "CPI 同比"], ["core_pce_yoy", "核心 PCE 同比"], ["ppi_yoy", "PPI 同比"]], { markValue: 2, markLabel: "2% 目标", yName: "%" }));
  chart("macro", "ch-macro-gdp", macroBars("gdp_qoq", "GDP 环比年化"));
  chart("macro", "ch-macro-jobs", async (p) => {
    const d = await load("macro");
    return {
      tooltip: tip(p),
      legend: { textStyle: { color: p.muted, fontSize: 11 }, top: 0 },
      grid: { left: 56, right: 56, top: 30, bottom: 28 },
      xAxis: timeX(p),
      yAxis: [
        Object.assign({ type: "value", name: "千人" }, baseAxis(p)),
        Object.assign({ type: "value", name: "失业率%", position: "right", splitLine: { show: false } }, baseAxis(p)),
      ],
      series: [
        { name: "非农新增", type: "bar", barWidth: "60%",
          data: d.nfp.dates.map((dt, i) => ({
            value: [dt, d.nfp.values[i]],
            itemStyle: { color: d.nfp.values[i] >= 0 ? p.moss : p.danger },
          })) },
        { name: "失业率", type: "line", yAxisIndex: 1, showSymbol: false,
          data: zip(d.unrate.dates, d.unrate.values),
          lineStyle: { color: p.cmpRed, width: 1.4 }, itemStyle: { color: p.cmpRed } },
      ],
    };
  });
  chart("macro", "ch-macro-profits", macroBars("cp_yoy", "企业利润同比"));

  // ---------------- 今日 · 头版：板块涨跌（自有数据） ----------------
  // 2026-07-26 §45（用户裁）：撤下 TradingView 热力图 iframe，本图以自有数据补位。撤换四由：
  // ① CTA 之后不放全页唯一的交互玩具（转化设计最典型的漏）② 全站唯一不属于自己的组件，
  //    卖「可追责自有记录」的站把首页版面租给别人=叙事矛盾 ③ 热力图是全网最商品化的部件，
  //    这一格永远赢不了也不需要赢 ④ iframe 无 lazy、首屏外同步加载实测 1.2s+，移动端 500 块不可读。
  // 数据 = data/pulse_heatmap.json（管线每日收盘产出，[ticker, sector, 当日涨跌%, 市值] 503 行）。
  // 生命周期与旧 mountHeatmap 完全一致：renderPulse 每次重建 DOM 后调用（日夜/语言切换即重画）。
  // 数据实况：pulse_heatmap.json 用 GICS 标准 11 板块（已核 503 行全覆盖）。未命中一律原名显示，绝不猜。
  const SECTOR_ZH = {
    "Information Technology": "信息技术", "Health Care": "医疗保健", "Financials": "金融",
    "Consumer Discretionary": "可选消费", "Consumer Staples": "必需消费", "Industrials": "工业",
    "Energy": "能源", "Materials": "原材料", "Utilities": "公用事业", "Real Estate": "房地产",
    "Communication Services": "通信服务",
  };
  // 标准 build 契约（接收调色板，返回 option）→ 注册进 registry：
  // 实例由 buildOne 的 built Map 统一管理，于是自动继承全站三件事 ——
  // ① 日夜切换 rebuildAll 重画（取色随主题）② 语言切换重画 ③ 全局 resize（app.js:254）。
  // 自己 init + 自挂 resize 会绕开这套，主题切一次轴标签就留在旧色（07-26 实测栽过一次）。
  async function buildSectorOption(p) {
    const hm = await load("pulse_heatmap");   // 抛错由 buildOne 接住 → 显示「数据更新中」占位
    const agg = {};
    (hm.rows || []).forEach((r) => {
      const sec = r[1], chg = r[2];
      if (!agg[sec]) agg[sec] = { up: 0, down: 0 };
      if (chg > 0) agg[sec].up++; else if (chg < 0) agg[sec].down++;
    });
    // 升序排：ECharts 类目轴自下而上 → 涨家占比最高的板块画在最顶
    const secs = Object.keys(agg).sort((a, b) =>
      agg[a].up / ((agg[a].up + agg[a].down) || 1) - agg[b].up / ((agg[b].up + agg[b].down) || 1));
    // 语言自判而不交给 i18nOption：GICS 板块名没有 D 键，交出去 EN 下会留一排中文。
    // 返回值已是目标语言，i18nOption 再跑一遍无副作用（ZH 直接 return，EN 拿到英文不动）。
    const zh = !window.MC_I18N || MC_I18N.lang() === "zh";
    const lbl = (o) => (o.value > 0 ? o.value : "");
    return {
      grid: { left: 8, right: 34, top: 4, bottom: 4, containLabel: true },
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      xAxis: { type: "value", show: false },
      yAxis: { type: "category", data: secs.map((x) => (zh ? (SECTOR_ZH[x] || x) : x)),
        axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: p.inkSoft, fontSize: 12 } },
      series: [
        { name: zh ? "上涨" : "Advancers", type: "bar", stack: "b", barWidth: 15,
          data: secs.map((x) => agg[x].up), itemStyle: { color: p.moss },
          label: { show: true, position: "inside", fontSize: 10, color: "#fff", formatter: lbl } },
        { name: zh ? "下跌" : "Decliners", type: "bar", stack: "b", barWidth: 15,
          data: secs.map((x) => agg[x].down), itemStyle: { color: p.danger },
          label: { show: true, position: "inside", fontSize: 10, color: "#fff", formatter: lbl } },
      ],
    };
  }
  chart("pulse", "ch-breadth-sector", buildSectorOption);

  // ---------------- 今日 · 头版（聚光灯封面） ----------------
  // ---------------- 信号台账图表（头版紧凑版 + K/LEAPS 页缩放版共用） ----------------
  const ledgerZoom = (p) => [
    { type: "inside" },
    { type: "slider", bottom: 6, height: 18, borderColor: p.border,
      fillerColor: "rgba(160,57,47,0.08)", handleStyle: { color: p.accent },
      textStyle: { color: p.muted, fontSize: 10 } },
  ];

  // 落点图：纳指 100 对数线 + 每一次 LEAPS 窗口 / K<1 信号的入场点
  async function buildLedgerMap(p, opts) {
    const o = opts || {};
    const [lp, ks] = await Promise.all([load("leaps"), load("kindex_signals")]);
    const idx = new Map(lp.dates.map((d, i) => [d, i]));
    const at = (ds) => {
      let i = idx.has(ds) ? idx.get(ds) : lp.dates.findIndex((x) => x >= ds);
      return i == null || i < 0 ? null : [lp.dates[i], lp.ndx[i]];
    };
    const lPts = lp.episodes.map((e) => { const q = at(e.start); return q && { value: q, ep: e }; }).filter(Boolean);
    const kPts = ks.signals.map((s) => { const q = at(s.start); return q && { value: q, sg: s }; }).filter(Boolean);
    return {
      tooltip: tip(p, {
        trigger: "item",
        formatter: (o) => {
          if (o.data && o.data.ep) { const e = o.data.ep; return `${e.start}<br/>LEAPS 窗口 · 最低恐贪 ${e.min_fng}<br/>12 个月后 ${pct(e.ndx_m12)}（纳指 100）`; }
          if (o.data && o.data.sg) { const s = o.data.sg; return `${s.start}<br/>K &lt; 1 信号 · 最低 K ${s.min_k}<br/>60 个交易日后 ${pct(s.fwd60)}（纳指 100）`; }
          return "";
        },
      }),
      legend: { top: 0, left: 0, textStyle: { color: p.muted, fontSize: 11 }, itemWidth: 12, itemHeight: 8 },
      grid: { left: 52, right: 18, top: 34, bottom: o.zoom ? 60 : 28 },
      dataZoom: o.zoom ? ledgerZoom(p) : undefined,
      xAxis: timeX(p),
      yAxis: Object.assign({ type: "log", splitLine: { show: false } }, baseAxis(p)),
      series: [
        { name: "纳指 100", type: "line", data: zip(lp.dates, lp.ndx), showSymbol: false, silent: true,
          lineStyle: { color: p.muted, width: 1, opacity: 0.7 }, itemStyle: { color: p.muted } },
        { name: "LEAPS 窗口开启", type: "scatter", data: lPts, symbolSize: 9, itemStyle: { color: p.moss } },
        { name: "K < 1 信号", type: "scatter", data: kPts, symbol: "diamond", symbolSize: 14, itemStyle: { color: p.danger } },
      ],
    };
  }

  // LEAPS 净值序列（净值曲线与滚动矩阵共用）：每次窗口首日买入纳指 100 持有 12 个月，
  // 持有期内新窗口跳过、空仓期记零、不计成本
  async function leapsEquitySeries() {
    const lp = await load("leaps");
    const dates = lp.dates, px = lp.ndx, HOLD = 252;
    const idx = new Map(dates.map((d, i) => [d, i]));
    const at = (ds) => (idx.has(ds) ? idx.get(ds) : dates.findIndex((x) => x >= ds));
    const segs = [];
    let exit = -1;
    for (const e of lp.episodes) {
      const i = at(e.start);
      if (i == null || i < 0 || i <= exit) continue;
      // 离场日优先用管线在纯价格日历上算好的 m12_exit（恐贪存档缺日会让 +252 行偏长）
      let j = e.m12_exit ? at(e.m12_exit) : -1;
      if (j == null || j < 0) j = Math.min(i + HOLD, px.length - 1);
      segs.push([i, j]); exit = j;
    }
    if (!segs.length) throw new Error("no episodes");
    const first = segs[0][0];
    const spx = lp.spx || null;
    const out = { dates: [], strat: [], hold: [], holdSpx: spx ? [] : null };
    let eq = 1, si = 0;
    for (let t = first; t < dates.length; t++) {
      if (t > first) {
        while (si < segs.length && t > segs[si][1]) si++;
        if (si < segs.length && t > segs[si][0] && t <= segs[si][1]) eq *= px[t] / px[t - 1];
      }
      out.dates.push(dates[t]);
      out.strat.push(eq);
      out.hold.push(px[t] / px[first]);
      if (spx) out.holdSpx.push(spx[t] / spx[first]);
    }
    return out;
  }

  async function buildLedgerEq(p, opts) {
    const o = opts || {};
    const s = await leapsEquitySeries();
    const zip4 = (vals) => vals.map((v, i) => [s.dates[i], +v.toFixed(4)]);
    const strat = zip4(s.strat), hold = zip4(s.hold), holdSpx = s.holdSpx ? zip4(s.holdSpx) : null;
    const spx = holdSpx;
    const endLbl = (color) => ({ show: true, formatter: (o) => "×" + (+o.value[1]).toFixed(1),
      fontFamily: "JetBrains Mono", fontSize: 11, color });
    const series = [
      { name: "每次窗口都跟（持有 12 个月）", type: "line", data: strat, showSymbol: false,
        lineStyle: { color: p.cmpRed, width: 2.6 }, itemStyle: { color: p.cmpRed }, endLabel: endLbl(p.cmpRed) },
      { name: "一直持有纳指 100", type: "line", data: hold, showSymbol: false,
        lineStyle: { color: p.cmpPurple, width: 2, type: "dashed" }, itemStyle: { color: p.cmpPurple }, endLabel: endLbl(p.cmpPurple) },
    ];
    if (spx) series.push(
      { name: "一直持有标普 500", type: "line", data: holdSpx, showSymbol: false,
        lineStyle: { color: p.cmpBlue, width: 2, type: "dashed" }, itemStyle: { color: p.cmpBlue }, endLabel: endLbl(p.cmpBlue) });
    return {
      tooltip: tip(p, { valueFormatter: (v) => "×" + (+v).toFixed(2) }),
      legend: { top: 0, left: 0, textStyle: { color: p.muted, fontSize: 11 } },
      grid: { left: 52, right: 56, top: 56, bottom: o.zoom ? 60 : 28 },
      dataZoom: o.zoom ? ledgerZoom(p) : undefined,
      xAxis: timeX(p),
      yAxis: Object.assign({ type: "log", splitLine: { show: false } }, baseAxis(p)),
      series,
    };
  }

  // K 指数页专属：落点图（2011 起）与 60 个交易日持有净值
  async function buildKMap(p) {
    const [kd, ks] = await Promise.all([load("kindex"), load("kindex_signals")]);
    const idx = new Map(kd.dates.map((d, i) => [d, i]));
    const at = (ds) => (idx.has(ds) ? idx.get(ds) : kd.dates.findIndex((x) => x >= ds));
    const pts = ks.signals.map((s) => {
      const i = at(s.start);
      return i >= 0 ? { value: [kd.dates[i], kd.ndx[i]], sg: s } : null;
    }).filter(Boolean);
    return {
      tooltip: tip(p, {
        trigger: "item",
        formatter: (o2) => {
          const s = o2.data && o2.data.sg;
          if (!s) return "";
          return `${s.start}<br/>K &lt; 1 信号 · 最低 K ${s.min_k}<br/>60 个交易日后：纳指 ${pct(s.fwd60)} · 标普 ${pct(s.spx_fwd60)}`;
        },
      }),
      legend: { top: 0, left: 0, textStyle: { color: p.muted, fontSize: 11 }, itemWidth: 12, itemHeight: 8 },
      grid: { left: 52, right: 18, top: 34, bottom: 60 },
      dataZoom: ledgerZoom(p),
      xAxis: timeX(p),
      yAxis: Object.assign({ type: "log", splitLine: { show: false } }, baseAxis(p)),
      series: [
        { name: "纳指 100", type: "line", data: zip(kd.dates, kd.ndx), showSymbol: false, silent: true,
          lineStyle: { color: p.muted, width: 1, opacity: 0.7 }, itemStyle: { color: p.muted } },
        { name: "K < 1 信号", type: "scatter", data: pts, symbol: "diamond", symbolSize: 15, itemStyle: { color: p.danger } },
      ],
    };
  }

  async function buildKEq(p) {
    const [kd, ks] = await Promise.all([load("kindex"), load("kindex_signals")]);
    const dates = kd.dates, px = kd.ndx, spx = kd.spx || null;
    const idx = new Map(dates.map((d, i) => [d, i]));
    const at = (ds) => (idx.has(ds) ? idx.get(ds) : dates.findIndex((x) => x >= ds));
    const segs = [];
    let exit = -1;
    for (const s of ks.signals) {
      const i = at(s.start);
      if (i == null || i < 0 || i <= exit) continue;
      let j = s.exit60 ? at(s.exit60) : -1;
      if (j == null || j < 0) j = Math.min(i + 60, px.length - 1);
      segs.push([i, j]); exit = j;
    }
    if (!segs.length) throw new Error("no signals");
    const first = segs[0][0];
    const strat = [], hold = [], holdSpx = [];
    let eq = 1, si = 0;
    for (let t = first; t < dates.length; t++) {
      if (t > first) {
        while (si < segs.length && t > segs[si][1]) si++;
        if (si < segs.length && t > segs[si][0] && t <= segs[si][1]) eq *= px[t] / px[t - 1];
      }
      strat.push([dates[t], +eq.toFixed(4)]);
      hold.push([dates[t], +(px[t] / px[first]).toFixed(4)]);
      if (spx) holdSpx.push([dates[t], +(spx[t] / spx[first]).toFixed(4)]);
    }
    const endLbl = (color) => ({ show: true, formatter: (o2) => "×" + (+o2.value[1]).toFixed(1),
      fontFamily: "JetBrains Mono", fontSize: 11, color });
    const series = [
      { name: "每次信号都跟（持有 60 个交易日）", type: "line", data: strat, showSymbol: false,
        lineStyle: { color: p.cmpRed, width: 2.6 }, itemStyle: { color: p.cmpRed }, endLabel: endLbl(p.cmpRed) },
      { name: "一直持有纳指 100", type: "line", data: hold, showSymbol: false,
        lineStyle: { color: p.cmpPurple, width: 2, type: "dashed" }, itemStyle: { color: p.cmpPurple }, endLabel: endLbl(p.cmpPurple) },
    ];
    if (spx) series.push(
      { name: "一直持有标普 500", type: "line", data: holdSpx, showSymbol: false,
        lineStyle: { color: p.cmpBlue, width: 2, type: "dashed" }, itemStyle: { color: p.cmpBlue }, endLabel: endLbl(p.cmpBlue) });
    return {
      tooltip: tip(p, { valueFormatter: (v) => "×" + (+v).toFixed(2) }),
      legend: { top: 0, left: 0, textStyle: { color: p.muted, fontSize: 11 } },
      grid: { left: 52, right: 56, top: 56, bottom: 60 },
      dataZoom: ledgerZoom(p),
      xAxis: timeX(p),
      yAxis: Object.assign({ type: "log", splitLine: { show: false } }, baseAxis(p)),
      series,
    };
  }

  // 恐惧的标价：窗口开启日 VIX（保费水位）× 12 个月后纳指涨跌
  async function buildLeapsVix(p) {
    const lp = await load("leaps");
    const pts = lp.episodes
      .filter((e) => e.vix_start != null && e.ndx_m12 != null)
      .map((e) => ({ value: [e.vix_start, e.ndx_m12], ep: e,
        itemStyle: { color: e.ndx_m12 > 0 ? p.moss : p.danger } }));
    return {
      tooltip: tip(p, {
        trigger: "item",
        formatter: (o) => {
          const e = o.data && o.data.ep;
          if (!e) return "";
          return `${e.start}<br/>开窗日 VIX ${e.vix_start} · 最低恐贪 ${e.min_fng}<br/>12 个月后：纳指 ${pct(e.ndx_m12)} · 标普 ${pct(e.spx_m12)}`;
        },
      }),
      grid: { left: 56, right: 24, top: 30, bottom: 44 },
      xAxis: Object.assign({ type: "value", name: "开窗日 VIX", nameLocation: "middle", nameGap: 28,
        nameTextStyle: { color: p.muted, fontSize: 11 } }, baseAxis(p)),
      yAxis: Object.assign({ type: "value", axisLabel: Object.assign({}, baseAxis(p).axisLabel, { formatter: "{value}%" }) }, baseAxis(p)),
      series: [{ type: "scatter", data: pts, symbolSize: 11 }],
    };
  }

  // 滚动三年年化：策略 vs 两个锚（高于虚线的时段 = 跟信号占优）
  async function buildLeapsRoll(p) {
    const s = await leapsEquitySeries();
    const W = 756; // 3 年 ≈ 756 个交易日
    const roll = (vals) => {
      const out = [];
      for (let t = W; t < vals.length; t++)
        out.push([s.dates[t], +((Math.pow(vals[t] / vals[t - W], 252 / W) - 1) * 100).toFixed(2)]);
      return out;
    };
    const series = [
      { name: "每次窗口都跟（持有 12 个月）", type: "line", data: roll(s.strat), showSymbol: false,
        lineStyle: { color: p.cmpRed, width: 2.6 }, itemStyle: { color: p.cmpRed } },
      { name: "一直持有纳指 100", type: "line", data: roll(s.hold), showSymbol: false,
        lineStyle: { color: p.cmpPurple, width: 2, type: "dashed" }, itemStyle: { color: p.cmpPurple } },
    ];
    if (s.holdSpx) series.push(
      { name: "一直持有标普 500", type: "line", data: roll(s.holdSpx), showSymbol: false,
        lineStyle: { color: p.cmpBlue, width: 2, type: "dashed" }, itemStyle: { color: p.cmpBlue } });
    return {
      tooltip: tip(p, { valueFormatter: (v) => (+v).toFixed(1) + "%" }),
      legend: { top: 0, left: 0, textStyle: { color: p.muted, fontSize: 11 } },
      grid: { left: 52, right: 24, top: 56, bottom: 60 },
      dataZoom: ledgerZoom(p),
      xAxis: timeX(p),
      yAxis: Object.assign({ type: "value", axisLabel: Object.assign({}, baseAxis(p).axisLabel, { formatter: "{value}%" }) }, baseAxis(p)),
      series,
    };
  }

  // 宏观 · 仓位与杠杆：NAAIM 经理人敞口（周频，2006→）
  chart("macro", "ch-naaim", async (p) => {
    const d = await load("naaim");
    return {
      tooltip: tip(p, { valueFormatter: (v) => (+v).toFixed(1) }),
      grid: { left: 52, right: 60, top: 24, bottom: 60 },
      dataZoom: ledgerZoom(p),
      xAxis: timeX(p),
      yAxis: Object.assign({ type: "value", min: 0 }, baseAxis(p)),
      series: [{
        name: "NAAIM 经理人敞口", type: "line", data: zip(d.dates, d.values), showSymbol: false,
        lineStyle: { color: p.teal, width: 1.6 }, itemStyle: { color: p.teal },
        endLabel: { show: true, formatter: (o) => (+o.value[1]).toFixed(0),
          fontFamily: "JetBrains Mono", fontSize: 11, color: p.teal },
        markLine: { silent: true, symbol: "none",
          lineStyle: { color: p.muted, type: "dashed", width: 1 },
          label: { color: p.muted, fontFamily: "JetBrains Mono", fontSize: 10, formatter: "满仓 100" },
          data: [{ yAxis: 100 }] },
      }],
    };
  });

  // 宏观 · 仓位与杠杆：AIAE 全社会股票配置（季频，1945→）
  chart("macro", "ch-aiae", async (p) => {
    const d = await load("aiae");
    const m = d.meta || {};
    const T = (s) => (window.MC_I18N ? MC_I18N.translate(s) : s);
    // 🔒 sane_check 守卫：值落合理区间外（如首跑 0.0011 单位错）不画数值，显式说「核对中」。
    //    belt-and-suspenders——即便将来某次 CI 出坏数据，前端也绝不显示错的 AIAE。
    if (m.sane_check === false || !d.aiae || !d.aiae.length) {
      return { graphic: [{ type: "text", left: "center", top: "middle", style: {
        text: T("数据核对中"), fill: p.muted, fontSize: 13, textAlign: "center" } }] };
    }
    const pts = zip(d.dates, d.aiae).map(([x, v]) => [x, +(v * 100).toFixed(1)]);   // 0.53 → 53%
    const vals = d.aiae.map((v) => v * 100).slice().sort((a, b) => a - b);
    const med = vals[Math.floor(vals.length / 2)];
    return {
      tooltip: tip(p, { valueFormatter: (v) => (v == null ? "--" : (+v).toFixed(1) + "%") }),
      grid: { left: 46, right: 56, top: 24, bottom: 60 },
      dataZoom: ledgerZoom(p),
      xAxis: timeX(p),
      yAxis: Object.assign({ type: "value", axisLabel: { formatter: "{value}%" } }, baseAxis(p)),
      series: [{
        name: T("全社会股票配置"), type: "line", data: pts, showSymbol: false,
        lineStyle: { color: p.accent, width: 1.6 }, itemStyle: { color: p.accent },
        endLabel: { show: true, formatter: (o) => (+o.value[1]).toFixed(0) + "%",
          fontFamily: "JetBrains Mono", fontSize: 11, color: p.accent },
        markLine: { silent: true, symbol: "none",
          lineStyle: { color: p.muted, type: "dashed", width: 1 },
          // 标签放左侧：右侧要留给末值 endLabel，且右边距窄会被切
          label: { position: "insideStartTop", color: p.muted, fontFamily: "JetBrains Mono",
            fontSize: 10, formatter: T("历史中位") + " " + med.toFixed(0) + "%" },
          data: [{ yAxis: med }] },
      }],
    };
  });

  // 宏观 · 仓位与杠杆：CFTC 标普 500 期货站位（2026-09-15 加，Klay 令免费源全接入）
  chart("macro", "ch-cot-equity", async (p) => {
    const d = await load("cot_equity");
    const m = d.markets.spx;
    const lev = m.series.map((s) => [s.date, s.lev_net]);
    const am = m.series.map((s) => [s.date, s.am_net]);
    return {
      tooltip: tip(p, { valueFormatter: (v) => (v == null ? "--" : (+v).toLocaleString("en-US")) }),
      legend: { top: 0, textStyle: { color: p.ink, fontSize: 11 }, itemWidth: 18 },
      grid: { left: 70, right: 24, top: 34, bottom: 60 },
      dataZoom: ledgerZoom(p),
      xAxis: timeX(p),
      yAxis: Object.assign({ type: "value", name: "净头寸（手）" }, baseAxis(p)),
      series: [
        { name: "杠杆基金", type: "line", showSymbol: false, data: lev,
          lineStyle: { color: p.accent, width: 1.6 }, itemStyle: { color: p.accent },
          markLine: { silent: true, symbol: "none", label: { show: false },
            lineStyle: { color: p.muted, type: "dashed", width: 1 }, data: [{ yAxis: 0 }] } },
        { name: "资管机构", type: "line", showSymbol: false, data: am,
          lineStyle: { color: p.moss, width: 1.1, type: "dashed" }, itemStyle: { color: p.moss } },
      ],
    };
  });

  // 宏观 · 仓位与杠杆：FINRA 融资余额（月频，1997→）
  chart("macro", "ch-margin-debt", async (p) => {
    const d = await load("margin_debt");
    const bn = (v) => (v == null ? null : +(v / 1000).toFixed(1));   // 百万美元 → 十亿美元
    const debit = d.series.map((s) => [s.month + "-01", bn(s.debit)]);
    const net = d.series.map((s) => [s.month + "-01", bn(s.net)]);
    return {
      tooltip: tip(p, { valueFormatter: (v) => (v == null ? "--" : (+v).toLocaleString("en-US") + " B") }),
      legend: { top: 0, textStyle: { color: p.ink, fontSize: 11 }, itemWidth: 18 },
      grid: { left: 64, right: 60, top: 34, bottom: 60 },
      dataZoom: ledgerZoom(p),
      xAxis: timeX(p),
      yAxis: Object.assign({ type: "value", name: "十亿美元" }, baseAxis(p)),
      series: [
        { name: "融资余额", type: "line", showSymbol: false, data: debit,
          lineStyle: { color: p.accent, width: 1.6 }, itemStyle: { color: p.accent },
          endLabel: { show: true, formatter: (o) => Math.round(+o.value[1]).toLocaleString("en-US"),
            fontFamily: "JetBrains Mono", fontSize: 11, color: p.accent } },
        { name: "净融资", type: "line", showSymbol: false, data: net,
          lineStyle: { color: p.moss, width: 1.1, type: "dashed" }, itemStyle: { color: p.moss } },
      ],
    };
  });

  // 宏观 · 仓位与杠杆：OFR 对冲基金杠杆率（季频，2013→）
  chart("macro", "ch-ofr-lev", async (p) => {
    const d = await load("ofr_hedge_funds");
    const eq = d.series.equity_leverage, all = d.series.all_leverage_gav_nav;
    const fmt = (v) => (v == null ? "--" : (+v).toFixed(2) + "×");
    return {
      tooltip: tip(p, { valueFormatter: fmt }),
      legend: { top: 0, textStyle: { color: p.ink, fontSize: 11 }, itemWidth: 18 },
      grid: { left: 52, right: 60, top: 34, bottom: 40 },
      xAxis: timeX(p),
      yAxis: Object.assign({ type: "value", scale: true, axisLabel: { formatter: "{value}×" } }, baseAxis(p)),
      series: [
        { name: "股票策略杠杆率", type: "line", data: zip(eq.dates, eq.values), symbolSize: 5,
          lineStyle: { color: p.accent, width: 1.6 }, itemStyle: { color: p.accent },
          endLabel: { show: true, formatter: (o) => (+o.value[1]).toFixed(2) + "×",
            fontFamily: "JetBrains Mono", fontSize: 11, color: p.accent } },
        { name: "全部合格基金", type: "line", data: zip(all.dates, all.values), symbolSize: 4,
          lineStyle: { color: p.moss, width: 1.1, type: "dashed" }, itemStyle: { color: p.moss } },
      ],
    };
  });

  // K / LEAPS 页注册台账图表（带缩放的完整版；头版是紧凑钩子）
  chart("kindex", "ch-k-map", buildKMap);
  chart("kindex", "ch-k-eq", buildKEq);
  chart("leaps", "ch-leaps-map", (p) => buildLedgerMap(p, { zoom: true }));
  chart("leaps", "ch-leaps-eq", (p) => buildLedgerEq(p, { zoom: true }));
  chart("leaps", "ch-leaps-vix", buildLeapsVix);
  chart("leaps", "ch-leaps-roll", buildLeapsRoll);

  async function renderPulse() {
    let d, leaps = null, kd = null, ks = null;
    try { d = await load("pulse"); } catch (e) {
      const _host8 = document.getElementById("pulse-base");
      if (!_host8) return;  // §63 瘦身路由页：该面板不在本页，跳过填充
      _host8.innerHTML =
        '<p style="color:var(--ink-muted)">数据更新中，稍后自动出现 · data updating</p>';
      return;
    }
    try { leaps = await load("leaps"); } catch (e) {}
    try { kd = await load("kindex"); ks = await load("kindex_signals"); } catch (e) { kd = ks = null; }

    // 2026-07-26 今日判读卡（用户裁）：露每期 digest 开头两句真现象句，断点即墙。
    // 契约见 data/judgment_teaser.json 的 _note：只取现象句，判断句永远留在断点之后（Paddle 主语测试）。
    // 超过 6 天未更新自动回退为纯送达行 —— 机制烂掉时站上不留过期样品。
    let jt = null;
    try { jt = await load("judgment_teaser"); } catch (e) {}
    // 2026-07-26 §46：每周对账墙。公开度按品类切而非按时间 —— 周报/月报公开（拉新·可转发），
    // 每日 digest 永不进表（付费独占）。按时间切会让付费者觉得「等一天就免费」，且架空
    // 定价页「判读档案」那条权益。数据契约见 data/digest_archive.json 的 _note：绝不手编。
    let arch = null;
    try { arch = await load("digest_archive"); } catch (e) {}
    const jtFresh = jt && jt.date && jt.zh &&
      (Date.now() - new Date(jt.date + "T00:00:00").getTime()) / 86400000 < 6.5;

    // 2026-07-26 前向计数器（用户裁）：N 从台账数据本身数（dates >= meta.forward_start），
    // 不做日历推算 —— 判据=台账数据非工作流自报，休市日天然不计。数据缺失时整行不出。
    let fwdN = null;
    try {
      const lg = await load("leaps_gauge");
      const fs = (lg.meta && lg.meta.forward_start) || "2026-07-13";
      const n = (lg.dates || []).filter((x) => x >= fs).length;
      if (n > 0) fwdN = n;
    } catch (e) {}

    const chg = (v) => `<span class="${v >= 0 ? "pos" : "neg"}">${(v > 0 ? "+" : "") + v.toFixed(2)}%</span>`;
    const tempColor = d.temp >= 75 ? "#B8421E" : d.temp >= 50 ? "#C9882E" : d.temp >= 25 ? "#14A63E" : "#2B5F8F";
    const tempWord = d.temp >= 75 ? "炙热" : d.temp >= 50 ? "偏暖" : d.temp >= 25 ? "温和" : "冰点";
    const pct = (v) => v.toFixed(1);
    const q = d.quotes || {};
    const windowOpen = leaps && leaps.current && leaps.current.window_open;

    // 读数台账区块（数据不全时整块跳过，不出残缺 UI）。2026-07-26 §43 用户裁：头版去「信号」「战报」——
    // 台账不动，措辞把主语说死：收益率是纳指 100 在触发日之后的表现，不是任何人的收益。
    let ledgerHTML = "";
    if (leaps && kd && ks && kd.current && leaps.current) {
      const fmt = (v) => (v == null ? "--" : (v > 0 ? "+" : "") + v.toFixed(1) + "%");
      const sigs = ks.signals, eps = leaps.episodes;
      const kAll = sigs.length, kW = sigs.filter((s) => s.fwd60 > 0).length,
        kL = sigs.filter((s) => s.fwd60 != null && s.fwd60 <= 0).length;
      const kWs = sigs.filter((s) => s.spx_fwd60 > 0).length,
        kLs = sigs.filter((s) => s.spx_fwd60 != null && s.spx_fwd60 <= 0).length;
      const lAll = eps.length, lW = eps.filter((e) => e.ndx_m12 > 0).length,
        lL = eps.filter((e) => e.ndx_m12 != null && e.ndx_m12 <= 0).length;
      const lWs = eps.filter((e) => e.spx_m12 > 0).length,
        lLs = eps.filter((e) => e.spx_m12 != null && e.spx_m12 <= 0).length;
      const kTrig = kd.current.k < 1, lOpen = leaps.current.window_open;
      const lastK = sigs[sigs.length - 1], lastL = eps[eps.length - 1];
      const lastLRet = lastL.ndx_to_date != null ? lastL.ndx_to_date : lastL.spx_to_date;
      ledgerHTML = `
      <div class="pulse-ledger">
        <div class="pulse-section-label">读数台账 · 逐次公开对账</div>
<!-- 2026-07-26 用户裁（§41）：两段压成一行 —— 品牌句留一句小字；「官方定义」段挪去 K 指数页
     hero（GEO 锚点不丢：llms.txt/方法论/Kaggle/HF 仍锚着，头版让位给卡片）。
     「GitHub 时间戳」半句删=与下方验证卡文案重复。 -->
        <p class="ledger-intro">两个原创指标，一本逐日自动记的账：赢的和输的都在账上。</p>
        <div class="ledger-cards">
          <a class="ledger-card" href="kindex">
            <div class="lc-name">K 指数 <span>CNN 恐贪 ÷ VIX</span></div>
            <div class="lc-val">${kd.current.k.toFixed(2)}</div>
            <div class="lc-state ${kTrig ? "neg" : "pos"}">${kTrig ? "触发中" : "未触发"} <span>（K &lt; 1 触发）</span></div>
            <div class="lc-meta"><b>${kAll}</b> <span>次触发（2011 年起）</span> · <span>60 个交易日后</span><br><span>标普</span> <b class="pos">${kWs}</b> <span>涨</span> <b class="neg">${kLs}</b> <span>跌</span> · <span>纳指</span> <b class="pos">${kW}</b> <span>涨</span> <b class="neg">${kL}</b> <span>跌</span></div>
          </a>
          <a class="ledger-card" href="leaps">
            <div class="lc-name">LEAPS 窗口 <span>恐贪 &lt; 25 · 极端恐惧</span></div>
            <div class="lc-val">${Math.round(leaps.current.fng)}</div>
            <div class="lc-state ${lOpen ? "neg" : "pos"}">${lOpen ? "窗口开启" : "窗口关闭"} <span>（恐贪 &lt; 25 开启）</span></div>
            <div class="lc-meta"><b>${lAll}</b> <span>次窗口（2011 年起）</span> · <span>12 个月后</span><br><span>标普</span> <b class="pos">${lWs}</b> <span>涨</span> <b class="neg">${lLs}</b> <span>跌</span> · <span>纳指</span> <b class="pos">${lW}</b> <span>涨</span> <b class="neg">${lL}</b> <span>跌</span></div>
          </a>
          <div class="ledger-card">
            <div class="lc-name">最近一次触发后的对账 <span>纳指 100 自触发日至今的涨跌 · 非任何人的收益</span></div>
            <div class="lc-row"><span class="lc-tag">K</span> <span class="lc-date">${lastK.start}</span> <span>至今</span> <b class="${lastK.fwd_to_date >= 0 ? "pos" : "neg"}">${fmt(lastK.fwd_to_date)}</b></div>
            <div class="lc-row"><span class="lc-tag">LEAPS</span> <span class="lc-date">${lastL.start}</span> <span>至今</span> <b class="${lastLRet >= 0 ? "pos" : "neg"}">${fmt(lastLRet)}</b></div>
            <!-- 2026-07-26 用户裁：删「每一次的逐条结果，见上方…」提示行 —— 与卡下方深链行重复 -->
          </div>
        </div>
        <!-- 2026-07-17 头版瘦身（用户裁）：落点图+净值曲线两张 15 年图撤回各自的家（K 页/恐惧的标价页），
             原位只留一行文字链。「净值曲线与付费 CTA 相邻」悬案随撤图自动结案。
             2026-07-26（§42 追补，用户裁）：该行下移到验证行之后 —— 台账区收成
             「数字卡 → 判读样品 → 验证背书 → 深链」一条线，两行小字（验证/深链）归拢在尾部。 -->
        <!-- 2026-07-16：删掉这里的付费 primary（原「盘前数据简报 · 创始价 $9.9」）。三个理由：
             ① $9.9 是邮件预约制（方案 C 两扇门：陌生人自助 $29 / 信任者预约），在头版对所有人喊 9.9 = 那扇门不存在了；
                且数字本身已过期（创始码 dsc_01kxjqmtb40e8bsy2zqtkqxk0e 是 $99/年 不是 $9.9/月）。
             ② 净值曲线正上方挂付费 CTA = 观感就是「看我多准→掏钱」，而台账写着 K 策略跑输纳指。
                改动词治不了，那是相邻性的问题（此前一直挂着的未决项，本次解决）。
             ③ 读完台账的自然下一步是「验证」与「继续跟着看」，不是掏钱。掏钱的位置在定价页（页脚有入口）。
             不需要再加锚点（且 #pulse-base 不是面板名，会打乱 hash 路由）。
             2026-07-17：订阅表单从这里搬进 #pricing 免费档：全站订阅动线唯一化（顶栏胶囊→定价页，
             免费/付费并排）；主页不补替代 CTA，观感失衡就留白。 -->
        <!-- 「方法论全文」已删：正上方 .ledger-note 那句已经写着「完整口径与如实披露见方法论」，
             隔 20px 再放个按钮是同一件事说两遍；页脚也有。
             「在 GitHub 验证台账」留下且不进页脚：它是净值曲线的解药：上面那句「本站不宣称信号能跑赢买入持有」
             之后，把可验证的链接就摆在这里，本身就是可信度。它的价值不是被点击，是它存在。
             挪进页脚 = 从「我请你查」变成「你想查自己找」，那是两种人格。 -->
        <!-- 2026-07-25：头版唯一的送达入口（fallback 行）。与 7·16 撤掉的付费 primary 不是一回事：
             那个是「创始价 $9.9」按钮（价格 + 紧挨净值曲线），本行只说送达、无价格、非按钮。
             2026-07-26：升级为今日判读卡（teaser 新鲜时），中英双渲染靠 CSS 按 html[lang] 切换；
             teaser 过期（>6 天）自动回退纯送达行。
             2026-07-26 晚（§42 用户裁）：判读卡升到验证行之上（样品先行），推翻 07-25「先验证再送达」
             的排序；验证卡同批从盒子压成一行小字（宽度即正文列宽）。分隔符用「·」不用全角逗号 ——
             这行在 EN 态是整键替换 + 裸标点，全角逗号会复现 §40 的英文态全角标点残留。 -->
        ${jtFresh ? `
        <div class="judgment-teaser">
          <div class="jt-label"><span>最新一期判读</span><span>每个交易日盘前更新</span><span class="jt-date">${jt.date}</span></div>
          <div class="jt-bodywrap">
            <p class="jt-body jt-zh">${jt.zh}</p>
            <p class="jt-body jt-en">${jt.en || jt.zh}</p>
          </div>
          <p class="jt-lock"><span>当日完整判读只进订户邮箱</span> → <a href="pricing">盘前数据简报</a></p>
        </div>` : `
        <p class="ledger-note">这一页的读数，每个交易日盘前送进邮箱 → <a href="pricing">盘前数据简报</a></p>`}
        ${arch && arch.issues && arch.issues.length ? `
        <div class="digest-archive">
          <div class="da-head"><span>每周对账 · 公开归档</span></div>
          ${arch.issues.slice(0, 3).map((i) => `<a class="da-row" href="${i.url}" target="_blank" rel="noopener"><span class="da-date">${i.date.slice(5)}</span><span class="da-title">${i.title}</span></a>`).join("")}
        </div>` : ""}
        <p class="ledger-note lv-line"><a href="https://github.com/klaywang24/fear-price/commits/main" target="_blank" rel="noopener"><span>在 GitHub 验证台账</span></a> · <span>每个交易日的读数，每日收盘自动更新提交，带 GitHub 时间戳，事后不可改写，GitHub 精确可查</span>${fwdN != null ? ` · <span>前向台账：第</span> <b class="fwd-n">${fwdN}</b> <span>个交易日 · 自 2026-07-13 起</span>` : ""}</p>
        <p class="ledger-note">15 年台账与全部输赢（包括跑输的那部分）→ <a href="kindex">K 指数</a> · <a href="leaps">恐惧的标价</a></p>
      </div>`;
    }

    // 情绪仪表盘六卡已于 2026-07-17 迁往「恐惧的标价」页（renderFearDecomp）：
    // 与该页既有 4 context 去重后剩四卡（SKEW/期限结构两张删），头版不再渲染。

    const _host9 = document.getElementById("pulse-base");
    if (!_host9) return;  // §63 瘦身路由页：该面板不在本页，跳过填充
    _host9.innerHTML = `
      <div class="pulse-kicker">TODAY'S FRONT PAGE · ${d.date}</div>
      <div class="pulse-chartband"></div>
      <div class="pulse-head">
        <div class="temp-ring" style="--pct:${d.temp};--temp-color:${tempColor}">
          <div class="val">${d.temp.toFixed(1)}<em>°</em></div>
          <div class="lbl"><span>市场温度</span> · <span>${tempWord}</span></div>
        </div>
        <div class="pulse-title">
          <h1>今日，市场的体温</h1>
          <p class="sub">温度 = （估值百分位 ${pct(d.val_pct)} + 情绪百分位 ${pct(d.sent_pct)}）÷ 2。估值取标普 500 PE(TTM) 在 1871 年以来全历史的位置；情绪取今日上涨家数占比在近一年中的位置。</p>
          <div style="margin-top:14px;display:flex;flex-direction:column;gap:8px">
            <div class="pulse-quotes">
              ${q.spx ? `<span class="pq"><span class="pq-l">标普 500</span> <b>${Math.round(q.spx.close).toLocaleString("en-US")}</b> ${chg(q.spx.chg)}</span>` : ""}
              ${q.ndx ? `<span class="pq"><span class="pq-l">纳指 100</span> <b>${Math.round(q.ndx.close).toLocaleString("en-US")}</b> ${chg(q.ndx.chg)}</span>` : ""}
              ${q.dji ? `<span class="pq"><span class="pq-l">道琼斯</span> <b>${Math.round(q.dji.close).toLocaleString("en-US")}</b> ${chg(q.dji.chg)}</span>` : ""}
              ${q.rut ? `<span class="pq"><span class="pq-l">罗素 2000</span> <b>${Math.round(q.rut.close).toLocaleString("en-US")}</b> ${chg(q.rut.chg)}</span>` : ""}
            </div>
            <div class="pulse-quotes">
              ${q.vix ? `<span class="pq"><span class="pq-l">VIX 恐慌指数</span> <b>${q.vix.close}</b> ${chg(q.vix.chg)}</span>` : ""}
              ${d.fng != null ? `<span class="pq"><span class="pq-l">恐惧贪婪指数</span> <b>${Math.round(d.fng)}</b></span>` : ""}
              ${d.k != null ? `<span class="pq"><span class="pq-l">K 指数</span> <b>${d.k.toFixed(2)}</b></span>` : ""}
              ${leaps ? `<span class="pq"><span class="pq-l">LEAPS Call 窗口</span> <b class="${windowOpen ? "neg" : "pos"}">${windowOpen ? "开启" : "关闭"}</b></span>` : ""}
            </div>
          </div>
        </div>
      </div>
      ${ledgerHTML}
      <div>
        <div class="pulse-section-label">涨跌分布 · 标普 500 成分股</div>
        <div class="breadth-bar">
          <div style="flex:${d.adv};background:var(--moss)">${d.adv} 涨</div>
          <div style="flex:${Math.max(d.flat, 6)};background:var(--ink-muted)">${d.flat} 平</div>
          <div style="flex:${d.dec};background:var(--danger)">${d.dec} 跌</div>
        </div>
        <div class="breadth-note">上涨家数占比 ${d.adv_ratio}%（(涨 + 平÷2) ÷ ${d.total}），处于近一年第 ${pct(d.sent_pct)} 百分位</div>
      </div>
      <div>
        <div class="pulse-section-label">今天热在哪 · 各板块涨跌家数（标普 500 全成分股 · 自有数据，每日收盘后更新）</div>
        <div id="ch-breadth-sector" class="sector-breadth"></div>
      </div>`;

    // 顶部世纪带：真实标普 500 月线（1927→，对数坐标：天然一路向上）。
    // 只做「峰值相对」的温和回撤放大：每个历史新高原样保留（上涨气势不变），
    // 仅把从峰值起的回撤加深一点点，让危机比原始更可辨、又不失向上主线。
    // 第 4 项 = 标签上/下；第 5 项 = 水平微调 px（负=左移）；第 6 项 = 垂直微调 px
    const CRISES = [
      ["1932-06", "1929", "大萧条", "below", 6, 8], ["1974-09", "1974", "滞胀", "below", 0, 0],
      ["1987-11", "1987", "黑色星期一", "above", -48, 0], ["2002-09", "2000", "互联网泡沫", "above", -48, 0],
      ["2009-02", "2008", "金融危机", "below", 0, 16], ["2020-03", "2020", "疫情冲击", "above", -20, 0],
      ["2022-09", "2022", "加息", "below", 44, 22],
    ];
    const DD_K = 1.7; // 回撤放大系数（1 = 真实，越大回撤越深；只影响回撤、不动新高。日频后调高让快速深跌如 2020 更醒目）
    const drawCentury = async (band, stroke, width, glow, opacity, withDots) => {
      try {
        const c = await load("sp500_century");
        const vals = c.close, n = vals.length;
        const L = vals.map((v) => Math.log(v));
        let pk = L[0];
        const adj = L.map((v) => { pk = Math.max(pk, v); return pk - DD_K * (pk - v); });
        const lo = Math.min(...adj), hi = Math.max(...adj);
        const xy = (i) => [i / (n - 1) * 100, 96 - (adj[i] - lo) / (hi - lo) * 92];
        const pts = adj.map((v, i) => xy(i).map((z) => z.toFixed(2)).join(",")).join(" ");
        band.insertAdjacentHTML("afterbegin",
          `<svg class="pulse-chartline" viewBox="0 0 100 100" preserveAspectRatio="none" style="opacity:${opacity}">
             <polyline class="draw-line" points="${pts}" pathLength="1000" fill="none"
               style="stroke:${stroke};stroke-width:${width}${glow ? `;filter:drop-shadow(0 0 ${glow}px ${stroke})` : ""}"/></svg>`);
        if (!withDots) return;
        for (const [ym, year, name, place, dx, dy] of CRISES) {
          const i = c.dates.findIndex((dt) => dt >= ym);
          if (i < 0) continue;
          const [x, y] = xy(i);
          const cls = (place === "below" ? "below " : "") + (x > 90 ? "edge-r" : x < 6 ? "edge-l" : "");
          band.insertAdjacentHTML("beforeend",
            `<div class="crisis-dot ${cls}" style="left:${x.toFixed(1)}%;top:${y.toFixed(1)}%${dx ? `;--dx:${dx}px` : ""}${dy ? `;--dy:${dy}px` : ""}">
               <i></i><span>${year} · <span>${name}</span></span></div>`);
        }
      } catch (e) {}
    };
    const base = document.getElementById("pulse-base");
    await drawCentury(base.querySelector(".pulse-chartband"), "var(--accent)", 0.5, 0, 0.55, true);

    // 揭示层 = 同一内容的夜间克隆 + 金色发光线（红点随克隆保留）
    const reveal = document.getElementById("pulse-reveal");
    reveal.innerHTML = base.innerHTML;
    // 克隆层去掉 id，避免与日间层重复（getElementById/ECharts 挂载只认日间层）
    reveal.querySelectorAll("[id]").forEach((el) => el.removeAttribute("id"));
    const rBand = reveal.querySelector(".pulse-chartband");
    const clonedSvg = rBand && rBand.querySelector("svg.pulse-chartline");
    if (clonedSvg) clonedSvg.remove();
    if (rBand) await drawCentury(rBand, "#E0B05A", 0.8, 2, 0.9, false);

    // 板块涨跌图（自有数据，§45 取代 TradingView widget）：只画日间层 —— 聚光灯只掀开顶部
    // 时间线，本区不会被揭示；揭示层的克隆容器已被上面 removeAttribute("id") 摘掉 id，不会抢渲染。
    // 走 buildOne 而非自建实例：renderPulse 每次重建 DOM，旧实例须由 built Map 统一 dispose。
    buildOne("ch-breadth-sector", buildSectorOption);

    // 台账两张图已随头版瘦身撤除（buildLedgerMap/Eq 仍服务 K 页与恐惧的标价页）；
    // stampSources 保留：它统一给全站 .card 补数据源行，与头版是否有图无关
    stampSources();

    // 聚光灯：只在悬停/点击世纪时间线上的六个危机点时亮起，定位在该点；离开即熄灭
    const hero = document.getElementById("pulse-hero");
    const R = 30; // 聚光灯半径（比之前缩小约 2/3，只掀开危机点附近一小块）
    const setSpot = (x, y) => {
      const m = `radial-gradient(circle ${R}px at ${x}px ${y}px, #fff 0%, #fff 45%, rgba(255,255,255,.55) 68%, rgba(255,255,255,.15) 86%, transparent 100%)`;
      reveal.style.webkitMaskImage = m;
      reveal.style.maskImage = m;
    };
    const hideSpot = () => setSpot(-99999, -99999);
    const spotAtDot = (dot) => {
      const hr = hero.getBoundingClientRect();
      const ir = (dot.querySelector("i") || dot).getBoundingClientRect();
      setSpot(ir.left + ir.width / 2 - hr.left, ir.top + ir.height / 2 - hr.top);
    };
    hideSpot();
    // 事件绑在日间层的危机点上（揭示层 pointer-events:none，不拦截）
    base.querySelectorAll(".crisis-dot").forEach((dot) => {
      dot.addEventListener("mouseenter", () => spotAtDot(dot));
      dot.addEventListener("mouseleave", hideSpot);
      dot.addEventListener("click", (e) => { e.stopPropagation(); spotAtDot(dot); }); // 触屏点亮
    });
    // 点击时间线以外任意处熄灭（触屏收起）
    hero.addEventListener("click", hideSpot);
  }

  // ---------------- 数据出处标注（每张图/表下方统一小字） ----------------
  const SRC_BY_PANEL = {
    pulse: "Yahoo Finance + CNN Fear & Greed", spy: "Yahoo Finance", qqq: "Yahoo Finance",
    tech: "Yahoo Finance", fin: "Yahoo Finance", consumer: "Yahoo Finance", luxury: "Yahoo Finance",
    macro: "FRED", kindex: "CNN Fear & Greed + Yahoo Finance", leaps: "Cboe (VIX/VIX1Y/SKEW) + FRED + CNN Fear & Greed + Yahoo Finance",
  };
  const SRC_OVERRIDES = [
    ["ch-spy-cape", "multpl / Robert Shiller"], ["ch-spy-pettm", "multpl"],
    ["ch-spy-eps", "multpl"], ["ch-spy-val", "multpl / Shiller"],
    ["spy-constituents", "Wikipedia"], ["qqq-constituents", "Wikipedia"],
    ["ch-spy-sectors", "Wikipedia"], ["ch-qqq-sectors", "Wikipedia"],
    ["ch-spy-sectorw", "Yahoo Finance"], ["ch-qqq-sectorw", "Yahoo Finance"],
    ["ch-mag7", "Yahoo Finance"],
    ["ch-leaps-vx", "Cboe CFE daily settlement"], ["ch-leaps-cot", "CFTC · Traders in Financial Futures"],
    // 2026-07-18：这两张图不吃页面默认的 Cboe+FRED+CNN+Yahoo 那串：写错出处比不写更糟。
    // ⚠️ 源名必须纯英文（本串不走 i18n，中文会在 EN 下泄漏）。
    ["ch-vol-family", "Cboe equity & sector volatility indices"],
    ["ch-vol-indices", "Cboe index volatility indices"],
    // 2026-09-15 仓位与杠杆四件：三张不是日频，出处行要写它们自己的频率与截止日，不吃默认那句
    ["ch-cot-equity", "CFTC · Traders in Financial Futures",
      { asofFrom: "cot_equity", asofPick: (d) => d.markets.spx.latest.date, freq: "周二数据周五 15:30 ET 发布，每周更新" }],
    ["ch-margin-debt", "FINRA margin statistics",
      { asofFrom: "margin_debt", asofPick: (d) => d.latest.month, freq: "月频，参考月次月第三周发布" }],
    ["ch-ofr-lev", "OFR Hedge Fund Monitor (SEC Form PF)",
      { asofFrom: "ofr_hedge_funds", asofPick: (d) => d.series.equity_leverage.latest.date, freq: "季频，滞后约两个季度" }],
    ["ch-naaim", "NAAIM Exposure Index",
      { asofFrom: "naaim", asofPick: (d) => d.date, freq: "NAAIM 2026-08 起改订阅制，公开数据延迟三个月，本图停更" }],
    ["ch-short-flow", "FINRA RegSHO daily short volume"],
    // 2026-07-18 夜：这张卡不能吃「数据截至 <今天> · 每交易日更新」：它是双月结算且
    // 滞后约两周。一张主打「我滞后」的卡片若把日期写成今天，是自己打自己。
    // ∴ 第三元素 = { asofFrom: 数据文件名 }，出处行改用该文件 meta.asof 与专属频率句。
    ["ch-short-interest", "FINRA consolidated short interest", { asofFrom: "short_interest" }],
    // 做空章新增四图：同样按结算日口径署源，别吃「数据截至<今天>·每交易日更新」的默认值
    ["ch-short-breadth", "FINRA consolidated short interest", { asofFrom: "short_interest" }],
    ["ch-short-single", "FINRA consolidated short interest", { asofFrom: "short_interest" }],
    ["ch-short-scatter", "FINRA RegSHO + consolidated short interest", { asofFrom: "short_interest" }],
    ["ch-short-sig", "FINRA RegSHO + consolidated short interest", { asofFrom: "short_interest" }],
  ];
  let metaDate = "";

  // ⚠️ 先把需要的 as-of 日期全部取回，**再**进同步循环。
  // 不要在循环里 await：「已有 .src-note 则跳过」这个守卫一旦被 await 打断，
  // 两次调用（meta 加载后一次、切语言 rebuildAll 后一次）会同时穿过守卫 → 出处行重复两遍。
  // 2026-07-18 夜实测踩中，改回「异步取数在前、DOM 写入全同步」。
  const _asofCache = {};
  async function stampSources() {
    if (!metaDate) return;
    const needed = [...new Set(SRC_OVERRIDES.filter((o) => o[2] && o[2].asofFrom).map((o) => o[2].asofFrom))];
    await Promise.all(needed.map(async (name) => {
      if (_asofCache[name] !== undefined) return;
      try {
        const dd = await load(name);
        const picker = (SRC_OVERRIDES.find((o) => o[2] && o[2].asofFrom === name) || [])[2];
        _asofCache[name] = (picker && picker.asofPick) ? (picker.asofPick(dd) || null) : ((dd.meta || {}).asof || null);
      } catch (e) { _asofCache[name] = null; }
    }));
    document.querySelectorAll(".panel .card").forEach((card) => {
      if (card.querySelector(".src-note")) return;
      const inner = card.querySelector(".chart, table");
      if (!inner) return;
      const panelKey = card.closest(".panel").id.replace("panel-", "");
      let src = SRC_BY_PANEL[panelKey] || "Yahoo Finance";
      let opts = null;
      for (const [id, s, o] of SRC_OVERRIDES) {
        if (card.querySelector("#" + id)) { src = s; opts = o || null; break; }
      }
      let line;
      if (opts && opts.asofFrom) {
        const asof = _asofCache[opts.asofFrom];
        if (!asof) return;          // 拿不到就不写：宁可缺一行，也不写一个错的日期
        const [y, mo, dd] = asof.split("-");
        line = opts.freq
          ? `数据截至 ${asof} · ${src} · ${opts.freq}`
          : `数据截至 ${dd}-${mo}-${y}（结算日） · ${src} · 每月两次结算，结算日后约 8 个交易日发布`;
      } else if (/-fd-/.test(inner.id || "")) {
        line = `数据截至 ${metaDate} · macrotrends + Yahoo Finance · 每周六自动更新`;
      } else {
        line = `数据截至 ${metaDate} · ${src} · 每交易日收盘后自动更新`;
      }
      card.insertAdjacentHTML("beforeend", `<p class="footnote src-note">${line}</p>`);
    });
  }

  // ---------------- LEAPS 窗口 ----------------
  chart("leaps", "ch-leaps", async (p) => {
    const d = await load("leaps");
    const areas = d.episodes.map((e) => [
      { xAxis: e.start, itemStyle: { color: p.accent, opacity: 0.12 } },
      { xAxis: e.end },
    ]);
    return {
      tooltip: tip(p),
      grid: { left: 58, right: 64, top: 28, bottom: 64 },
      xAxis: timeX(p),
      yAxis: [
        Object.assign({ type: "value", name: "CNN 恐贪", min: 0, max: 100 }, baseAxis(p)),
        Object.assign({ type: "log", name: "NDX", position: "right", splitLine: { show: false } }, baseAxis(p)),
      ],
      dataZoom: [{ type: "inside" }, { type: "slider", bottom: 6, height: 18,
        borderColor: p.border, fillerColor: "rgba(160,57,47,0.08)",
        handleStyle: { color: p.accent }, textStyle: { color: p.muted, fontSize: 10 } }],
      series: [
        { name: "CNN 恐贪", type: "line", yAxisIndex: 0, showSymbol: false,
          data: zip(d.dates, d.fng), lineStyle: { color: p.gold, width: 1.1 }, itemStyle: { color: p.gold },
          markLine: { silent: true, symbol: "none",
            lineStyle: { color: p.danger, type: "dashed", width: 1.2 },
            label: { color: p.danger, formatter: "开仓阈值 25", fontFamily: "JetBrains Mono", fontSize: 10 },
            data: [{ yAxis: d.threshold }] },
          markArea: { silent: true, data: areas } },
        { name: "纳指 100", type: "line", yAxisIndex: 1, showSymbol: false,
          data: zip(d.dates, d.ndx), lineStyle: { color: p.blue, width: 1.4 }, itemStyle: { color: p.blue } },
      ],
    };
  });

  async function renderLeaps() {
    const d = await load("leaps");
    const cur = d.current;
    const last = d.episodes[d.episodes.length - 1];
    const _host10 = document.getElementById("leaps-status");
    if (!_host10) return;  // §63 瘦身路由页：该面板不在本页，跳过填充
    _host10.innerHTML = `
      <div class="stat ${cur.window_open ? "signal-on" : ""}">
        <div class="label">今日 CNN 恐贪（${cur.date.replace(/-/g, "\u2011")}）</div>
        <div class="value">${cur.fng.toFixed(0)}</div>
        <div class="note">${cur.window_open ? "极端恐惧区（&lt; 25）" : "常态（≥ 25）"}</div>
      </div>
      <div class="stat"><div class="label">极端恐惧线</div><div class="value">&lt; ${d.threshold}</div><div class="note">${cur.rating || "历史参照"}</div></div>
      <div class="stat"><div class="label">2011 年来极端恐惧</div><div class="value">${d.episodes.length} 段</div><div class="note">连续交易日聚为一段</div></div>
      <div class="stat"><div class="label">最近一次</div><div class="value" style="font-size:18px">${last.start.replace(/-/g, "\u2011")}</div><div class="note">NDX 至今 ${last.ndx_to_date > 0 ? "+" : ""}${last.ndx_to_date}%</div></div>`;

    const cell = (v) => v == null ? "<td>--</td>" :
      `<td class="${v >= 0 ? "pos" : "neg"}">${(v > 0 ? "+" : "") + v.toFixed(1)}%</td>`;
    const _host11 = document.getElementById("leaps-table");
    if (!_host11) return;  // §63 瘦身路由页：该面板不在本页，跳过填充
    _host11.innerHTML =
      "<tr><th>#</th><th>窗口首日</th><th>持续(日)</th><th>最低恐贪</th>" +
      "<th>SPX+6m</th><th>SPX+12m</th><th>SPX+18m</th><th>NDX+6m</th><th>NDX+12m</th><th>NDX+18m</th><th>NDX至今</th></tr>" +
      d.episodes.slice().reverse().map((e, i) =>
        `<tr><td>${d.episodes.length - i}</td><td>${e.start}</td><td>${e.days_below}</td>` +
        `<td class="k-min">${e.min_fng}</td>` +
        cell(e.spx_m6) + cell(e.spx_m12) + cell(e.spx_m18) +
        cell(e.ndx_m6) + cell(e.ndx_m12) + cell(e.ndx_m18) + cell(e.ndx_to_date) + "</tr>").join("");

    const done = d.episodes.filter((e) => e.ndx_m12 != null);
    const win = done.filter((e) => e.ndx_m12 > 0).length;
    const spxDone = d.episodes.filter((e) => e.spx_m12 != null);
    const spxWin = spxDone.filter((e) => e.spx_m12 > 0).length;
    document.getElementById("leaps-verdict").textContent =
      `历史记录：${d.episodes.length} 段极端恐惧中，12 个月后 NDX 上行 ${win}/${done.length}、SPX 上行 ${spxWin}/${spxDone.length}。` +
      `注意 2021 年下半年的几段：高位回落途中的"极端恐惧"并非底部，12 个月后仍为负：恐惧标记的是情绪极值，不是估值底。` +
      `与 K 指数（CNN÷VIX）参照观察即可。以上为历史事实记录，非买卖建议，历史规律不保证未来。`;
  }

  // ---------------- 恐惧的标价指数（LEAPS 贵贱温度计） ----------------
  chart("leaps", "ch-leaps-gauge", async (p) => {
    const d = await load("leaps_gauge");
    const fs = d.meta.forward_start;
    const bt = d.dates.map((dt, i) => [dt, dt < fs ? d.expensiveness_3y[i] : null]);
    const fw = d.dates.map((dt, i) => [dt, dt >= fs ? d.expensiveness_3y[i] : null]);
    return {
      tooltip: tip(p, { valueFormatter: (v) => (v == null ? "--" : (+v).toFixed(0)) }),
      grid: { left: 48, right: 24, top: 26, bottom: 64 },
      xAxis: timeX(p),
      yAxis: Object.assign({ type: "value", name: "贵贱百分位", min: 0, max: 100 }, baseAxis(p)),
      dataZoom: [{ type: "inside" }, { type: "slider", bottom: 6, height: 18,
        borderColor: p.border, fillerColor: "rgba(160,57,47,0.08)",
        handleStyle: { color: p.accent }, textStyle: { color: p.muted, fontSize: 10 } }],
      series: [
        // 2026-07-17 统一色调（用户定）：数据色只留猩红(danger)与绿(moss)，灰色退场：回测段绿、前向段猩红
        { name: "回测（可复现）", type: "line", showSymbol: false, connectNulls: false,
          data: bt, lineStyle: { color: p.moss, width: 1.4 }, itemStyle: { color: p.moss },
          markLine: { silent: true, symbol: "none",
            lineStyle: { color: p.ink, type: "dashed", width: 1 },
            label: { color: p.ink, formatter: "50 中性", fontFamily: "JetBrains Mono", fontSize: 10 },
            data: [{ yAxis: 50 }] } },
        { name: "前向台账（发布后逐日）", type: "line", showSymbol: true, symbolSize: 7, connectNulls: false,
          data: fw, lineStyle: { color: p.danger, width: 1.8 }, itemStyle: { color: p.danger } },
      ],
    };
  });

  // 短端 vs 长端（2026-07-16 用户定）：把「斜率故事」从文案搬上站：两条原始序列自己说话。
  // 展示层，非指数成分（斜率进不进分类器走注册流程，见 digest README「分类器的已知盲区」）。图注只写事实。
  chart("leaps", "ch-leaps-ends", async (p) => {
    const [lg, sent] = await Promise.all([load("leaps_gauge"), load("sentiment")]);
    const t = sent.term;
    const v1y = Object.create(null);
    lg.dates.forEach((dt, i) => { v1y[dt] = lg.vix1y[i]; });
    const long = [], front = [];
    t.dates.forEach((dt, i) => {
      if (v1y[dt] != null && t.vix9d[i] != null) {
        long.push([dt, v1y[dt]]);
        front.push([dt, t.vix9d[i]]);
      }
    });
    return {
      tooltip: tip(p, { valueFormatter: (v) => (v == null ? "--" : (+v).toFixed(2)) }),
      legend: { top: 0, textStyle: { color: p.ink, fontSize: 11 }, itemWidth: 18 },
      grid: { left: 48, right: 24, top: 34, bottom: 64 },
      xAxis: timeX(p),
      yAxis: Object.assign({ type: "value", name: "波动率点" }, baseAxis(p)),
      dataZoom: [{ type: "inside" }, { type: "slider", bottom: 6, height: 18,
        borderColor: p.border, fillerColor: "rgba(160,57,47,0.08)",
        handleStyle: { color: p.accent }, textStyle: { color: p.muted, fontSize: 10 } }],
      series: [
        { name: "1 年期 · VIX1Y", type: "line", showSymbol: false, connectNulls: false,
          data: long, lineStyle: { color: p.accent, width: 1.8 }, itemStyle: { color: p.accent } },
        { name: "9 天期 · VIX9D", type: "line", showSymbol: false, connectNulls: false,
          data: front, lineStyle: { color: p.moss, width: 1.2 }, itemStyle: { color: p.moss } },
      ],
    };
  });

  // 恐惧的远期价目表：VX 期货逐月结算曲线（真钱轨，与指数轨互证；2026-07-17 期货双源上线）
  chart("leaps", "ch-leaps-vx", async (p) => {
    const d = await load("vx_curve");
    const cats = ["VIX 现值", ...d.curve.map((c) => c[0].slice(5).replace("-", "/"))];
    const today = [d.spot_vix, ...d.curve.map((c) => c[1])];
    const wkMap = Object.create(null);
    ((d.week_ago && d.week_ago.curve) || []).forEach((w) => { wkMap[w[0]] = w[1]; });
    const wk = [null, ...d.curve.map((c) => (wkMap[c[0]] != null ? wkMap[c[0]] : null))];
    return {
      tooltip: tip(p, { valueFormatter: (v) => (v == null ? "--" : (+v).toFixed(2)) }),
      legend: { top: 0, textStyle: { color: p.ink, fontSize: 11 }, itemWidth: 18 },
      grid: { left: 48, right: 24, top: 34, bottom: 30 },
      xAxis: Object.assign({ type: "category", data: cats }, baseAxis(p)),
      yAxis: Object.assign({ type: "value", name: "波动率点", scale: true }, baseAxis(p)),
      series: [
        // 统一色调：最新=猩红、一周前=绿虚线（数据色只留 danger/moss 两色）
        { name: "最新结算", type: "line", data: today, symbolSize: 5,
          lineStyle: { color: p.danger, width: 2 }, itemStyle: { color: p.danger },
          label: { show: true, position: "top", color: p.muted, fontSize: 10,
            fontFamily: "JetBrains Mono", formatter: (o) => (o.value == null ? "" : (+o.value).toFixed(1)) } },
        { name: "一周前", type: "line", data: wk, symbolSize: 3,
          lineStyle: { color: p.moss, width: 1.2, type: "dashed" }, itemStyle: { color: p.moss } },
      ],
    };
  });

  // 谁在押注恐惧：CFTC TFF 里杠杆基金在 VIX 期货的净头寸（周频，2006→）
  chart("leaps", "ch-leaps-cot", async (p) => {
    const d = await load("cot_vix");
    const lev = d.series.map((s) => [s.date, s.lev_net]);
    const am = d.series.map((s) => [s.date, s.am_net]);
    return {
      tooltip: tip(p, { valueFormatter: (v) => (v == null ? "--" : (+v).toLocaleString("en-US")) }),
      legend: { top: 0, textStyle: { color: p.ink, fontSize: 11 }, itemWidth: 18 },
      grid: { left: 64, right: 24, top: 34, bottom: 64 },
      xAxis: timeX(p),
      yAxis: Object.assign({ type: "value", name: "净头寸（手）" }, baseAxis(p)),
      dataZoom: [{ type: "inside" }, { type: "slider", bottom: 6, height: 18,
        borderColor: p.border, fillerColor: "rgba(160,57,47,0.08)",
        handleStyle: { color: p.accent }, textStyle: { color: p.muted, fontSize: 10 } }],
      series: [
        { name: "杠杆基金", type: "line", showSymbol: false, connectNulls: false, data: lev,
          lineStyle: { color: p.accent, width: 1.6 }, itemStyle: { color: p.accent },
          markLine: { silent: true, symbol: "none", label: { show: false },
            lineStyle: { color: p.muted, type: "dashed", width: 1 }, data: [{ yAxis: 0 }] } },
        { name: "资管机构", type: "line", showSymbol: false, connectNulls: false, data: am,
          lineStyle: { color: p.moss, width: 1.1, type: "dashed" }, itemStyle: { color: p.moss } },
      ],
    };
  });

  async function renderLeapsGauge() {
    let d;
    try { d = await load("leaps_gauge"); }
    catch (e) {
      const _host12 = document.getElementById("lg-hero");
      if (!_host12) return;  // §63 瘦身路由页：该面板不在本页，跳过填充
      _host12.innerHTML =
        '<div class="card"><p style="color:var(--ink-muted);font-size:13px">数据更新中，稍后自动出现 · data updating</p></div>';
      return;
    }
    const c = d.current, e = c.expensiveness, x = c.context, t = x.term_ladder;
    const p3 = Math.round(e.p3y);
    const col = p3 >= 60 ? "var(--danger)" : p3 <= 40 ? "var(--moss)" : "var(--gold)";
    const verdict = p3 >= 66 ? "偏贵" : p3 >= 55 ? "略偏贵" : p3 >= 45 ? "中性" : p3 >= 34 ? "略偏便宜" : "偏便宜";
    const inv = t.ratio_vix_vix1y < 1;
    const sign = (v) => (v > 0 ? "+" : "") + v.toFixed(1);
    // 柱高用相对刻度：把 5 档间的差值放大（绝对刻度下 15→23 只差三成，视觉上像一样高）
    const ladderVals = [t.vix9d, t.vix, t.vix3m, t.vix6m, t.vix1y];
    const ladderHi = Math.max(...ladderVals), ladderLo = Math.min(...ladderVals);
    const barH = (v) => Math.round(16 + 84 * (v - ladderLo + 0.6) / (ladderHi - ladderLo + 0.6));
    // 2026-07-19 降级横幅：上游断供时**显式说出来**，绝不拿旧值假装新鲜。
    // 站的立身之本是「数字是那天官方说的、我们没动过」——那就必须包括「今天没拿到」也照说。
    const m = d.meta || {};
    // ⚠️ 必须单行：多行模板字面量会把换行与缩进带进 textContent，i18n 的 P 正则就永远匹配不上
    const banner = m.degraded
      ? `<p class="lg-degraded">数据源中断：本页读数停在 ${c.date}（${m.stale_days} 天前），等待上游恢复。历史台账完整未受影响。</p>`
      : "";
    const _host13 = document.getElementById("lg-hero");
    if (!_host13) return;  // §63 瘦身路由页：该面板不在本页，跳过填充
    _host13.innerHTML = `
      <div class="card">${banner}
        <div class="lg-top">
          <div class="lg-num">
            <div class="lg-big" style="color:${col}">${p3}<span>/100</span></div>
            <div class="lg-verdict" style="background:${col}">${verdict}</div>
            <div class="lg-numsub">VIX1Y 在过去 <b>3 年</b>的百分位（高=贵）</div>
            <div class="lg-numsub">即：比过去三年 ${p3}% 的交易日都贵</div>
          </div>
          <div class="lg-right">
            <div class="lg-spectrum"><div class="lg-mark" style="left:${e.p3y}%"><span>${p3}</span></div></div>
            <div class="lg-scale"><span>0 · 便宜</span><span>贵 · 100</span></div>
            <div class="lg-windows">
              <div><span>近 3 年</span><b>${p3}</b><i>主看</i></div>
              <div><span>近 5 年</span><b>${Math.round(e.p5y)}</b><i>中期</i></div>
              <div><span>全 史</span><b>${Math.round(e.pfull)}</b><i>长期</i></div>
            </div>
            <div class="lg-wnote">同一天，近 3 年偏贵、拉长看只是中性：最近三年太平静。三窗并陈，不藏选择。</div>
          </div>
        </div>
      </div>
      <div class="card lg-ladder">
        <div class="lc-name">期限阶梯 <span>短端平静 → 长端（你买的那截）最贵</span></div>
        <div class="lg-bars">
          <div class="lg-bar"><div class="lg-col" style="height:${barH(t.vix9d)}%"></div><b>${t.vix9d.toFixed(1)}</b><span>9 天</span></div>
          <div class="lg-bar"><div class="lg-col" style="height:${barH(t.vix)}%"></div><b>${t.vix.toFixed(1)}</b><span>VIX 30 天</span></div>
          <div class="lg-bar"><div class="lg-col" style="height:${barH(t.vix3m)}%"></div><b>${t.vix3m.toFixed(1)}</b><span>3 月</span></div>
          <div class="lg-bar"><div class="lg-col" style="height:${barH(t.vix6m)}%"></div><b>${t.vix6m.toFixed(1)}</b><span>6 月</span></div>
          <div class="lg-bar hi"><div class="lg-col" style="height:${barH(t.vix1y)}%"></div><b>${t.vix1y.toFixed(1)}</b><span>1 年 ★</span></div>
        </div>
        <div class="lg-wnote">五根柱 = 给 5 个期限各开一份"保费"；买 LEAPS，付的是最右那根的价。</div>
      </div>
      <div class="senti-cards lg-ctx4">
        <div class="senti-card">
          <div class="lc-name">VRP 波动税 <span>你多付的"冤枉钱"</span></div>
          <div class="lc-val">${sign(x.vrp.main)}</div>
          <div class="lc-meta"><span>vol 点 · 近 3 月 ${sign(x.vrp.small)}</span></div>
        </div>
        <div class="senti-card">
          <div class="lc-name">期限结构 <span>VIX ÷ VIX1Y</span></div>
          <div class="lc-val">${t.ratio_vix_vix1y.toFixed(2)}</div>
          <div class="lc-meta"><span>${inv ? "越远越贵 · 保得久，保费高" : "倒挂 · 眼下比一年后还贵"}</span></div>
        </div>
        <div class="senti-card">
          <div class="lc-name">Call 偏斜 SKEW <span>看涨相对看跌</span></div>
          <div class="lc-val">${Math.round(x.call_skew.pctile_full)}</div>
          <div class="lc-meta"><span>分位 · 全史 · 值 ${x.call_skew.value.toFixed(0)}</span></div>
        </div>
        <div class="senti-card">
          <div class="lc-name">实际利率 <span>10 年期 · 资金压在仓里的成本</span></div>
          <div class="lc-val">${Math.round(x.real_rate.pctile_full)}</div>
          <div class="lc-meta"><span>分位 · 全史 · ${x.real_rate.value.toFixed(2)}%</span></div>
        </div>
      </div>
      <p class="footnote src-note"><span>VIX1Y = ${c.vix1y}</span> · <span>4 context 只展示，不平均进头条</span> · <span>数据截至</span> ${c.date} (<span>${c.segment === "forward" ? "前向台账" : "回测"}</span>) · <span>描述性数据，非投资建议</span></p>`;
  }

  // 恐惧的分解（2026-07-17 自头版迁入）：与本页 4 context 去重后剩四卡：
  // P/C、VXN 纳指溢价、广度 %>200DMA、恐贪七分量（SKEW/期限结构两张与 4 context 重复，已删）。
  // 文案逐字沿用头版旧卡，i18n D key 原样命中；语言切换由文本节点翻译器接管，无需重渲。
  async function renderFearDecomp() {
    const host = document.getElementById("leaps-decomp");
    if (!host) return;
    let senti = null, breadth = null;
    try { senti = await load("sentiment"); } catch (e) {}
    try { breadth = await load("breadth"); } catch (e) {}
    if (!senti || !senti.pc) {
      host.innerHTML = '<div class="card"><p style="color:var(--ink-muted);font-size:13px">数据更新中，稍后自动出现 · data updating</p></div>';
      return;
    }
    const SUB_NAMES = {
      market_momentum_sp500: "市场动能", stock_price_strength: "股价强度",
      stock_price_breadth: "股价广度", put_call_options: "期权 Put/Call",
      market_volatility_vix: "波动率 VIX", junk_bond_demand: "垃圾债需求",
      safe_haven_demand: "避险需求",
    };
    // 2026-07-17 统一色调（用户定）：中性灰退场，两色制：恐惧侧(<45)猩红，其余绿
    const subColor = (s) => (s < 45 ? "var(--danger)" : "var(--moss)");
    const subRows = Object.entries(SUB_NAMES).map(([k, name]) => {
      const v = senti.subs && senti.subs[k];
      if (!v) return "";
      return `<div class="senti-row"><span class="senti-name">${name}</span>
        <span class="senti-bar"><i style="width:${Math.max(3, Math.min(100, v.score))}%;background:${subColor(v.score)}"></i></span>
        <b style="color:${subColor(v.score)}">${v.score.toFixed(0)}</b></div>`;
    }).join("");
    host.innerHTML = `
      <div class="senti-cards">
        <div class="senti-card">
          <div class="lc-name">Put/Call 比 <span>全市场 · 5 日均值</span></div>
          <div class="lc-val">${senti.pc.current == null ? "--" : senti.pc.current.toFixed(2)}</div>
          <div class="lc-meta"><span>近一年百分位</span> <b>${senti.pc.pctile == null ? "--" : senti.pc.pctile.toFixed(0)}</b><br><span>越高 = 买保护的人越多 = 越恐慌</span></div>
        </div>
        ${senti.vxn ? `
        <div class="senti-card">
          <div class="lc-name">纳指波动溢价 <span>VXN ÷ VIX</span></div>
          <div class="lc-val">${senti.vxn.current.toFixed(2)}</div>
          <div class="lc-meta"><span class="nw"><span>当前 VXN</span> <b>${senti.vxn.vxn.toFixed(1)}</b></span> · <span class="nw"><span>全史百分位</span> <b>${senti.vxn.pctile.toFixed(0)}</b></span><br><span>越高 = 市场为纳指波动付的保费越贵</span></div>
        </div>` : ""}
        ${breadth && breadth.current != null ? `
        <div class="senti-card">
          <div class="lc-name">市场广度 <span>标普成分股在 200 日均线上的占比</span></div>
          <div class="lc-val">${breadth.current.toFixed(0)}%</div>
          <div class="lc-meta"><span class="nw"><span>累积史百分位</span> <b>${breadth.pctile.toFixed(0)}</b></span> · <span class="nw"><span>自</span> ${breadth.since}</span><br><span>越低 = 超卖越深，历史底部常见个位数</span></div>
        </div>` : ""}
        <div class="senti-card">
          <div class="lc-name">恐贪指数的七个分量 <span>CNN 官方口径</span></div>
          ${subRows}
        </div>
      </div>
      <p class="footnote senti-note"><span>数据截至</span> ${senti.date} · CNN Fear & Greed + Cboe + Yahoo Finance · <span>每交易日收盘后自动更新</span></p>`;
  }

  // ---------------- 注册 SPY / QQQ ----------------
  chart("spy", "ch-spy-century", centuryChart("sp500_century", [{ ds: "sp500_century", name: "标普 500" }]));
  chart("spy", "ch-spy-annual", annualChart("sp500_annual"));
  chart("spy", "ch-spy-dist", distChart("sp500_distribution"));
  chart("spy", "ch-spy-holding", holdingChart("sp500_holding"));
  chart("spy", "ch-spy-rollmatrix", rollMatrixChart("sp500_rollmatrix"));
  chart("spy", "ch-spy-dailyhist", dailyHistChart("sp500_extremes"));
  chart("spy", "ch-spy-dd", ddChart("sp500_drawdowns"));
  chart("spy", "ch-spy-intra", intraChart("sp500_intrayear"));
  chart("spy", "ch-spy-cape", capeChart());
  chart("spy", "ch-spy-pettm", simpleLine("sp500_pe_ttm", "PE(TTM)", "accent", { avgLine: true }));
  chart("spy", "ch-spy-eps", simpleLine("sp500_eps_hist", "EPS(TTM)", "moss", { log: true }));
  chart("spy", "ch-spy-vol", volChart("sp500_volatility"));
  chart("spy", "ch-spy-season", seasonChart("sp500_seasonality"));
  chart("spy", "ch-spy-bullbear", bullBearChart("sp500_bullbear", "sp500_century"));
  chart("spy", "ch-spy-sectors", sectorChart("sp500_constituents"));
  chart("spy", "ch-spy-sectorw", sectorWeightChart("sp500_sector_weights"));

  chart("qqq", "ch-qqq-century", centuryChart(null, [
    { ds: "ixic_century", name: "纳指综指" }, { ds: "ndx_century", name: "纳指 100" },
  ]));
  chart("qqq", "ch-qqq-annual", annualChart("ndx_annual"));
  chart("qqq", "ch-qqq-dist", distChart("ndx_distribution"));
  chart("qqq", "ch-qqq-holding", holdingChart("ndx_holding"));
  chart("qqq", "ch-qqq-rollmatrix", rollMatrixChart("ndx_rollmatrix"));
  chart("qqq", "ch-qqq-dailyhist", dailyHistChart("ndx_extremes"));
  chart("qqq", "ch-qqq-dd", ddChart("ndx_drawdowns"));
  chart("qqq", "ch-qqq-intra", intraChart("ndx_intrayear"));
  chart("qqq", "ch-qqq-vol", volChart("ndx_volatility"));
  chart("qqq", "ch-qqq-season", seasonChart("ndx_seasonality"));
  chart("qqq", "ch-qqq-bullbear", bullBearChart("ndx_bullbear", "ndx_century"));
  chart("qqq", "ch-qqq-sectors", sectorChart("ndx_constituents"));
  chart("qqq", "ch-qqq-sectorw", sectorWeightChart("ndx_sector_weights"));

  ["tech", "fin", "consumer", "luxury"].forEach((b) => {
    chart(b, "ch-" + b + "-growth", basketGrowthChart(b));
    chart(b, "ch-" + b + "-annual", annualChart(b + "_annual"));
    chart(b, "ch-" + b + "-dd", ddChart(b + "_drawdowns"));
  });
  chart("tech", "ch-mag7", mag7Chart());
  // 科技板块锚 = QQQ，复用纳指 100（ndx）面板
  chart("tech", "ch-tech-etf", centuryChart(null, [{ ds: "ndx_century", name: "QQQ 纳指 100" }]));
  chart("tech", "ch-tech-etf-annual", annualChart("ndx_annual"));
  chart("tech", "ch-tech-etf-dd", ddChart("ndx_drawdowns"));
  chart("fin", "ch-fin-etf", centuryChart(null, [{ ds: "s_xlf_century", name: "XLF" }]));
  chart("fin", "ch-fin-etf-annual", annualChart("s_xlf_annual"));
  chart("fin", "ch-fin-etf-dd", ddChart("s_xlf_drawdowns"));
  chart("consumer", "ch-consumer-etf", centuryChart(null, [
    { ds: "s_xlp_century", name: "XLP 必需消费" }, { ds: "s_xly_century", name: "XLY 可选消费" },
  ]));
  chart("consumer", "ch-consumer-etf-annual", annualChart("s_xlp_annual"));
  chart("consumer", "ch-consumer-etf2-annual", annualChart("s_xly_annual"));

  // ---------------- 定价：月 / 年切换 ----------------
  // 事件委托：docs-i18n 切换语言时会整块替换 #panel-pricing 的 innerHTML，直接绑按钮的监听会被销毁
  document.addEventListener("click", (e) => {
    const b = e.target.closest(".pbill-btn");
    if (!b) return;
    document.querySelectorAll(".pbill-btn").forEach((x) => x.classList.toggle("active", x === b));
    const tiers = document.getElementById("pricing-tiers");
    if (tiers) tiers.classList.toggle("annual", b.dataset.bill === "y");
  });

  // ---------------- 个股与板块（30 天口径；2026-07-18） ----------------
  // ⚠️ 本段全部 30 天，与旗舰的 1 年不是一把尺：文案里已写死，改动时勿弱化。
  async function renderVolFamily() {
    const host = document.getElementById("vf-hero");
    if (!host) return;
    let d;
    try { d = await load("vol_family"); }
    catch (e) {
      host.innerHTML = '<div class="card"><p style="color:var(--ink-muted);font-size:13px">数据更新中，稍后自动出现 · data updating</p></div>';
      return;
    }
    const s = d.dispersion;
    if (!s) { host.innerHTML = ""; return; }
    const p = Math.round(s.pfull);
    const col = p >= 60 ? "var(--danger)" : p <= 40 ? "var(--moss)" : "var(--gold)";
    host.innerHTML = `
      <div class="card">
        <div class="lg-top">
          <div class="lg-num">
            <div class="lg-big" style="color:${col}">${s.current.toFixed(2)}<span>×</span></div>
            <div class="lg-verdict" style="background:${col}">第 ${p} 百分位</div>
            <div class="lg-numsub">个股平均 ÷ 大盘</div>
            <div class="lg-numsub">单只股票的保费 = 大盘的 ${s.current.toFixed(2)} 倍</div>
          </div>
          <div class="lg-right">
            <div class="lg-spectrum"><div class="lg-mark" style="left:${s.pfull}%"><span>${p}</span></div></div>
            <div class="lg-scale"><span>0 · 个股与大盘同调</span><span>各走各的 · 100</span></div>
            <div class="lg-windows">
              <div><span>当前</span><b>${s.current.toFixed(2)}×</b><i>今日</i></div>
              <div><span>历史中位</span><b>${s.median_full.toFixed(2)}×</b><i>${s.start.slice(0, 4)} 年至今</i></div>
              <div><span>近 3 年</span><b>${Math.round(s.p3y)}</b><i>百分位</i></div>
            </div>
          </div>
        </div>
        <p class="footnote src-note"><span>VIXEQ ÷ VIX</span> · <span>数据截至</span> ${s.date} · <span>样本 ${s.days} 个交易日</span> · <span>描述性数据，非投资建议</span></p>
      </div>`;
  }

  chart("leaps", "ch-vol-family", async (p) => {
    const d = await load("vol_family");
    // 只画算得出百分位的（历史不足的留空，不用短窗口冒充）；按贵贱排序，最贵在上
    const rows = d.members.filter((m) => m.pctile_available && m.p3y != null)
      .sort((a, b) => a.p3y - b.p3y);
    return {
      tooltip: tip(p, { valueFormatter: (v) => (v == null ? "--"
        // ⚠️ 函数体内的中文 JSON.stringify(getOption()) 扫不到——2026-07-18 那次
        //    全域审计正是因此漏掉这两处（英文态悬停显示「76.4 分位」）。走 translate。
        : (+v).toFixed(1) + " " + (window.MC_I18N ? MC_I18N.translate("分位") : "分位")) }),
      grid: { left: 118, right: 44, top: 22, bottom: 34 },
      // 轴名去掉：卡片标题已写「贵贱百分位」，放轴末尾会跟「100」刻度重叠（2026-07-20 用户指出）
      xAxis: Object.assign({ type: "value", min: 0, max: 100 }, baseAxis(p)),
      yAxis: Object.assign({ type: "category", data: rows.map((r) => r.label) },
        baseAxis(p), { axisLabel: { color: p.muted, fontSize: 11 } }),
      // 配色沿用 VRP 四格的定案：由浅到深猩红（#E8735A→#A0392F），纯视觉层级、不编码含义。
      // 全都 ≥60 时若一律用 danger，六根同色的墙会让 86.6 和 98.7 除了长度毫无区别。
      // 低于 50（真便宜）仍走绿 moss：红/绿的语义边界不动，只在红这一侧补层级。
      series: [{
        type: "bar", data: rows.map((r) => r.p3y), barMaxWidth: 18,
        itemStyle: {
          color: (x) => {
            if (x.value < 50) return p.moss;
            const hot = rows.filter((r) => r.p3y >= 50);
            const i = hot.findIndex((r) => r.p3y === x.value);
            const t = hot.length > 1 ? Math.max(0, i) / (hot.length - 1) : 1;
            const mix = (a, b) => Math.round(a + (b - a) * t);
            return `rgb(${mix(232, 160)},${mix(115, 57)},${mix(90, 47)})`;
          },
          borderRadius: [0, 4, 4, 0],
        },
        label: { show: true, position: "right", color: p.muted, fontSize: 11,
          fontFamily: "JetBrains Mono", formatter: (x) => x.value.toFixed(1) },
        markLine: { silent: true, symbol: "none",
          lineStyle: { color: p.ink, type: "dashed", width: 1 },
          label: { color: p.ink, formatter: "50 中性", fontSize: 10, fontFamily: "JetBrains Mono" },
          data: [{ xAxis: 50 }] },
      }],
    };
  });

  // 指数波动率家族（2026-09-15 加）：Cboe VIX/VXN/RVX/VXD/VXTLT/VVIX 的三年分位横条
  chart("leaps", "ch-vol-indices", async (p) => {
    const d = await load("vol_indices");
    const rows = d.members.filter((m) => m.p3y != null).sort((a, b) => a.p3y - b.p3y);
    const T = (x) => (window.MC_I18N ? MC_I18N.translate(x) : x);
    return {
      tooltip: tip(p, { valueFormatter: (v) => (v == null ? "--" : (+v).toFixed(1) + " " + T("分位")) }),
      grid: { left: 150, right: 44, top: 22, bottom: 34 },
      xAxis: Object.assign({ type: "value", min: 0, max: 100 }, baseAxis(p)),
      yAxis: Object.assign({ type: "category", data: rows.map((r) => `${T(r.label)} (${r.current.toFixed(1)})`) },
        baseAxis(p), { axisLabel: { color: p.muted, fontSize: 11 } }),
      series: [{
        type: "bar", data: rows.map((r) => r.p3y), barMaxWidth: 18,
        itemStyle: { color: (x) => (x.value < 50 ? p.moss : p.accent), borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: "right", color: p.muted, fontSize: 11,
          fontFamily: "JetBrains Mono", formatter: (x) => x.value.toFixed(1) },
        markLine: { silent: true, symbol: "none",
          lineStyle: { color: p.ink, type: "dashed", width: 1 },
          label: { color: p.ink, formatter: "50 " + T("中性"), fontSize: 10, fontFamily: "JetBrains Mono" },
          data: [{ xAxis: 50 }] },
      }],
    };
  });

  // 类目轴的票代前加公司 logo（2026-08-07 加）。
  // 复用站内自托管 logos/<小写票代>.png —— 与个股页大标题、表格 tbl-logo 同源，
  // 由 scripts/fetch_logos.py 每周同步，不引入新依赖、不打外部请求。
  // ⚠️ 与 HTML 侧 __logoErr 的三级兜底不同：ECharts 的 rich 图片没有 onerror，
  //    文件缺失时该格留空、票代文字照常显示 —— 是降级不是报错，可接受。
  //    （本页 13 只票的 logo 已于 2026-08-07 逐个核实存在，含 SPY/QQQ 两只 ETF。）
  // 🚨 只给「类目就是票代」的轴用。同文件 ch-vol-family 的类目是 r.label 不是票代，
  //    照抄会给非票代标签配图 —— 2026-08-07 改这处时正是靠断言数量对不上才发现。
  // 2026-09-16 加 fixed：类目轴标签整体右对齐，票代长短不一（MU 两个字母、GOOGL 五个），
  //   logo 那一列就参差成锯齿（Klay 截图点名）。fixed=true 时票代格取该图最长票代的宽度、
  //   格内左对齐 ⇒ 每行标签总宽相同 ⇒ logo 左缘对齐。三张图逐张接入，默认 false 不动旧行为。
  const logoAxisLabel = (p, rows, fixed = false) => ({
    color: p.muted, fontSize: 11,
    formatter: (tk) => `{i${tk}|}{t|${tk}}`,
    rich: Object.assign(
      { t: Object.assign({ color: p.muted, fontSize: 11, padding: [0, 0, 0, 6], verticalAlign: "middle" },
          fixed ? { width: Math.max(...rows.map((r) => r.tk.length)) * 7 + 2, align: "left" } : {}) },
      Object.fromEntries(rows.map((r) => [`i${r.tk}`, {
        backgroundColor: { image: `logos/${r.tk.toLowerCase()}.png` },
        width: 14, height: 14, verticalAlign: "middle",
      }])),
    ),
  });

  // 做空成交结构（2026-07-18）：只报位置，不报方向。
  // 🚨 做空占比高 ≠ 看空（做市商对冲/ETF 套利/可转债对冲均计入），故这张图画的是
  // 「当日占比在自己三年历史中的百分位」，而不是占比本身：占比只作 tooltip 里的原值。
  chart("leaps", "ch-short-flow", async (p) => {
    const d = await load("short_flow");
    const rows = Object.entries(d.current)
      .filter(([, v]) => v.pctile != null)
      .map(([tk, v]) => ({ tk, ...v }))
      .sort((a, b) => a.pctile - b.pctile);
    if (!rows.length) {
      return { title: { text: "历史积累中，满 250 个交易日后显示", left: "center", top: "middle",
        textStyle: { color: p.muted, fontSize: 13, fontWeight: "normal" } } };
    }
    return {
      tooltip: tip(p, { valueFormatter: (v) => (v == null ? "--"
        // ⚠️ 函数体内的中文 JSON.stringify(getOption()) 扫不到——2026-07-18 那次
        //    全域审计正是因此漏掉这两处（英文态悬停显示「76.4 分位」）。走 translate。
        : (+v).toFixed(1) + " " + (window.MC_I18N ? MC_I18N.translate("分位") : "分位")) }),
      grid: { left: 98, right: 44, top: 22, bottom: 34 },
      xAxis: Object.assign({ type: "value", min: 0, max: 100, name: "三年百分位" }, baseAxis(p)),
      yAxis: Object.assign({ type: "category", data: rows.map((r) => r.tk) },
        baseAxis(p), { axisLabel: logoAxisLabel(p, rows, true) }),
      series: [{
        type: "bar", data: rows.map((r) => r.pctile), barMaxWidth: 18,
        // 同 VRP 定案：由浅到深猩红，纯视觉层级；此处不设红绿语义：
        // 因为「占比高」本身没有好坏，绿色会被误读成「安全」。
        itemStyle: {
          color: (x) => {
            const t = rows.length > 1 ? x.dataIndex / (rows.length - 1) : 1;
            const mix = (a, b) => Math.round(a + (b - a) * t);
            return `rgb(${mix(232, 160)},${mix(115, 57)},${mix(90, 47)})`;
          },
          borderRadius: [0, 4, 4, 0],
        },
        label: { show: true, position: "right", color: p.muted, fontSize: 11,
          fontFamily: "JetBrains Mono",
          formatter: (x) => x.value.toFixed(0) + paren(rows[x.dataIndex].ratio.toFixed(0) + "%") },
        markLine: { silent: true, symbol: "none",
          lineStyle: { color: p.ink, type: "dashed", width: 1 },
          label: { color: p.ink, formatter: "50 中性", fontSize: 10, fontFamily: "JetBrains Mono" },
          data: [{ xAxis: 50 }] },
      }],
    };
  });

  // 做空持仓（2026-07-18 夜）：存量，非流量；滞后约两周，故只当背景变量。
  // 🚨 用 DTC（补仓天数 = 持仓 ÷ 日均量）而不是持仓股数：股数会随股本与成交量长期漂移，
  //    实测「持仓水平分位」跨票均值近 6 期 81.8、2024 全年 60.1，看着像在飙升；但同期
  //    DTC 分位是 63.3 vs 74.5：**两个口径指向相反**。归一化之后才是真的拥挤度。
  /* 贰 · 当前横截面：三档可切（补仓天数 / 持仓股数 / 当日流量） */
  let crossMode = "dtc";
  chart("leaps", "ch-short-interest", async (p) => {
    const [si, sf] = await Promise.all([load("short_interest"), load("short_flow")]);
    const isEN = !!(window.MC_I18N && MC_I18N.lang && MC_I18N.lang() === "en");
    const T = (s) => (window.MC_I18N ? MC_I18N.translate(s) : s);
    const rows = [];
    for (const tk of Object.keys(si.series)) {
      const c = si.current[tk] || {}, f = (sf.current || {})[tk] || {};
      let v = null, extra = "";
      if (crossMode === "dtc") { v = c.pctile_dtc; extra = c.dtc != null ? c.dtc.toFixed(2) + (isEN ? "d" : " 天") : ""; }
      else if (crossMode === "si") { v = c.pctile_si; extra = c.si != null ? (c.si / 1e6).toFixed(0) + "M" : ""; }
      else { v = f.pctile; extra = f.ratio != null ? f.ratio.toFixed(0) + "%" : ""; }
      if (v != null) rows.push({ tk, v, extra });
    }
    rows.sort((a, b) => a.v - b.v);
    const miss = Object.keys(si.series).filter((tk) => !rows.find((r) => r.tk === tk));
    const note = document.getElementById("cross-note");
    if (note) {
      note.textContent =
        (crossMode === "flow" ? T("当日做空成交占比在自身三年历史中的位置。这是流量，与另外两档的存量口径不同。")
         : crossMode === "dtc" ? T("补仓天数 = 做空持仓 ÷ 日均成交量，除掉了规模，读的是相对拥挤度。")
         : T("持仓股数的分位。注意它有非平稳问题：多数票同时逼近高位，多半是尺子的问题不是市场的问题。"))
        + (miss.length ? wideGap() + T("未显示：") + joinList(miss) + T("（历史不足，不给百分位：宁可不出数，也不出假数）") : "");
    }
    if (!rows.length) {
      return { title: { text: T("历史积累中"), left: "center", top: "middle",
        textStyle: { color: p.muted, fontSize: 13, fontWeight: "normal" } } };
    }
    return {
      tooltip: tip(p, { valueFormatter: (v) => (v == null ? "--" : (+v).toFixed(1)) }),
      grid: { left: 82, right: 92, top: 24, bottom: 32 },
      xAxis: Object.assign({ type: "value", min: 0, max: 100,
        name: isEN ? "percentile" : "百分位" }, baseAxis(p)),
      yAxis: Object.assign({ type: "category", data: rows.map((r) => r.tk) },
        baseAxis(p), { axisLabel: logoAxisLabel(p, rows) }),
      series: [{
        type: "bar", data: rows.map((r) => r.v), barMaxWidth: 18,
        // 单色由浅到深，不设红绿语义：「拥挤」本身没有好坏，绿色会被读成「安全」
        itemStyle: {
          color: (x) => {
            const t = rows.length > 1 ? x.dataIndex / (rows.length - 1) : 1;
            const mix = (a, b) => Math.round(a + (b - a) * t);
            return `rgb(${mix(232, 160)},${mix(115, 57)},${mix(90, 47)})`;
          },
          borderRadius: [0, 4, 4, 0],
        },
        label: { show: true, position: "right", color: p.muted, fontSize: 11,
          fontFamily: "JetBrains Mono",
          formatter: (x) => x.value.toFixed(0) + (rows[x.dataIndex].extra ? paren(rows[x.dataIndex].extra) : "") },
        markLine: { silent: true, symbol: "none",
          lineStyle: { color: p.ink, type: "dashed", width: 1 },
          label: { color: p.ink, formatter: isEN ? "50 neutral" : "50 中性", fontSize: 10, fontFamily: "JetBrains Mono" },
          data: [{ xAxis: 50 }] },
      }],
    };
  });
  segBind("seg-cross", (k) => { crossMode = k; rebuild("ch-short-interest"); });

  /* 叁 · 单票下钻：持仓（柱）与补仓天数（线）同图，双轴各自缩放 */
  let curTk = "NVDA";
  chart("leaps", "ch-short-single", async (p) => {
    const si = await load("short_interest");
    const sa = await shortAnalytics();
    const isEN = !!(window.MC_I18N && MC_I18N.lang && MC_I18N.lang() === "en");
    const T = (s) => (window.MC_I18N ? MC_I18N.translate(s) : s);
    const N = 48;
    const dates = si.dates.slice(-N), ser = si.series[curTk].slice(-N), dtc = si.days_to_cover[curTk].slice(-N);
    const keep = dates.map((d, i) => i).filter((i) => ser[i] != null);
    const note = document.getElementById("single-note");
    const c = si.current[curTk] || {}, co = sa.corr[curTk];
    if (note) {
      /* ⚠️ 这段曾硬编码「，」「。」「），」等全角标点，英文态下读起来是坏的；
         且 T(" 股（环比 ") 这类**带首尾空格的键在 translate() 里会被 trim 掉、永远匹配不上**（实测）。
         ∴ 键一律不留首尾空格，分隔符改用语言中性的「 · 」与半角逗号。 */
      const sep = " · ";
      note.textContent = keep.length
        ? [curTk,
           `${T("最新结算")} ${c.settle || "—"}`,
           `${T("持仓")} ${c.si != null ? (c.si / 1e6).toFixed(1) + "M" : "—"} (${c.chg != null ? (c.chg >= 0 ? "+" : "") + c.chg.toFixed(1) + "%" : "—"})`,
           `${T("补仓天数")} ${c.dtc != null ? c.dtc.toFixed(2) : "—"}`,
           `${T("股数分位")} ${c.pctile_si != null ? c.pctile_si.toFixed(0) : "—"}`,
           `${T("拥挤度分位")} ${c.pctile_dtc != null ? c.pctile_dtc.toFixed(0) : "—"}`,
          ].join(sep)
          + (co ? sep + `${T("流量变化与持仓变化的相关")} ρ=${co.chg >= 0 ? "+" : ""}${co.chg.toFixed(3)}, ${T("置换检验")} p=${co.p.toFixed(4)}${co.p < 0.05 ? T("（p<0.05，但 Bonferroni 校正后不存活）") : T("（不显著）")}` : "")
          + (c.note ? sep + c.note : "")
        : T("该标的在本窗口内无数据（代码复用切断后历史不足）");
    }
    if (!keep.length) {
      return { title: { text: T("该标的在本窗口内无数据"), left: "center", top: "middle",
        textStyle: { color: p.muted, fontSize: 13, fontWeight: "normal" } } };
    }
    return {
      tooltip: tip(p),
      legend: { data: [T("做空持仓（股数）"), T("补仓天数")], top: 0, textStyle: { color: p.muted, fontSize: 11 } },
      grid: { left: 62, right: 56, top: 34, bottom: 34 },
      xAxis: Object.assign({ type: "category", data: dates }, baseAxis(p),
        { axisLabel: { color: p.muted, fontSize: 10, interval: Math.ceil(dates.length / 8) } }),
      yAxis: [
        Object.assign({ type: "value", name: isEN ? "shares" : "股数",
          axisLabel: { color: p.muted, fontSize: 10, formatter: (v) => (v / 1e6).toFixed(0) + "M" } }, baseAxis(p)),
        Object.assign({ type: "value", name: isEN ? "days" : "天", scale: true,
          splitLine: { show: false } }, baseAxis(p)),
      ],
      series: [
        { name: T("做空持仓（股数）"), type: "bar", data: dates.map((d, i) => ser[i]),
          itemStyle: { color: p.border, borderRadius: [2, 2, 0, 0] }, barMaxWidth: 12 },
        { name: T("补仓天数"), type: "line", yAxisIndex: 1, data: dates.map((d, i) => dtc[i]),
          symbol: "none", lineStyle: { width: 2.2, color: p.accent }, connectNulls: true },
      ],
    };
  });

  /* 肆 · 散点：流量 × 持仓变化。原提案（水平 vs 变化）是零，换同类对同类才有微弱正相关 */
  let scMode = "lvl";
  chart("leaps", "ch-short-scatter", async (p) => {
    const sa = await shortAnalytics();
    const isEN = !!(window.MC_I18N && MC_I18N.lang && MC_I18N.lang() === "en");
    const T = (s) => (window.MC_I18N ? MC_I18N.translate(s) : s);
    const pts = [];
    for (const tk of Object.keys(sa.periods)) {
      const ps = sa.periods[tk];
      if (ps.length < 20) continue;
      const etf = tk === "SPY" || tk === "QQQ";
      for (let i = 1; i < ps.length; i++) {
        pts.push({ value: [scMode === "lvl" ? ps[i].fp : +(ps[i].fp - ps[i - 1].fp).toFixed(1), ps[i].sc],
                   tk, d: ps[i].d, etf });
      }
    }
    const rho = scMode === "lvl" ? sa.pooled.lvl : sa.pooled.chg;
    const setT = (id, t) => { const n = document.getElementById(id); if (n) n.textContent = t; };
    setT("sc-rho", (rho >= 0 ? "+" : "") + rho.toFixed(3));
    setT("sc-n", "n = " + sa.pooled.n + (isEN ? " settlement windows" : " 个结算窗口"));
    const big = document.getElementById("sc-rho");
    if (big) big.style.color = Math.abs(rho) < 0.05 ? p.muted : p.accent;
    const vd = document.getElementById("sc-verdict");
    if (vd) {
      vd.style.borderLeftColor = Math.abs(rho) < 0.05 ? p.muted : p.accent;
      const parts = scMode === "lvl"
        /* 删掉了原来的第三句「提案死在这里：它不是没做，是做了、数据说不行」。
           那句在讲作者的过程，不在讲市场：「提案」指什么只有作者知道，读者没见过它；
           且与章节标题「一个被数据否掉的提案」完全重复。站上卖的是读数与它的限度，
           不是「我试过什么」。(2026-07-19 用户指出) */
        ? ["这就是原提案的形式：相关系数等于零。",
           "「做空占比冲高 = 有人在建空头仓位」这个直觉，在数据上完全不成立：点云是一团圆的，没有任何方向。"]
        : ["换成同类对同类（变化对变化）才有，但很弱。",
           "而且这个弱相关不是均匀分布的：个股有，两只 ETF 精确为零。请看下一章的逐票拆解。"];
      vd.replaceChildren(...parts.map((s) => {
        const el = document.createElement("p"); el.textContent = T(s); return el;
      }));
    }
    return {
      tooltip: Object.assign(tip(p), { formatter: (o) => `${o.data.tk} ${o.data.d}<br>${o.data.value[0]}${wideGap()}${o.data.value[1]}%` }),
      grid: { left: 56, right: 20, top: 22, bottom: 46 },
      xAxis: Object.assign({ type: "value", scale: true,
        name: scMode === "lvl" ? (isEN ? "flow: avg percentile" : "流量：当期做空占比的平均分位")
                               : (isEN ? "flow change" : "流量变化：本期分位 − 上期分位"),
        nameLocation: "middle", nameGap: 28 }, baseAxis(p)),
      yAxis: Object.assign({ type: "value", scale: true,
        name: isEN ? "short interest change %" : "持仓变化 %" }, baseAxis(p)),
      series: [{
        type: "scatter", symbolSize: 6, data: pts,
        itemStyle: { color: (x) => (x.data.etf ? p.blue : p.accent), opacity: 0.5 },
        markLine: { silent: true, symbol: "none",
          lineStyle: { color: p.ink, type: "dashed", width: 1 }, data: [{ yAxis: 0 }] },
      }],
    };
  });
  segBind("seg-scatter", (k) => { scMode = k; rebuild("ch-short-scatter"); });

  /* 伍 · 显著性：逐票 ρ 与置换检验 p */
  chart("leaps", "ch-short-sig", async (p) => {
    const sa = await shortAnalytics();
    const isEN = !!(window.MC_I18N && MC_I18N.lang && MC_I18N.lang() === "en");
    const rows = Object.entries(sa.corr).map(([tk, c]) => ({ tk, ...c })).sort((a, b) => a.chg - b.chg);
    return {
      tooltip: Object.assign(tip(p), {
        formatter: (o) => {
          const r = rows[o.dataIndex];
          return `${r.tk}<br>ρ = ${r.chg.toFixed(3)}<br>p = ${r.p.toFixed(4)}<br>n = ${r.n}`;
        },
      }),
      grid: { left: 82, right: 108, top: 22, bottom: 32 },
      xAxis: Object.assign({ type: "value", scale: true,
        name: isEN ? "Spearman ρ" : "Spearman ρ" }, baseAxis(p)),
      yAxis: Object.assign({ type: "category", data: rows.map((r) => r.tk) },
        baseAxis(p), { axisLabel: logoAxisLabel(p, rows) }),
      series: [{
        type: "bar", data: rows.map((r) => r.chg), barMaxWidth: 16,
        // 标红＝p<0.05；ETF 用蓝，呼应散点图里那两只精确为零的点
        itemStyle: {
          color: (x) => (rows[x.dataIndex].p < 0.05 ? p.accent : (rows[x.dataIndex].etf ? p.blue : p.border)),
          borderRadius: 3,
        },
        label: { show: true, position: "right", color: p.muted, fontSize: 11,
          fontFamily: "JetBrains Mono",
          formatter: (x) => {
            const r = rows[x.dataIndex];
            return `${r.chg >= 0 ? "+" : ""}${r.chg.toFixed(3)}  p=${r.p.toFixed(3)}`;
          } },
        markLine: { silent: true, symbol: "none",
          lineStyle: { color: p.ink, type: "dashed", width: 1 }, data: [{ xAxis: 0 }] },
      }],
    };
  });

  /* 单票选择器：动态生成，随语言重建时保持当前选中 */
  function buildTkPicker() {
    const box = document.getElementById("tk-picker");
    if (!box || box.children.length) return;
    load("short_interest").then((si) => {
      Object.keys(si.series).forEach((tk) => {
        const b = document.createElement("button");
        b.type = "button"; b.className = "tk-chip"; b.textContent = tk;
        b.setAttribute("aria-selected", String(tk === curTk));
        b.onclick = () => {
          curTk = tk;
          [...box.children].forEach((x) => x.setAttribute("aria-selected", String(x === b)));
          rebuild("ch-short-single");
        };
        box.appendChild(b);
      });
    });
  }
  buildTkPicker();

  /* ---------------- 口径实验室（2026-07-19）：同一批数据，换把尺，结论会翻 ----------------
     广度序列**在客户端从既有 short_interest.json 现算**：不动管线、不加数据文件、
     不影响台账自核的五文件对账。算法与 2026-07-19 那次离线复核完全一致：
       每期取各标的在自身近 48 期中的百分位（样本不足 24 期不参与），再跨票平均。
     🚨 这张图存在的意义就是让人看到「持仓股数」与「补仓天数」指向相反：
        最近 6 期 81.8 vs 63.3、2024 全年 60.1 vs 74.5。
        此前曾按股数口径发过一个「连续 6 期两年高位」的观察，
        隔天用归一化口径复核发现方向是反的，已撤回。故此处**只并排陈列两把尺，
        不给方向判断**。 */
  const SI_WIN = 48, SI_MIN = 24;
  let breadthMode = "si";

  function calcBreadth(d, container) {
    const dates = d.dates, tks = Object.keys(d.series), out = [];
    for (let j = 0; j < dates.length; j++) {
      if (dates[j] < "2024-01-01") continue;
      const ps = [];
      for (const tk of tks) {
        const s = d[container][tk];
        if (!s || s[j] == null) continue;
        const hist = [];
        for (let k = Math.max(0, j - SI_WIN + 1); k <= j; k++) if (s[k] != null) hist.push(s[k]);
        if (hist.length < SI_MIN) continue;
        ps.push(hist.filter((v) => v <= s[j]).length / hist.length * 100);
      }
      if (ps.length >= 8) out.push([dates[j], +(ps.reduce((a, b) => a + b, 0) / ps.length).toFixed(1), ps.length]);
    }
    return out;
  }

  chart("leaps", "ch-short-breadth", async (p) => {
    const d = await load("short_interest");
    const rows = calcBreadth(d, breadthMode === "si" ? "series" : "days_to_cover");
    if (!rows.length) {
      return { title: { text: "历史积累中", left: "center", top: "middle",
        textStyle: { color: p.muted, fontSize: 13, fontWeight: "normal" } } };
    }
    const last6 = rows.slice(-6).reduce((a, r) => a + r[1], 0) / Math.min(6, rows.length);
    const y24 = rows.filter((r) => r[0] < "2025-01-01");
    const base = y24.length ? y24.reduce((a, r) => a + r[1], 0) / y24.length : null;
    const up = base != null && last6 > base;
    const setTxt = (id, t) => { const n = document.getElementById(id); if (n) n.textContent = t; };
    setTxt("cal-now", last6.toFixed(1));
    setTxt("cal-base", base == null ? "—" : base.toFixed(1));
    const big = document.getElementById("cal-now");
    if (big) big.style.color = up ? p.accent : p.moss;
    const vd = document.getElementById("cal-verdict");
    if (vd) {
      vd.style.borderLeftColor = up ? p.accent : p.moss;
      const T = (s) => (window.MC_I18N ? MC_I18N.translate(s) : s);
      /* 判读拆成两段：第一句是读数，第二句是它的限度。
         挤成一段时读者会把限度那半句读丢：而这一章存在的意义就是那半句。 */
      const parts = breadthMode === "si"
        ? ["按持仓股数读：最近 6 期高于 2024 年，看起来像空头在高位堆积。",
           "但股数会随股本与成交量长期漂移：请切到另一个口径再看一次。"]
        : ["按补仓天数读：除以日均成交量之后，当前低于 2024 年。",
           "绝对股数确实涨了，但成交量涨得更快，相对于流动性，空头并不比 2024 年拥挤。"];
      vd.replaceChildren(...parts.map((s) => {
        const el = document.createElement("p"); el.textContent = T(s); return el;
      }));
    }
    // ⚠️ formatter 是函数，i18n 的 D 字典扫不到它里面的中文（2026-07-18 在「天」上踩过）。
    //    按当前语言选词，图表本来就随语言重建。
    const isEN = !!(window.MC_I18N && MC_I18N.lang && MC_I18N.lang() === "en");
    const unit = isEN ? "th pct" : " 分位";
    return {
      tooltip: tip(p, { valueFormatter: (v) => (v == null ? "--" : (+v).toFixed(1) + unit) }),
      grid: { left: 48, right: 20, top: 20, bottom: 34 },
      xAxis: Object.assign({ type: "category", data: rows.map((r) => r[0]) }, baseAxis(p),
        { axisLabel: { color: p.muted, fontSize: 10, interval: Math.ceil(rows.length / 7) } }),
      yAxis: Object.assign({ type: "value", min: 0, max: 100,
        name: isEN ? "Avg percentile" : "跨票平均分位" }, baseAxis(p)),
      series: [{
        type: "line", data: rows.map((r) => r[1]), smooth: false, symbol: "none",
        lineStyle: { width: 2.2, color: p.accent },
        // ⚠️ 调色板里没有 accentSoft；不要硬编码 rgba（那是浅色态的值，暗色下会脏）。
        // 用 accent + opacity，明暗两态都跟着 token 走。
        areaStyle: { color: p.accent, opacity: 0.12 },
        markLine: { silent: true, symbol: "none",
          lineStyle: { color: p.ink, type: "dashed", width: 1 },
          label: { color: p.ink, formatter: "50", fontSize: 10, fontFamily: "JetBrains Mono" },
          data: [{ yAxis: 50 }] },
      }],
    };
  });

  /* ---------------- 做空分析：流量 × 存量（客户端现算，不新增数据文件） ----------------
     两个源都已在站上：short_flow.json（每日做空成交占比，759 天）与
     short_interest.json（双月合并短仓，205 期）。以下全部由它们派生。
     🚨 置换检验用**固定种子**的 PRNG：这是台账站，同一份数据每次打开必须得到
        完全相同的 p 值。用 Math.random 会让数字每次刷新都变，那是不可对账的。 */
  const FLOW_WIN = 756, FLOW_MIN = 250;
  let _sa = null;

  function rank(v) { const s = v.map((x, i) => i).sort((a, b) => v[a] - v[b]); const r = []; s.forEach((i, k) => { r[i] = k; }); return r; }
  function pearson(a, b) {
    const n = a.length, ma = a.reduce((x, y) => x + y, 0) / n, mb = b.reduce((x, y) => x + y, 0) / n;
    let num = 0, da = 0, db = 0;
    for (let i = 0; i < n; i++) { const x = a[i] - ma, y = b[i] - mb; num += x * y; da += x * x; db += y * y; }
    return da && db ? num / Math.sqrt(da * db) : 0;
  }
  const spearman = (a, b) => pearson(rank(a), rank(b));

  async function shortAnalytics() {
    if (_sa) return _sa;
    const [si, sf] = await Promise.all([load("short_interest"), load("short_flow")]);
    const fdates = sf.dates, di = {}; fdates.forEach((d, i) => { di[d] = i; });

    // 每日流量的滚动百分位（无前视）；先按 >10 交易日断口切掉代码复用前的历史
    const fpct = {};
    for (const tk of Object.keys(sf.series)) {
      const vals = sf.series[tk];
      const idx = []; vals.forEach((v, i) => { if (v != null) idx.push(i); });
      let cut = 0;
      for (let k = 1; k < idx.length; k++) if (idx[k] - idx[k - 1] - 1 > 10) cut = idx[k];
      const pct = new Array(vals.length).fill(null), hist = [];
      for (let i = 0; i < vals.length; i++) {
        const v = vals[i];
        if (i < cut || v == null) continue;
        if (hist.length >= FLOW_MIN) {
          const w = hist.slice(-FLOW_WIN);
          pct[i] = w.filter((x) => x <= v).length / w.length * 100;
        }
        hist.push(v);
      }
      fpct[tk] = pct;
    }

    // 结算窗口：窗口内流量分位均值 × 该期持仓变化
    const sdates = si.dates, periods = {};
    for (const tk of Object.keys(sf.series)) {
      const ser = si.series[tk], chg = si.change_pct[tk], dtc = si.days_to_cover[tk];
      if (!ser) continue;
      const ps = [];
      for (let i = 1; i < sdates.length; i++) {
        if (ser[i] == null || chg[i] == null) continue;
        const a = sdates[i - 1].replace(/-/g, ""), b = sdates[i].replace(/-/g, "");
        const win = [];
        for (const d of fdates) if (d > a && d <= b && fpct[tk][di[d]] != null) win.push(fpct[tk][di[d]]);
        if (win.length < 5) continue;
        ps.push({ d: sdates[i], fp: +(win.reduce((x, y) => x + y, 0) / win.length).toFixed(1),
                  sc: +chg[i].toFixed(1), si: ser[i], dtc: dtc[i] });
      }
      if (ps.length) periods[tk] = ps;
    }

    // 相关性 + 置换检验（固定种子）
    let seed = 20260719;
    const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
    const corr = {}, poolL = [], poolC = [], poolS = [];
    for (const tk of Object.keys(periods)) {
      const ps = periods[tk];
      if (ps.length < 20) continue;
      const lvl = [], chg2 = [], sc = [];
      for (let i = 1; i < ps.length; i++) { lvl.push(ps[i].fp); chg2.push(ps[i].fp - ps[i - 1].fp); sc.push(ps[i].sc); }
      poolL.push(...lvl); poolC.push(...chg2); poolS.push(...sc);
      const rC = spearman(chg2, sc);
      let hit = 0;
      for (let k = 0; k < 4000; k++) {
        const s2 = sc.slice();
        for (let i = s2.length - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); const t = s2[i]; s2[i] = s2[j]; s2[j] = t; }
        if (Math.abs(spearman(chg2, s2)) >= Math.abs(rC)) hit++;
      }
      corr[tk] = { lvl: +spearman(lvl, sc).toFixed(3), chg: +rC.toFixed(3),
                   p: hit / 4000, n: chg2.length, etf: tk === "SPY" || tk === "QQQ" };
    }
    _sa = { periods, corr,
            pooled: { lvl: +spearman(poolL, poolS).toFixed(3), chg: +spearman(poolC, poolS).toFixed(3), n: poolS.length } };
    return _sa;
  }

  function segBind(id, onPick) {
    document.getElementById(id)?.addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      [...e.currentTarget.children].forEach((x) => x.setAttribute("aria-selected", String(x === b)));
      onPick(b.dataset.k);
    });
  }
  const rebuild = (elId) => buildOne(elId, registry.leaps.find((r) => r.elId === elId).build);

  segBind("seg-breadth", (k) => { breadthMode = k; rebuild("ch-short-breadth"); });

  // ---------------- 启动 ----------------
  renderKStatus();
  renderValCards();
  renderLeaps();
  renderLeapsGauge();
  renderFearDecomp();
  renderVolFamily();
  renderPulse();
  renderDDTable("sp500_drawdowns", "spy-dd-table");
  renderDDTable("ndx_drawdowns", "qqq-dd-table");
  renderHoldingTable("sp500_holding", "spy-holding-table");
  renderHoldingTable("ndx_holding", "qqq-holding-table");
  renderBullBearTable("sp500_bullbear", "spy-bullbear-table");
  renderBullBearTable("ndx_bullbear", "qqq-bullbear-table");
  renderExtremesTable("sp500_extremes", "spy-extremes-table");
  renderExtremesTable("ndx_extremes", "qqq-extremes-table");
  renderConstituents("sp500_constituents", "spy-constituents");
  renderConstituents("ndx_constituents", "qqq-constituents");
  renderBasketTable("tech", "tech-table");
  renderBasketTable("fin", "fin-table");
  renderBasketTable("consumer", "consumer-table");
  renderBasketTable("luxury", "luxury-table");
  load("meta").then((m) => {
    // "数据更新于" 单独包 span 以便 i18n 词典翻译；日期语言中性
    const _host14 = document.getElementById("meta-line");
    if (!_host14) return;  // §63 瘦身路由页：该面板不在本页，跳过填充
    _host14.innerHTML =
      '<span>数据更新于</span> ' + m.updated.slice(0, 10);
    const [y, mo, dd] = m.updated.slice(0, 10).split("-");
    metaDate = `${dd}-${mo}-${y}`; // 日-月-年
    stampSources();
  }).catch(() => {});
  if (window.MC_I18N) {
    // 切语言会重建图表与头版 → 产生全新 DOM 节点，必须重扫，否则新节点没有 .rv、
    // 或有 .rv 却没人观察（旧 observer 只认旧节点），表现为切完语言整块不亮。
    MC_I18N.onChange(() => { rebuildAll(); renderPulse(); rvScan(); });
    MC_I18N.ready.then(route);
  } else route();
})();
