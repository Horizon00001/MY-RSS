const API = window.location.origin;
const state = {
  route: "/",
  query: "",
  page: 1,
  limit: 18,
  entries: [],
  selected: null,
  feeds: [],
  health: null,
};

function $(id) {
  return document.getElementById(id);
}

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function toast(message, type = "info") {
  const host = $("toastHost");
  if (!host) return;
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  node.textContent = message;
  host.appendChild(node);
  setTimeout(() => {
    node.style.opacity = "0";
    node.style.transform = "translateY(6px)";
    setTimeout(() => node.remove(), 180);
  }, 2200);
}

function formatDate(input) {
  if (!input) return "未知时间";
  return String(input).replace(" (北京时间)", "");
}

function parseRoute() {
  const raw = location.hash.slice(1) || "/";
  const [path, queryString] = raw.split("?");
  return { path, params: new URLSearchParams(queryString || "") };
}

function setActiveNav(route) {
  document.querySelectorAll(".nav-link").forEach((link) => {
    const target = link.dataset.route;
    link.classList.toggle("active", route === target || (target !== "/" && route.startsWith(target)));
  });
}

function setViewMeta(title, hint) {
  $("viewTitle").textContent = title;
  $("viewHint").textContent = hint;
}

function setOverview(metrics) {
  const el = $("overview");
  el.innerHTML = `
    <div class="metric">
      <div class="metric-label">文章总数</div>
      <div class="metric-value">${metrics.articles}</div>
    </div>
    <div class="metric">
      <div class="metric-label">RSS 源</div>
      <div class="metric-value">${metrics.feeds}</div>
    </div>
    <div class="metric">
      <div class="metric-label">健康源</div>
      <div class="metric-value">${metrics.active}</div>
    </div>
    <div class="metric">
      <div class="metric-label">AI 摘要</div>
      <div class="metric-value">${metrics.summaries}</div>
    </div>
  `;
}

function renderFeedList(items) {
  const host = $("feedList");
  if (!items.length) {
    host.innerHTML = `<div class="feed-item"><div class="feed-name">暂无源</div><div class="feed-url">请先导入 OPML 或配置 RSS 源。</div></div>`;
    return;
  }

  host.innerHTML = items.map((feed) => {
    const health = state.health?.feeds?.[feed.url] || {};
    const count = health.count || 0;
    const badgeClass = count > 0 ? "good" : "bad";
    const badgeText = count > 0 ? `${count} 篇` : "无数据";
    return `
      <div class="feed-item">
        <div class="feed-name">${escapeHtml(feed.name || feed.url)}</div>
        <div class="feed-url">${escapeHtml(feed.url)}</div>
        <div class="badge ${badgeClass}">${badgeText}</div>
      </div>
    `;
  }).join("");
}

function renderArticleList(items) {
  const host = $("articleList");
  if (!items.length) {
    host.innerHTML = `<div class="feed-item"><div class="feed-name">暂无文章</div><div class="feed-url">试试刷新 RSS，或者搜索别的关键词。</div></div>`;
    $("pagination").innerHTML = "";
    return;
  }

  host.innerHTML = items.map((entry, index) => {
    const active = state.selected?.link === entry.link ? "active" : "";
    const snippet = (entry.ai_summary || entry.summary || "").slice(0, 180);
    return `
      <article class="article-item ${active}" data-index="${index}">
        <div class="article-title">${escapeHtml(entry.title || "(无标题)")}</div>
        <div class="article-meta">
          <span>${escapeHtml(formatDate(entry.date))}</span>
          ${entry.ai_summary ? '<span class="badge good">AI 摘要</span>' : ""}
        </div>
        <div class="article-snippet">${escapeHtml(snippet || "没有摘要")}${snippet.length >= 180 ? "…" : ""}</div>
      </article>
    `;
  }).join("");

  host.querySelectorAll(".article-item").forEach((node) => {
    node.addEventListener("click", () => {
      const entry = items[Number(node.dataset.index)];
      selectArticle(entry);
    });
  });
}

function renderPagination(total) {
  const host = $("pagination");
  const totalPages = Math.max(1, Math.ceil(total / state.limit));
  if (totalPages <= 1) {
    host.innerHTML = "";
    return;
  }

  const pages = [];
  const max = Math.min(totalPages, 8);
  for (let i = 1; i <= max; i += 1) {
    pages.push(`<button class="${i === state.page ? "active" : ""}" data-page="${i}">${i}</button>`);
  }
  host.innerHTML = pages.join("");
  host.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.page = Number(btn.dataset.page);
      const route = state.query ? `#/?q=${encodeURIComponent(state.query)}&page=${state.page}` : `#/page/${state.page}`;
      location.hash = route;
    });
  });
}

function renderDetail(entry) {
  const host = $("articleDetail");
  if (!entry) {
    host.className = "detail-empty";
    host.innerHTML = "选择一篇文章查看内容、摘要和原文链接。";
    $("detailSummary").textContent = "未选择文章";
    return;
  }

  host.className = "detail";
  $("detailSummary").textContent = formatDate(entry.date);

  host.innerHTML = `
    <div class="detail-title">${escapeHtml(entry.title || "(无标题)")}</div>
    <div class="detail-meta">
      <span>${escapeHtml(formatDate(entry.date))}</span>
      ${entry.ai_summary ? '<span class="badge good">AI 摘要</span>' : '<span class="badge bad">暂无 AI</span>'}
    </div>
    <div class="detail-section">
      <h3>AI 摘要</h3>
      <div class="detail-copy">${escapeHtml(entry.ai_summary || "暂无 AI 摘要")}</div>
    </div>
    <div class="detail-section">
      <h3>原文摘要</h3>
      <div class="detail-copy">${escapeHtml(entry.summary || "暂无原文摘要")}</div>
    </div>
    <div class="detail-section">
      <h3>正文</h3>
      <div class="detail-copy">${escapeHtml(entry.content || "暂无正文内容")}</div>
    </div>
    <a class="detail-link" href="${entry.link || "#"}" target="_blank" rel="noreferrer">打开原文</a>
  `;
}

async function fetchJson(url, options = {}) {
  const res = await fetch(url, options);
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data?.detail || data?.message || "请求失败");
  }
  return data;
}

async function loadOverview() {
  const [feeds, health, articles] = await Promise.all([
    fetchJson(`${API}/rss/feeds`),
    fetchJson(`${API}/rss/feeds/health`),
    fetchJson(`${API}/rss/articles?limit=1`),
  ]);
  state.feeds = (feeds.feeds || []).map((url) => ({ url, name: url }));
  state.health = health;
  $("countArticles").textContent = String(articles.total || 0);
  $("countFeeds").textContent = String(feeds.feeds?.length || 0);
  $("countActive").textContent = String(health.active_feeds || 0);

  setOverview({
    articles: articles.total || 0,
    feeds: feeds.feeds?.length || 0,
    active: health.active_feeds || 0,
    summaries: Object.values(health.feeds || {}).filter((item) => item.count > 0).length,
  });

  renderFeedList(state.feeds);
}

async function loadArticles() {
  const query = state.query.trim();
  const offset = (state.page - 1) * state.limit;
  const url = query
    ? `${API}/rss/search?q=${encodeURIComponent(query)}&limit=${state.limit}&offset=${offset}`
    : `${API}/rss/articles?limit=${state.limit}&offset=${offset}`;
  const data = await fetchJson(url);

  state.entries = data.entries || [];
  $("articleSummary").textContent = query ? `搜索结果 ${data.total || 0} 条` : `最近文章 ${data.total || 0} 条`;
  renderArticleList(state.entries);
  renderPagination(data.total || state.entries.length);

  if (!state.selected && state.entries.length) {
    selectArticle(state.entries[0], false);
  }
}

async function selectArticle(entry, syncHash = true) {
  state.selected = entry;
  renderDetail(entry);
  renderArticleList(state.entries);
  if (syncHash) {
    location.hash = `#/article/${encodeURIComponent(entry.link)}`;
  }
}

async function loadArticleByLink(link) {
  const data = await fetchJson(`${API}/rss/article?link=${encodeURIComponent(link)}`);
  state.selected = data;
  renderDetail(data);
  renderArticleList(state.entries);
}

async function refreshRSS() {
  const data = await fetchJson(`${API}/rss/refresh`, { method: "POST" });
  toast(data.message || "RSS 刷新已开始", "success");
}

async function fillMissingSummary() {
  const data = await fetchJson(`${API}/rss/summarize-missing`, { method: "POST" });
  toast(data.message || "AI 摘要补齐已开始", "success");
}

function exportOPML() {
  window.open(`${API}/rss/feeds/export`, "_blank", "noopener");
}

async function showHome(params) {
  state.route = "/";
  state.query = params.get("q") || "";
  state.page = Number(params.get("page") || "1");
  $("searchInput").value = state.query;
  setActiveNav("/");
  setViewMeta("文章", "搜索、刷新、查看摘要和原文");
  $("feedSummary").textContent = state.query ? "搜索模式" : "全部 RSS 源";
  await loadOverview();
  await loadArticles();
}

async function showFeeds() {
  state.route = "/feeds";
  setActiveNav("/feeds");
  setViewMeta("源管理", "导入、导出和查看各源文章数量");
  const data = await fetchJson(`${API}/rss/feeds`);
  const health = await fetchJson(`${API}/rss/feeds/health`);
  state.feeds = (data.feeds || []).map((url) => ({ url, name: url }));
  state.health = health;
  $("feedSummary").textContent = `${data.feeds?.length || 0} 个源`;
  $("articleSummary").textContent = "源管理模式";
  $("articleList").innerHTML = "";
  $("pagination").innerHTML = "";
  renderFeedList(state.feeds);
  renderDetail(null);
  setOverview({
    articles: Object.values(health.feeds || {}).reduce((sum, item) => sum + (item.count || 0), 0),
    feeds: data.feeds?.length || 0,
    active: health.active_feeds || 0,
    summaries: Object.values(health.feeds || {}).filter((item) => item.count > 0).length,
  });
}

async function showHealth() {
  state.route = "/health";
  setActiveNav("/health");
  setViewMeta("健康状态", "查看各源最近抓取和缓存状态");
  const health = await fetchJson(`${API}/rss/feeds/health?days=7`);
  state.health = health;
  $("feedSummary").textContent = "健康状态";
  $("articleSummary").textContent = "健康监控模式";
  $("articleList").innerHTML = "";
  $("pagination").innerHTML = "";
  renderFeedList(state.feeds);
  renderDetail(null);
  setOverview({
    articles: Object.values(health.feeds || {}).reduce((sum, item) => sum + (item.count || 0), 0),
    feeds: health.total_feeds || 0,
    active: health.active_feeds || 0,
    summaries: Object.values(health.feeds || {}).filter((item) => item.count > 0).length,
  });
}

async function route() {
  const { path, params } = parseRoute();
  try {
    if (path.startsWith("/article/")) {
      state.route = path;
      setActiveNav("/");
      setViewMeta("文章详情", "查看原文摘要、AI 摘要和正文");
      const link = decodeURIComponent(path.replace("/article/", ""));
      await loadOverview();
      await loadArticles();
      await loadArticleByLink(link);
      return;
    }
    if (path === "/feeds") {
      await showFeeds();
      return;
    }
    if (path === "/health") {
      await showHealth();
      return;
    }
    await showHome(params);
  } catch (err) {
    toast(err.message, "error");
  }
}

function wireControls() {
  $("refreshBtn").addEventListener("click", async () => {
    try {
      await refreshRSS();
    } catch (err) {
      toast(err.message, "error");
    }
  });
  $("summaryBtn").addEventListener("click", async () => {
    try {
      await fillMissingSummary();
    } catch (err) {
      toast(err.message, "error");
    }
  });
  $("exportBtn").addEventListener("click", exportOPML);
  $("searchBtn").addEventListener("click", () => {
    state.page = 1;
    location.hash = `#/?q=${encodeURIComponent($("searchInput").value || "")}&page=1`;
  });
  $("searchInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      $("searchBtn").click();
    }
  });
}

window.addEventListener("hashchange", route);
window.addEventListener("load", () => {
  wireControls();
  route();
});
