import feedparser
import requests
import configparser
import pathlib
import time
import dateutil
from datetime import datetime, timedelta, timezone

BEIJING_TZ = timezone(timedelta(hours=8))
class RSSContentExtractor():
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
                print(entry.title)
            time.sleep(1)
        return entries

    def filter_by_date(self, entries: list) -> list:
        filter_entries = []
        day = int(self.config.get("filter", "days"))
        now = datetime.now(BEIJING_TZ)
        cutoff = now - timedelta(days=day)
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
        
    def display_content(self, entries: list[dict]) -> None:
        output_path = pathlib.Path(__file__).parent / 'rss_output.txt'
        with open(output_path, 'w', encoding='utf-8') as f:
            for entry in entries:
                title = entry.get('title', ' ')
                link = entry.get('link', ' ')
                summary = entry.get('summary', ' ')
                entry_date = self.get_entry_date(entry)
                date_str = entry_date.strftime('%Y-%m-%d %H:%M:%S (北京时间)') if entry_date else ' '
                content = entry.get('content', ' ')

                print(title)
                print(link)
                print(summary)
                print(date_str)
                print(content)

                f.write(f"{title}\n")
                f.write(f"{link}\n")
                f.write(f"{summary}\n")
                f.write(f"{date_str}\n")
                f.write(f"{content}\n\n")
        print(f"\n内容已保存到: {output_path}")

def main() -> None:
    extractor = RSSContentExtractor()
    urls = extractor.load_rss_feeds()
    entries = extractor.fetch_rss_entries(urls)
    filter_entries = extractor.filter_by_date(entries)
    extractor.display_content(filter_entries)


if __name__ == "__main__":
    main()