"""MY-RSS - RSS feed extraction and AI summarization service."""

from src.api import app
from src.database import init_db

@app.on_event("startup")
async def startup_event():
    """Initialize database on startup."""
    init_db()
    print("Database initialized")

if __name__ == "__main__":
    import uvicorn
    from src.config import settings
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)
