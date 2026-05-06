# MY-RSS

MY-RSS 是一个基于 FastAPI 的 RSS 聚合与 AI 摘要服务，支持抓取 RSS/Atom 源、存储到 SQLite、按时间过滤、生成 AI 摘要，以及通过 API / SSE / WebSocket 提供内容访问。

这个项目适合用来学习：

- RSS 抓取与解析
- FastAPI 接口设计
- SQLite 本地存储
- 异步 I/O 与并发控制
- AI 摘要接入
- OPML 导入 / 导出
- 推荐系统基础实践

## 功能

- 抓取多个 RSS 源并按时间过滤
- 将文章存入 SQLite，支持本地查询
- 可选 AI 摘要，支持批量补齐缺失摘要
- 支持文章搜索
- 支持 RSS 源健康状态统计
- 支持 OPML 导入 / 导出
- 提供 SSE 流式接口和 WebSocket 实时更新
- 提供基础推荐接口

## 项目结构

```text
MY-RSS/
├── main.py                    # FastAPI 启动入口
├── config.ini                 # 默认 RSS 源配置，可被 config/sources.json 覆盖
├── requirements.txt           # Python 依赖
├── docker-compose.yml
├── Dockerfile
├── config/
│   └── sources.example.json   # JSON RSS 源配置示例
├── examples/
│   └── stream_demo.py
├── src/
│   ├── api.py                 # 兼容入口，保留旧导入路径
│   ├── app_setup.py           # FastAPI app、静态文件和路由挂载
│   ├── config.py
│   ├── database.py
│   ├── dependencies.py
│   ├── fetcher.py
│   ├── feed_parser.py
│   ├── models.py
│   ├── opml.py
│   ├── rss_service.py
│   ├── state_manager.py
│   ├── summarizer.py
│   ├── routes/
│   │   ├── opml.py
│   │   ├── rss.py
│   │   └── websocket.py
│   └── recommender/
├── static/
│   ├── index.html             # 当前静态前端入口
│   ├── css/style.css
│   ├── js/app.js
│   └── ws_test.html
├── tests/
└── pytest.ini
```

当前前端是由 FastAPI 直接托管的静态文件，入口是 `/static/index.html`。仓库根目录目前没有 `package.json`、`frontend/` 或 `web/` 目录，所以不需要运行 `npm install`、`npm run build` 或单独启动前端开发服务器。

## 技术栈

- Python 3.13+
- FastAPI
- SQLite
- aiohttp
- feedparser
- httpx
- pydantic v2
- pytest
- 原生 HTML / CSS / JavaScript 静态前端

## 快速开始

### 1. 克隆并进入项目

```bash
cd /home/default/Projects/MY-RSS
```

### 2. 创建并激活虚拟环境

```bash
source venv/bin/activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 配置 RSS 源

编辑 `config.ini` 的 `[rss]` 部分，填入你想订阅的 RSS 地址。

示例：

```ini
[rss]
url1 = https://feeds.bbci.co.uk/news/world/rss.xml
url2 = https://techcrunch.com/feed/

[filter]
days = 7

[headers]
user_agent = Mozilla/5.0 (compatible; MY-RSS Bot/1.0)
```

### 5. 启动服务

```bash
python main.py
```

启动后默认访问：

- 前端页面：`http://localhost:8000/static/index.html`
- API 文档：`http://localhost:8000/docs`
- 根接口：`http://localhost:8000/`

前端代码在 `static/` 目录中，接口请求使用当前页面同源地址，例如 `/rss/articles`、`/rss/feeds`、`/rss/feeds/health`、`/rss/search`、`/rss/refresh`、`/rss/summarize-missing` 和 `/rss/feeds/export`。这些路径由后端直接提供，不需要额外的 `/api` 前缀。

## Docker 运行

项目也支持 Docker：

```bash
docker compose up --build
```

服务默认映射到 `8000` 端口。

## 测试

先进入项目并激活虚拟环境：

```bash
cd /home/default/Projects/MY-RSS
source venv/bin/activate
```

日常推荐先跑较快、范围明确的测试文件：

```bash
pytest tests/test_config.py tests/test_feed_parser.py tests/test_fetcher.py tests/test_database.py -v
```

如果你只想跑 API 相关测试：

```bash
pytest tests/test_api.py -v
```

当前前端是静态 HTML/CSS/JavaScript，没有独立的 npm 测试或构建命令。修改接口对接时，优先运行相关 API 测试，并手动打开 `/static/index.html` 验证页面能正常请求后端。

提交前或做完一组改动后，再跑全量测试：

```bash
pytest -v
```

项目的 `pytest.ini` 已固定只收集 `tests/` 目录，并预留了 `unit`、`integration`、`slow` 三类标记。这样新手直接运行 `pytest` 时，不会意外收集到项目外的测试文件；如果以后新增较慢测试，可以用 `@pytest.mark.slow` 标出来，再用 `pytest -m "not slow"` 跳过。

## 主要接口

### RSS

- `GET /rss/entries`：抓取并返回 RSS 内容
- `GET /rss/articles`：读取本地 SQLite 中已保存的文章
- `GET /rss/article`：按链接获取单篇本地文章
- `POST /rss/refresh`：后台刷新 RSS
- `POST /rss/summarize-missing`：后台补齐缺失的 AI 摘要
- `GET /rss/stream`：SSE 流式获取 RSS
- `GET /rss/feeds`：查看配置的 RSS 源
- `GET /rss/state`：查看增量抓取状态
- `POST /rss/state/reset`：重置增量状态
- `GET /rss/search`：搜索文章
- `GET /rss/feeds/health`：查看 RSS 源健康状态

### OPML

- `POST /rss/feeds/import`：导入 OPML 文件
- `GET /rss/feeds/export`：导出当前订阅为 OPML

### WebSocket

- `WS /ws/rss`：接收实时 RSS 更新通知

### 推荐

- `GET /recommend/`：获取推荐文章
- `POST /recommend/articles/{article_id}/feedback`：记录反馈
- `POST /recommend/refresh`：刷新推荐索引
- `GET /recommend/popular`：获取热门文章

## 配置说明

### RSS 源配置

项目会优先读取 `config/sources.json`；如果该文件不存在，则回退读取 `config.ini`。

`config.ini` 常用字段：

- `[rss]`：RSS 源列表
- `[filter]`：默认过滤天数
- `[headers]`：抓取时使用的 User-Agent

`config/sources.example.json` 提供了 JSON 配置示例，可以复制为 `config/sources.json` 后按需修改。

### `.env`

项目会自动加载 `.env`，可配置服务端口等运行参数。

常见字段包括：

- `API_HOST`
- `API_PORT`
- `LOG_LEVEL`
- `POLLING_INTERVAL_SECONDS`

AI 摘要默认读取 OpenCode 配置中的 `xlab` provider（`~/.config/opencode/opencode.json`），模型默认为 `gpt-5.5`。项目本地 `.env` 仍可用于应用设置，但当前摘要实现不依赖 README 旧版写法中的项目本地 `API_KEY` / `API_URL`。

## 数据文件

运行时会生成或使用以下本地文件：

- `myrss.db`：SQLite 数据库
- `fetch_state.json`：增量抓取状态
- `reading_history.json`：阅读历史

这些文件都属于运行时数据，不建议提交到 Git。

## 常见问题

### 为什么页面启动后没有 AI 摘要？

AI 摘要依赖可用的摘要配置。如果摘要服务不可用，接口仍然可以返回普通 RSS 内容。

### 为什么某些 feed 没有更新？

项目会记录 ETag / Last-Modified，并优先使用条件请求。如果源站没有新内容，接口可能返回缓存结果。

### 为什么推荐结果看起来不多？

推荐系统依赖本地文章和用户交互数据。刚开始使用时，数据较少属于正常情况。

## 开发建议

如果你想继续扩展这个项目，比较值得做的方向是：

1. 把搜索升级为全文检索
2. 增加后台任务开关和健康监控面板
3. 补充更完整的推荐评估和用户反馈闭环
4. 为数据库迁移补充版本说明和回滚文档
5. 如果未来重新引入前端构建工具，再同步补充对应的 `package.json`、构建命令和测试命令

## 许可证

这个仓库目前未显式声明许可证。如果你准备公开发布，建议补充一个合适的 `LICENSE` 文件。
