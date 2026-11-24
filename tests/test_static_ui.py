"""UI dùng CSS build sẵn (không phụ thuộc Tailwind Play CDN)."""
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"


def test_base_template_has_no_tailwind_cdn() -> None:
    html = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "cdn.tailwindcss.com" not in html
    assert "/static/css/tailwind.css" in html
    assert "/static/css/daisyui.min.css" in html
    # Thứ tự cascade: daisyui -> <style> nội bộ -> tailwind utilities (cuối head)
    daisy = html.index('href="/static/css/daisyui.min.css')
    style = html.index("\n    <style>")
    tailwind = html.index('href="/static/css/tailwind.css')
    assert daisy < style < tailwind < html.index("</head>")


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["tailwind.css", "daisyui.min.css"])
async def test_built_css_is_served_gzipped(name: str) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/static/css/{name}", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/css")
    assert resp.headers.get("content-encoding") == "gzip"
