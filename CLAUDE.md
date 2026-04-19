# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MY-RSS is an RSS feed extraction and AI summarization service built with FastAPI. It fetches RSS feeds concurrently, filters entries by date, and generates AI summaries using DeepSeek API.

## Run

```bash
cd /root/Projects/MY-RSS
source venv/bin/activate
python main.py
```

API docs at `http://localhost:8000/docs`

## Test

```bash
source venv/bin/activate
pytest -v
```

## Architecture

The refactored `src/` module follows clean architecture:

| Module | Responsibility |
|--------|---------------|
| `config.py` | Unified settings from `.env` (API creds) + `config.ini` (RSS feeds/filters) |
| `fetcher.py` | Async HTTP fetching with semaphore control, yields entries as they arrive |
| `feed_parser.py` | RSS/Atom parsing, date extraction, filtering by date range |
| `summarizer.py` | DeepSeek API integration for AI summaries with batch concurrency |
| `state_manager.py` | Tracks last fetch time for incremental updates |
| `models.py` | Pydantic request/response models |
| `api.py` | FastAPI routes - fetch pipeline with optional AI summarization |

### Data Flow
```
RSS feeds → Fetcher (async) → FeedParser (filter by date) → Summarizer (AI, optional) → API response
```

### Legacy Code
- `rss_api.py` and `rss源的内容提取.py` are the original monolithic implementations - still present for backward compatibility
- `ai_summarizer.py` re-exports `src/summarizer.py` as `RSSSummarizer` for legacy imports

## Configuration

**`.env`** - API credentials:
- `API_KEY` - DeepSeek API key
- `API_URL` - DeepSeek API endpoint

**`config.ini`** - RSS sources:
- `[rss]` section - feed URLs
- `[filter]` section - `days` filter (default: 2)
- `[headers]` section - user agent string

## Key Implementation Notes

- **Beijing timezone (UTC+8)** is used for all date filtering and display
- **Semaphore limit** (default 20) controls concurrent fetches to avoid overwhelming servers
- **AI summarization** is batched (10 entries/batch) with max 20 concurrent API calls
- **State file** (`fetch_state.json`) persists last fetch timestamp for incremental updates

## Recommendation System

MY-RSS includes a hybrid recommendation system combining TF-IDF content similarity and collaborative filtering.

### Architecture

```
src/recommender/
├── __init__.py           # Module exports
├── api.py                # FastAPI routes for recommendations
├── behavior_tracker.py   # Tracks user interactions
├── collaborative.py      # User-based collaborative filtering
├── hybrid_recommender.py # Combines TF-IDF + collaborative
├── models.py             # Data models (Article, UserInteraction)
└── tfidf.py              # TF-IDF content similarity
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/recommend/` | GET | Get personalized recommendations |
| `/recommend/articles/{id}/feedback` | POST | Record user feedback |
| `/recommend/refresh` | POST | Force refresh recommendation index |
| `/recommend/popular` | GET | Get popular articles |

### Recommendation Algorithm

- **TF-IDF Content Similarity** (weight: α=0.6): Finds articles similar to what user has interacted with
- **Collaborative Filtering** (weight: 1-α=0.4): Finds articles liked by similar users
- **Curated Pool**: 100 high-quality sources for exploration
- **Cold Start**: Falls back to recent/popular articles for new users

### Feedback Actions

- `view`: User viewed the article (+1)
- `bookmark`: User bookmarked (+3)
- `share`: User shared (+4)
- `skip`: User quickly skipped (-1)
- `not_interested`: Explicit negative feedback (-2), lowers同类内容权重

## Software Engineering Concepts Demonstrated

### 1. Clean Architecture / Separation of Concerns
Each module has a single responsibility:
- `fetcher.py` - HTTP 获取，与业务逻辑解耦
- `feed_parser.py` - RSS 解析，与传输层解耦
- `summarizer.py` - AI 调用，与数据处理解耦

### 2. Dependency Injection
通过 `get_summarizer()` 函数动态获取依赖，便于测试和替换实现：
```python
def get_summarizer() -> Optional[Summarizer]:
    try:
        return Summarizer()
    except ValueError:
        return None
```

### 3. Pipeline Pattern (流水线模式)
使用 `asyncio.Queue` 实现生产者-消费者流水线，fetch 和 summarize 并行执行：
```python
queue = asyncio.Queue(maxsize=100)
# fetcher 生产，summarizer 消费
```

### 4. Backward Compatibility (向后兼容)
通过适配器模式保持旧 API 兼容：
```python
# ai_summarizer.py
from src.summarizer import Summarizer as _Summarizer
RSSSummarizer = _Summarizer  # 旧接口
```

### 5. State Management (状态管理)
`StateManager` 封装状态持久化，支持增量更新：
```python
state_manager.last_fetch  # 读取
state_manager.update_last_fetch()  # 写入
```

### 6. Builder Pattern (流式生成器)
`fetch_rss_streaming()` 使用 async generator 模式，边抓取边 yield 结果

### 7. Semaphore (信号量)
控制并发数量的经典用法：
```python
self._semaphore = asyncio.Semaphore(max_concurrent)
async with self._semaphore:
    ...
```

### 8. Retry with Exponential Backoff
AI 调用失败时指数退避重试：
```python
time.sleep(2 ** attempt)  # 1s, 2s, 4s
```

### 9. Configuration Externalization
配置与代码分离，支持多数据源（`.env` + `config.ini`）
