import feedparser
import requests
import configparser
import pathlib
import time
import dateutil
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="RSS内容提取API", description="从RSS源提取和过滤内容的API服务")

BEIJING_TZ = timezone(timedelta(hours=8))

class RSSEntry(BaseModel):
    title: str
    link: str
    summary: str
    date: Optional[str]
    content: str

class RSSResponse(BaseModel):
    total: int
    entries: list[RSSEntry]

class RSSExtractorAPI:
    def __init__(self):
        self.config_path = pathlib.Path(__file__).parent / 'config.ini'
        self.config = configparser.ConfigParser()
        self.config.read(self.config_path, encoding="utf-8")

    def load_rss_feeds(self) -> list[str]:
        urls = []
        for _, url in self.config.items("rss"):
            urls.append(url)
        return urls

    def fetch_rss_entries(self, urls: list[str]) -> list[dict]:
        headers = {'User-Agent': self.config.get('headers', 'user_agent')}
        entries = []
        for url in urls:    
            response = requests.get(url, headers=headers)
            feed = feedparser.parse(response.text)
            for entry in feed.entries:
                entries.append(entry)
            time.sleep(1)
        return entries

    def filter_by_date(self, entries: list, days: int = None) -> list:
        filter_entries = []
        if days is None:
            days = int(self.config.get("filter", "days"))
        now = datetime.now(BEIJING_TZ)
        cutoff = now - timedelta(days=days)
        for entry in entries:
            entry_date = self.get_entry_date(entry)
            if entry_date and entry_date > cutoff:
                filter_entries.append(entry)
        return filter_entries

    def get_entry_date(self, entry) -> datetime | None:
        for field in ['updated', 'published', 'date', 'pubDate']:
            if hasattr(entry, field):
                try:
                    parsed = dateutil.parser.parse(getattr(entry, field))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    return parsed.astimezone(BEIJING_TZ)
                except Exception:
                    continue
        return None

    def format_entry(self, entry) -> dict:
        entry_date = self.get_entry_date(entry)
        return {
            "title": entry.get('title', ''),
            "link": entry.get('link', ''),
            "summary": entry.get('summary', ''),
            "date": entry_date.strftime('%Y-%m-%d %H:%M:%S (北京时间)') if entry_date else None,
            "content": entry.get('content', '')
        }

extractor = RSSExtractorAPI()

@app.get("/", summary="API根路径")
async def root():
    return {"message": "RSS内容提取API", "docs": "/docs"}

@app.get("/rss/entries", response_model=RSSResponse, summary="获取RSS内容")
async def get_rss_entries(
    days: int = Query(default=None, description="过滤最近几天的内容,不传则使用配置文件中的值"),
    limit: int = Query(default=None, description="返回条目数量限制")
):
    try:
        urls = extractor.load_rss_feeds()
        entries = extractor.fetch_rss_entries(urls)
        filtered_entries = extractor.filter_by_date(entries, days)
        
        formatted = [extractor.format_entry(e) for e in filtered_entries]
        
        if limit:
            formatted = formatted[:limit]
        
        return RSSResponse(total=len(formatted), entries=formatted)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/rss/feeds", summary="获取配置的RSS源列表")
async def get_rss_feeds():
    try:
        urls = extractor.load_rss_feeds()
        return {"feeds": urls}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
