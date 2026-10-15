"""
Unit tests for Library management API routes.
"""

from unittest.mock import MagicMock, AsyncMock
import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.routes.library import task_tracker


@pytest.fixture
def mock_service(monkeypatch):
    """Mock SubtitleService instance in app.main."""
    service = MagicMock()
    service.get_library_sections.return_value = [
        {"key": "1", "title": "Movies", "type": "movie"},
        {"key": "2", "title": "TV Shows", "type": "show"},
    ]
    service.get_library_items.return_value = {
        "section_key": "1",
        "section_title": "Movies",
        "section_type": "movie",
        "total_size": 1,
        "offset": 0,
        "limit": 50,
        "items": [
            {
                "rating_key": "101",
                "title": "Inception",
                "year": 2010,
                "type": "movie",
                "thumb": "/thumb/101.jpg",
                "summary": "Dream within a dream",
            }
        ],
    }
    service.get_show_tree.return_value = {
        "rating_key": "201",
        "title": "Severance",
        "year": 2022,
        "total_episodes": 1,
        "vi_count": 0,
        "can_translate_count": 1,
        "missing_vi_count": 1,
        "seasons": [
            {
                "rating_key": "202",
                "season_number": 1,
                "title": "Season 1",
                "episodes": [
                    {
                        "rating_key": "203",
                        "season_number": 1,
                        "episode_number": 1,
                        "title": "Good News About Hell",
                        "has_vi": False,
                        "has_en": True,
                        "can_translate": True,
                    }
                ],
            }
        ],
    }
    service.get_movie_details.return_value = {
        "rating_key": "101",
        "title": "Inception",
        "year": 2010,
        "type": "movie",
        "has_vi": True,
        "has_en": True,
        "can_translate": False,
    }
    monkeypatch.setattr("app.main.subtitle_service", service)
    return service


@pytest.mark.asyncio
async def test_get_library_sections(mock_service):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/library/sections")
        assert response.status_code == 200
        data = response.json()
        assert "sections" in data
        assert len(data["sections"]) == 2
        assert data["sections"][0]["title"] == "Movies"


@pytest.mark.asyncio
async def test_get_section_items(mock_service):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/library/sections/1/items?offset=0&limit=10")
        assert response.status_code == 200
        data = response.json()
        assert data["total_size"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["title"] == "Inception"


@pytest.mark.asyncio
async def test_get_section_items_with_filter(mock_service):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/library/sections/1/items?offset=0&limit=10&filter=can_translate")
        assert response.status_code == 200
        mock_service.get_library_items.assert_called_with(
            section_key="1",
            query=None,
            offset=0,
            limit=10,
            subtitle_filter="can_translate",
            sort=None,
            use_cache=True,
        )

        # Test sort parameter
        response2 = await client.get("/api/library/sections/1/items?offset=0&limit=10&sort=addedAt:desc")
        assert response2.status_code == 200
        mock_service.get_library_items.assert_called_with(
            section_key="1",
            query=None,
            offset=0,
            limit=10,
            subtitle_filter="missing_vi",
            sort="addedAt:desc",
            use_cache=True,
        )

@pytest.mark.asyncio
async def test_get_section_items_refresh_and_no_cache(mock_service):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Test refresh=true disables cache
        response = await client.get("/api/library/sections/1/items?refresh=true")
        assert response.status_code == 200
        mock_service.get_library_items.assert_called_with(
            section_key="1",
            query=None,
            offset=0,
            limit=50,
            subtitle_filter="missing_vi",
            sort=None,
            use_cache=False,
        )

        # Test use_cache=false disables cache
        response = await client.get("/api/library/sections/1/items?use_cache=false")
        assert response.status_code == 200
        mock_service.get_library_items.assert_called_with(
            section_key="1",
            query=None,
            offset=0,
            limit=50,
            subtitle_filter="missing_vi",
            sort=None,
            use_cache=False,
        )


@pytest.mark.asyncio
async def test_get_show_tree_cache_and_refresh_params(mock_service):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Default uses cache
        response = await client.get("/api/library/shows/201/tree")
        assert response.status_code == 200
        mock_service.get_show_tree.assert_called_with("201", use_cache=True)

        # refresh=true bypasses cache
        response = await client.get("/api/library/shows/201/tree?refresh=true")
        assert response.status_code == 200
        mock_service.get_show_tree.assert_called_with("201", use_cache=False)


@pytest.mark.asyncio
async def test_get_movie_details_cache_and_refresh_params(mock_service):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Default uses cache
        response = await client.get("/api/library/movies/101")
        assert response.status_code == 200
        mock_service.get_movie_details.assert_called_with("101", use_cache=True)

        # refresh=true bypasses cache
        response = await client.get("/api/library/movies/101?refresh=true")
        assert response.status_code == 200
        mock_service.get_movie_details.assert_called_with("101", use_cache=False)

@pytest.mark.asyncio
async def test_get_show_tree(mock_service):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/library/shows/201/tree")
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Severance"
        assert len(data["seasons"]) == 1
        assert data["seasons"][0]["episodes"][0]["can_translate"] is True


@pytest.mark.asyncio
async def test_get_movie_details(mock_service):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/library/movies/101")
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Inception"
        assert data["has_vi"] is True


@pytest.mark.asyncio
async def test_batch_upload_target_and_task_status(mock_service):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/library/batch/upload-target",
            json={"rating_keys": ["101", "102"], "use_cache": True},
        )
        assert response.status_code == 200
        data = response.json()
        assert "task_id" in data
        assert data["total"] == 2

        task_id = data["task_id"]
        status_resp = await client.get(f"/api/library/tasks/{task_id}")
        assert status_resp.status_code == 200
        task_data = status_resp.json()
        assert task_data["id"] == task_id
        assert task_data["total"] == 2


@pytest.mark.asyncio
async def test_library_ui_page(mock_service):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/library")
        assert response.status_code == 200
        assert "Plex Library Manager" in response.text


@pytest.mark.asyncio
async def test_library_ui_bulk_selection_controls(mock_service):
    """Test that /library renders multi-selection and floating bulk action bar."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/library")
        assert response.status_code == 200
        html = response.text
        # Floating bulk action bar
        assert 'id="bulkActionBar"' in html
        assert 'id="bulkSelectedCountBadge"' in html
        assert "Tìm sub đã chọn" in html
        assert "Dịch AI đã chọn" in html
        # Toolbar select all
        assert 'id="btnSelectAllPage"' in html
        # Season bulk action handler
        assert "startBatchActionForSeason" in html


@pytest.mark.asyncio
async def test_batch_upload_target_expands_shows(mock_service):
    """Test that batch upload expands show rating_keys to episodes."""
    mock_show = MagicMock()
    mock_show.type = "show"
    ep1 = MagicMock()
    ep1.ratingKey = 501
    ep2 = MagicMock()
    ep2.ratingKey = 502
    mock_show.episodes.return_value = [ep1, ep2]
    mock_service.plex_client.get_item.return_value = mock_show

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/library/batch/upload-target",
            json={"rating_keys": ["3001"], "use_cache": True},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2


@pytest.mark.asyncio
async def test_library_sections_cache_control(mock_service):
    """Test that /api/library/sections forwards use_cache and refresh flags."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res1 = await client.get("/api/library/sections")
        assert res1.status_code == 200
        mock_service.get_library_sections.assert_called_with(use_cache=True)

        res2 = await client.get("/api/library/sections?refresh=true")
        assert res2.status_code == 200
        mock_service.get_library_sections.assert_called_with(use_cache=False)

        res3 = await client.get("/api/library/sections?use_cache=false")
        assert res3.status_code == 200
        mock_service.get_library_sections.assert_called_with(use_cache=False)


@pytest.mark.asyncio
async def test_library_cache_stats_and_clear_endpoints(mock_service):
    """Test /api/library/cache/stats and /api/library/cache/clear endpoints."""
    mock_service.get_plex_cache_stats.return_value = {
        "sections": {"size": 2, "hits": 5, "misses": 1},
        "show_tree": {"size": 3, "hits": 10, "misses": 2},
    }
    mock_service.clear_plex_cache.return_value = {"cleared": "all"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        stats_res = await client.get("/api/library/cache/stats")
        assert stats_res.status_code == 200
        assert stats_res.json()["sections"]["size"] == 2

        clear_res = await client.post("/api/library/cache/clear")
        assert clear_res.status_code == 200
        assert clear_res.json() == {"ok": True, "cleared": "all"}
        mock_service.clear_plex_cache.assert_called_with(rating_key=None, section_key=None)
