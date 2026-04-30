/* MY-RSS Frontend Application */

const API = "http://localhost:8000";

// ─── Router ───────────────────────────────────────────
function route() {
  const hash = location.hash.slice(1) || "/";
  document.querySelectorAll(".nav-link").forEach(a => {
    const r = a.dataset.route;
    a.classList.toggle("active", hash === r || (r !== "/" && hash.startsWith(r)));
  });

  if (hash.startsWith("/article/")) {
    showArticle(hash.split("/article/")[1]);
  } else if (hash === "/feeds") {
    showFeeds();
  } else if (hash === "/health") {
    showHealth();
  } else {
    showHome(new URLSearchParams());
  }
}

window.addEventListener("hashchange", route);
window.addEventListener("load", route);

// ─── Toast ────────────────────────────────────────────
function toast(msg, type = "") {
  const t = document.createElement("div");
  t.className = "toast " + type;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2500);
}

// ─── Render helpers ───────────────────────────────────
function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => {
    if (k === "className") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  });
  children.forEach(c => e.append(typeof c === "string" ? document.createTextNode(c) : c));
  return e;
}

function formatDate(d) {
  if (!d) return "";
  return d.replace(" (北京时间)", "");
}

// ─── Home: Article List ───────────────────────────────
async function showHome(params) {
  const app = document.getElementById("app");
  const page = parseInt(params.get("page")) || 1;
  const q = params.get("q") || "";
  const limit = 20;
  const offset = (page - 1) * limit;

  app.innerHTML = `<div class="loading">加载中...</div>`;

  let data;
  if (q) {
    const res = await fetch(`${API}/rss/search?q=${encodeURIComponent(q)}&limit=${limit}&offset=${offset}`);
    data = await res.json();
  } else {
    const res = await fetch(`${API}/rss/articles?limit=${limit}&offset=${offset}`);
    data = await res.json();
  }

  const entries = data.entries || [];
  const total = data.total || entries.length;

  app.innerHTML = "";
  app.append(
    el("div", { className: "search-bar" },
      el("input", { type: "text", id: "searchInput", placeholder: "搜索文章...", value: q,
        onkeydown: e => { if (e.key === "Enter") { location.hash = `#/?q=${encodeURIComponent(e.target.value)}&page=1`; } }
      }),
      el("button", { onclick: () => {
        const v = document.getElementById("searchInput").value;
        location.hash = `#/?q=${encodeURIComponent(v)}&page=1`;
      }}, "搜索"),
      q ? el("button", { className: "btn btn-outline", onclick: () => { location.hash = "#/"; } }, "清除") : ""
    )
  );

  if (entries.length === 0) {
    app.append(el("div", { className: "empty" },
      el("h3", {}, q ? `没有找到 "${q}" 相关文章` : "暂无文章"),
      el("p", {}, q ? "试试其他关键词" : "暂无本地文章，请先触发 RSS 抓取")
    ));
    return;
  }

  entries.forEach(entry => {
    const card = el("div", { className: "card", onclick: () => {
      location.hash = `#/article/${encodeURIComponent(entry.link)}`;
    }},
      el("div", { className: "source" }, entry.title ? "" : ""),
      el("h3", {}, entry.title || "(无标题)"),
      el("div", { className: "meta" },
        formatDate(entry.date),
        entry.ai_summary ? el("span", { className: "ai-badge" }, "AI 摘要") : ""
      ),
      el("div", { className: "summary" },
        (entry.ai_summary || entry.summary || "").slice(0, 200) + "..."
      )
    );
    app.append(card);
  });

  // Pagination
  const totalPages = Math.ceil(total / limit);
  if (totalPages > 1) {
    const pag = el("div", { className: "pagination" });
    for (let i = 1; i <= Math.min(totalPages, 10); i++) {
      const btn = el("button", { className: i === page ? "active" : "", onclick: () => {
        location.hash = `#/?q=${encodeURIComponent(q)}&page=${i}`;
      }}, String(i));
      pag.append(btn);
    }
    app.append(pag);
  }
}

// ─── Article Detail ───────────────────────────────────
async function showArticle(link) {
  const app = document.getElementById("app");
  const url = decodeURIComponent(link);
  app.innerHTML = `<div class="loading">加载中...</div>`;

  try {
    const res = await fetch(`${API}/rss/articles?limit=500&days=30`);
    const data = await res.json();
    const entry = (data.entries || []).find(e => e.link === url);

    if (!entry) {
      app.innerHTML = `<div class="empty"><h3>文章未找到</h3><p><a href="#/">返回列表</a></p></div>`;
      return;
    }

    app.innerHTML = "";
    app.append(
      el("div", { className: "article-detail" },
        el("button", { className: "btn btn-outline btn-sm", onclick: () => history.back() }, "← 返回"),
        el("h2", {}, entry.title || "(无标题)"),
        el("div", { className: "meta" }, formatDate(entry.date) || ""),
        entry.ai_summary ? el("div", { className: "ai-box" },
          el("h4", {}, "AI 摘要"),
          el("p", {}, entry.ai_summary)
        ) : el("div", { className: "ai-box" }, el("h4", {}, "AI 摘要"), el("p", {}, "暂无 AI 摘要")),
        entry.summary ? el("div", {}, el("h4", {}, "原文摘要"), el("div", { className: "content" }, entry.summary)) : "",
        el("p", { style: "margin-top: 16px;" },
          el("a", { href: entry.link, target: "_blank" }, "→ 阅读原文")
        )
      )
    );
  } catch (e) {
    app.innerHTML = `<div class="error">加载失败: ${e.message}</div>`;
  }
}

// ─── Feed Management ──────────────────────────────────
async function showFeeds() {
  const app = document.getElementById("app");

  const [feedsRes, healthRes] = await Promise.all([
    fetch(`${API}/rss/feeds`),
    fetch(`${API}/rss/feeds/health`),
  ]);
  const feeds = await feedsRes.json();
  const health = await healthRes.json();

  app.innerHTML = "";
  app.append(el("h2", { style: "margin-bottom: 16px;" }, "RSS 源管理"));

  // Stats
  app.append(el("div", { className: "stats-row" },
    el("div", { className: "stat-card" },
      el("div", { className: "value blue" }, String(health.total_feeds || feeds.feeds.length)),
      el("div", { className: "label" }, "总源数")
    ),
    el("div", { className: "stat-card" },
      el("div", { className: "value green" }, String(health.active_feeds || 0)),
      el("div", { className: "label" }, "活跃源")
    ),
    el("div", { className: "stat-card" },
      el("div", { className: "value" }, String(health.inactive_feeds || 0)),
      el("div", { className: "label" }, "无数据源")
    ),
    el("div", { className: "stat-card" },
      el("div", { className: "value" }, Object.values(health.feeds || {}).reduce((s, f) => s + f.count, 0)),
      el("div", { className: "label" }, "近7天文章")
    )
  ));

  // Import/Export buttons
  app.append(el("div", { className: "feed-actions" },
    el("label", { className: "btn", style: "cursor:pointer;" },
      "导入 OPML",
      el("input", { type: "file", accept: ".opml,.xml", style: "display:none;",
        onchange: async (e) => {
          const file = e.target.files[0];
          if (!file) return;
          const form = new FormData();
          form.append("file", file);
          try {
            const res = await fetch(`${API}/rss/feeds/import`, { method: "POST", body: form });
            const d = await res.json();
            if (res.ok) toast(d.message, "success");
            else toast(d.detail, "error");
            showFeeds();
          } catch (err) { toast("导入失败: " + err.message, "error"); }
        }
      })
    ),
    el("button", { className: "btn btn-outline", onclick: () => {
      window.open(`${API}/rss/feeds/export`, "_blank");
    }}, "导出 OPML")
  ));

  // Feed list
  app.append(el("h3", { style: "margin-top: 24px; margin-bottom: 8px;" }, "已配置的源"));
  const list = el("div", { className: "feed-list" });
  (feeds.feeds || []).forEach(url => {
    const name = (health.feeds || {})[url]?.source_name || url;
    const count = (health.feeds || {})[url]?.count || 0;
    list.append(el("div", { className: "feed-item" },
      el("div", {},
        el("div", {}, name.length > 60 ? name.slice(0, 60) + "..." : name),
        el("div", { className: "url" }, url.slice(0, 80))
      ),
      el("span", { className: `badge ${count > 0 ? "active" : "inactive"}` },
        count > 0 ? `+${count}` : "无数据"
      )
    ));
  });
  app.append(list);
}

// ─── Feed Health ──────────────────────────────────────
async function showHealth() {
  const app = document.getElementById("app");
  app.innerHTML = `<div class="loading">加载中...</div>`;

  const res = await fetch(`${API}/rss/feeds/health?days=7`);
  const data = await res.json();

  app.innerHTML = "";
  app.append(el("h2", { style: "margin-bottom: 16px;" }, "源健康状态"));

  app.append(el("div", { className: "stats-row" },
    el("div", { className: "stat-card" },
      el("div", { className: "value blue" }, String(data.total_feeds)),
      el("div", { className: "label" }, "总计")
    ),
    el("div", { className: "stat-card" },
      el("div", { className: "value green" }, String(data.active_feeds)),
      el("div", { className: "label" }, "活跃")
    ),
    el("div", { className: "stat-card" },
      el("div", { className: "value" }, String(data.inactive_feeds)),
      el("div", { className: "label" }, "无数据")
    ),
    el("div", { className: "stat-card" },
      el("div", { className: "value" }, Object.values(data.feeds).reduce((s, f) => s + f.count, 0)),
      el("div", { className: "label" }, "文章数")
    )
  ));

  if (data.inactive_feeds > 0) {
    app.append(el("h4", { style: "margin-bottom: 8px; color: 'var(--warning)'" }, "无数据的源："));
    data.inactive_feed_urls.forEach(url => {
      app.append(el("div", { className: "feed-item" },
        el("div", { className: "url" }, url),
        el("span", { className: "badge inactive" }, "无数据")
      ));
    });
  }

  app.append(el("h3", { style: "margin: 24px 0 12px;" }, "源详情"));
  const grid = el("div", { className: "health-grid" });
  Object.entries(data.feeds).forEach(([url, info]) => {
    grid.append(el("div", { className: "health-card" },
      el("div", { className: "name" }, info.source_name || url.slice(0, 50)),
      el("div", { className: "stats" }, `文章: ${info.count}`),
      el("div", { className: "stats" }, info.latest ? `最新: ${formatDate(info.latest)}` : ""),
      el("span", { className: `badge ${info.count > 0 ? "active" : "inactive"}` },
        info.count > 0 ? "活跃" : "无数据"
      )
    ));
  });
  app.append(grid);
}
