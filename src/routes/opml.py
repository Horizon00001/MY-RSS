"""OPML import and export routes."""

from xml.etree import ElementTree as ET

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

from ..config import settings
from ..opml import generate_opml, import_feeds_to_config, parse_opml

router = APIRouter()

@router.post("/rss/feeds/import", summary="OPML导入")
async def import_opml(
    file: UploadFile = File(..., description="OPML文件 (.opml / .xml)"),
):
    """
    Import RSS feeds from an OPML file. Accepts .opml or .xml files
    exported from other RSS readers like Feedly, Inoreader, NetNewsWire.
    Returns count of added/skipped feeds.
    """
    if not file.filename or not file.filename.endswith((".opml", ".xml")):
        raise HTTPException(
            status_code=400,
            detail="请上传 .opml 或 .xml 文件",
        )

    try:
        content = await file.read()
        feeds = parse_opml(content)
        result = import_feeds_to_config(feeds)
    except ET.ParseError as e:
        raise HTTPException(
            status_code=400,
            detail=f"OPML 解析失败: {e}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"导入失败: {e}",
        )

    return {
        "message": f"导入完成: 新增 {result['added']} 条, 跳过 {result['skipped']} 条重复",
        **result,
    }


@router.get("/rss/feeds/export", summary="OPML导出")
async def export_opml():
    """
    Export all configured RSS feeds as an OPML file.
    Can be imported into Reeder, NetNewsWire, Feedly, Inoreader, etc.
    """
    if not settings.rss_feeds:
        raise HTTPException(status_code=404, detail="没有配置的RSS源")

    xml_content = generate_opml(settings.rss_feeds)
    return PlainTextResponse(
        content=xml_content,
        media_type="application/xml",
        headers={
            "Content-Disposition": 'attachment; filename="myrss_feeds.opml"'
        },
    )
