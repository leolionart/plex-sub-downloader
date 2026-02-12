"""
Unit tests for SubsourceClient.

Mocks HTTP requests để test logic mà không gọi Subsource API thật.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from pathlib import Path
import zipfile

import httpx

from app.clients.subsource_client import SubsourceClient, SubsourceClientError
from app.models.subtitle import SubtitleSearchParams, SubtitleResult


@pytest.fixture
def subsource_client():
    """SubsourceClient instance."""
    return SubsourceClient()


@pytest.fixture
def mock_subtitle_result():
    """Mock SubtitleResult."""
    return SubtitleResult(
        id="12345",
        name="Movie.2024.WEB-DL.Vi.srt",
        language="vi",
        download_url="https://subsource.net/download/12345",
        release_info="WEB-DL",
        uploader="TestUploader",
        rating=8.5,
        downloads=1234,
        quality_type="retail",
    )


@pytest.mark.asyncio
class TestSearchSubtitles:
    """Test search_subtitles method."""

    async def test_search_by_imdb_id(self, subsource_client):
        """Test search bằng IMDb ID."""
        params = SubtitleSearchParams(
            language="vi",
            title="The Matrix",
            year=1999,
            imdb_id="tt0133093",
        )

        # Mock HTTP response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {
                    "id": "12345",
                    "name": "The.Matrix.1999.BluRay.Vi.srt",
                    "language": "vi",
                    "download_url": "https://subsource.net/download/12345",
                    "release_info": "BluRay",
                    "rating": 9.0,
                    "downloads": 5000,
                }
            ]
        }
        mock_response.raise_for_status = Mock()

        subsource_client._client.get = AsyncMock(return_value=mock_response)

        results = await subsource_client.search_subtitles(params)

        assert len(results) > 0
        assert results[0].language == "vi"

    async def test_search_by_title(self, subsource_client):
        """Test search bằng title + year."""
        params = SubtitleSearchParams(
            language="vi",
            title="Breaking Bad",
            year=2008,
            season=1,
            episode=1,
        )

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": []}
        mock_response.raise_for_status = Mock()

        subsource_client._client.get = AsyncMock(return_value=mock_response)

        results = await subsource_client.search_subtitles(params)

        assert isinstance(results, list)

    async def test_search_no_results(self, subsource_client):
        """Test search không tìm thấy kết quả."""
        params = SubtitleSearchParams(
            language="vi",
            title="Nonexistent Movie",
        )

        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.raise_for_status = Mock(
            side_effect=httpx.HTTPStatusError("Not found", request=Mock(), response=mock_response)
        )

        subsource_client._client.get = AsyncMock(return_value=mock_response)

        # Should return empty list instead of raising
        results = await subsource_client._search_by_title(params)
        assert results == []


class TestRankAndFilter:
    """Test _rank_and_filter method."""

    def test_filter_by_language(self, subsource_client):
        """Test filter subtitles theo language."""
        results = [
            SubtitleResult(
                id="1", name="sub1.srt", language="vi",
                download_url="http://test.com/1", quality_type="retail"
            ),
            SubtitleResult(
                id="2", name="sub2.srt", language="en",
                download_url="http://test.com/2", quality_type="retail"
            ),
            SubtitleResult(
                id="3", name="sub3.srt", language="vi",
                download_url="http://test.com/3", quality_type="translated"
            ),
        ]

        params = SubtitleSearchParams(language="vi")

        filtered = subsource_client._rank_and_filter(results, params)

        assert len(filtered) == 2
        assert all(r.language == "vi" for r in filtered)

    def test_sort_by_priority(self, subsource_client):
        """Test sort theo priority score."""
        results = [
            SubtitleResult(
                id="1", name="ai.srt", language="vi",
                download_url="http://test.com/1", quality_type="ai"
            ),
            SubtitleResult(
                id="2", name="retail.srt", language="vi",
                download_url="http://test.com/2", quality_type="retail"
            ),
            SubtitleResult(
                id="3", name="translated.srt", language="vi",
                download_url="http://test.com/3", quality_type="translated"
            ),
        ]

        params = SubtitleSearchParams(language="vi")

        sorted_results = subsource_client._rank_and_filter(results, params)

        # Should be sorted: retail > translated > ai
        assert sorted_results[0].quality_type == "retail"
        assert sorted_results[1].quality_type == "translated"
        assert sorted_results[2].quality_type == "ai"


@pytest.mark.asyncio
class TestDownloadSubtitle:
    """Test download_subtitle method."""

    async def test_download_srt_file(self, subsource_client, mock_subtitle_result, tmp_path):
        """Test download direct .srt file."""
        srt_content = b"1\n00:00:00,000 --> 00:00:05,000\nTest subtitle\n"

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.content = srt_content
        mock_response.raise_for_status = Mock()

        subsource_client._client.get = AsyncMock(return_value=mock_response)

        srt_path = await subsource_client.download_subtitle(
            mock_subtitle_result,
            tmp_path,
        )

        assert srt_path.exists()
        assert srt_path.suffix == ".srt"
        assert srt_path.read_bytes() == srt_content

    async def test_download_zip_file(self, subsource_client, mock_subtitle_result, tmp_path):
        """Test download và extract ZIP archive."""
        # Create mock ZIP file
        zip_path = tmp_path / "temp.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("subtitle.srt", "Test subtitle content")

        zip_content = zip_path.read_bytes()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/zip"}
        mock_response.content = zip_content
        mock_response.raise_for_status = Mock()

        subsource_client._client.get = AsyncMock(return_value=mock_response)

        srt_path = await subsource_client.download_subtitle(
            mock_subtitle_result,
            tmp_path,
        )

        assert srt_path.exists()
        assert srt_path.suffix == ".srt"
        assert "Test subtitle content" in srt_path.read_text()

    async def test_download_http_error(self, subsource_client, mock_subtitle_result, tmp_path):
        """Test download với HTTP error."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.raise_for_status = Mock(
            side_effect=httpx.HTTPStatusError("Server error", request=Mock(), response=mock_response)
        )

        subsource_client._client.get = AsyncMock(return_value=mock_response)

        with pytest.raises(SubsourceClientError, match="Download failed"):
            await subsource_client.download_subtitle(mock_subtitle_result, tmp_path)


class TestDetectQualityType:
    """Test _detect_quality_type method."""

    def test_detect_retail(self, subsource_client):
        """Test detect retail quality."""
        item = {
            "name": "Movie.2024.BluRay.1080p.Vi.srt",
            "release_info": "BluRay"
        }

        quality = subsource_client._detect_quality_type(item)
        assert quality == "retail"

    def test_detect_ai(self, subsource_client):
        """Test detect AI quality."""
        item = {
            "name": "Movie.AI.Generated.srt",
            "release_info": "Auto-generated"
        }

        quality = subsource_client._detect_quality_type(item)
        assert quality == "ai"

    def test_detect_translated_default(self, subsource_client):
        """Test default to translated."""
        item = {
            "name": "Movie.2024.srt",
            "release_info": ""
        }

        quality = subsource_client._detect_quality_type(item)
        assert quality == "translated"
