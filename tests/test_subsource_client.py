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
from app.models.runtime_config import RuntimeConfig
from app.models.subtitle import SubtitleSearchParams, SubtitleResult


@pytest.fixture
def subsource_client():
    """SubsourceClient instance."""
    return SubsourceClient(RuntimeConfig(subsource_api_key="test-key"))


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

    def test_extract_season_pack(self, subsource_client):
        """Test parse season pack như S01.COMPLETE."""
        season, episode = subsource_client._extract_season_episode(
            "Daredevil.Born.Again.S01.COMPLETE.DSNP.WEB-DL"
        )

        assert season == 1
        assert episode is None

    def test_extract_textual_season(self, subsource_client):
        """Test parse textual season như 'first season'."""
        season, episode = subsource_client._extract_season_episode(
            "invincible-first-season_vietnamese-2470110"
        )

        assert season == 1
        assert episode is None

    def test_extract_x_episode_pattern(self, subsource_client):
        """Test parse pattern như 4x05."""
        season, episode = subsource_client._extract_season_episode(
            "Invincible.4x05.1080p.WEB-DL"
        )

        assert season == 4
        assert episode == 5

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

    def test_wrong_season_pack_not_used_as_untagged_fallback(self, subsource_client):
        """Test season pack sai mùa không bị coi là untagged fallback."""
        results = [
            SubtitleResult(
                id="1",
                name="Daredevil.Born.Again.S01.COMPLETE.DSNP.WEB-DL",
                language="en",
                download_url="http://test.com/1",
                quality_type="retail",
                season=1,
                episode=None,
            ),
        ]

        params = SubtitleSearchParams(
            language="en",
            title="Daredevil: Born Again",
            season=2,
            episode=1,
            video_filename="Daredevil.Born.Again.S02E01.mkv",
        )

        filtered = subsource_client._rank_and_filter(results, params)

        assert filtered == []

    def test_unknown_episode_match_requires_filename_similarity(self, subsource_client):
        """Test untagged subtitle không được dùng nếu không đủ tín hiệu match."""
        results = [
            SubtitleResult(
                id="1",
                name="invincible-first-season_vietnamese-2470110",
                language="vi",
                download_url="http://test.com/1",
                quality_type="translated",
                season=None,
                episode=None,
            ),
        ]

        params = SubtitleSearchParams(
            language="vi",
            title="Invincible",
            season=4,
            episode=5,
            video_filename="Invincible.2021.S04E05.1080p.WEB-DL.mkv",
        )

        filtered = subsource_client._rank_and_filter(results, params)

        assert filtered == []

    def test_unknown_episode_match_without_filename_is_rejected(self, subsource_client):
        """Test episode search không còn đoán mò với untagged subtitle."""
        results = [
            SubtitleResult(
                id="1",
                name="subtitle-unknown",
                language="vi",
                download_url="http://test.com/1",
                quality_type="translated",
                season=None,
                episode=None,
            ),
        ]

        params = SubtitleSearchParams(
            language="vi",
            title="Invincible",
            season=4,
            episode=5,
        )

        filtered = subsource_client._rank_and_filter(results, params)

        assert filtered == []

    def test_exact_episode_matches_reranked_by_filename_similarity(self, subsource_client):
        """Test exact episode matches được ưu tiên theo filename similarity."""
        results = [
            SubtitleResult(
                id="1",
                name="Invincible.S04E05.AMZN.WEBRip",
                language="vi",
                download_url="http://test.com/1",
                quality_type="translated",
                season=4,
                episode=5,
            ),
            SubtitleResult(
                id="2",
                name="Invincible.S04E05.NF.WEBRip",
                language="vi",
                download_url="http://test.com/2",
                quality_type="translated",
                season=4,
                episode=5,
            ),
        ]

        params = SubtitleSearchParams(
            language="vi",
            title="Invincible",
            season=4,
            episode=5,
            video_filename="Invincible.S04E05.AMZN.1080p.WEB-DL.mkv",
        )

        filtered = subsource_client._rank_and_filter(results, params)

        assert filtered[0].id == "1"

    def test_filename_similarity_prefers_matching_release_tokens(self, subsource_client):
        """Test scorer ưu tiên release tokens đúng platform/source/codec."""
        amzn_score = subsource_client._filename_similarity(
            "Invincible.S04E05.AMZN.1080p.WEB-DL.DDP5.1.H.264.mkv",
            "Invincible.S04E05.AMZN.1080p.WEB-DL.DDP5.1.H.264",
        )
        nf_score = subsource_client._filename_similarity(
            "Invincible.S04E05.AMZN.1080p.WEB-DL.DDP5.1.H.264.mkv",
            "Invincible.S04E05.NF.1080p.WEBRip.DDP5.1.x264",
        )

        assert amzn_score > nf_score


class TestMovieMatching:
    """Test movie/show title fallback matching."""

    def test_movie_match_score_prefers_exact_title_year_and_season(self, subsource_client):
        exact = {
            "movieId": 101,
            "title": "Invincible",
            "releaseYear": 2021,
            "season": 4,
            "subtitleCount": 50,
        }
        wrong = {
            "movieId": 102,
            "title": "Invincible Fight Girl",
            "releaseYear": 2024,
            "season": 1,
            "subtitleCount": 80,
        }

        exact_score = subsource_client._movie_match_score(exact, "Invincible", year=2021, season=4)
        wrong_score = subsource_client._movie_match_score(wrong, "Invincible", year=2021, season=4)

        assert exact_score > wrong_score


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

    async def test_download_zip_prefers_expected_episode_file(
        self,
        subsource_client,
        mock_subtitle_result,
        tmp_path,
    ):
        """Test season pack ZIP chọn đúng file episode cần thiết."""
        zip_path = tmp_path / "season-pack.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("Invincible.S04E01.vi.srt", "Episode 1 subtitle")
            zf.writestr("Invincible.S04E05.vi.srt", "Episode 5 subtitle")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/zip"}
        mock_response.content = zip_path.read_bytes()
        mock_response.raise_for_status = Mock()

        subsource_client._client.get = AsyncMock(return_value=mock_response)

        srt_path = await subsource_client.download_subtitle(
            mock_subtitle_result,
            tmp_path,
            expected_season=4,
            expected_episode=5,
            video_filename="Invincible.S04E05.1080p.WEB-DL.mkv",
        )

        assert srt_path.exists()
        assert srt_path.name.endswith("Invincible.S04E05.vi.srt")
        assert "Episode 5 subtitle" in srt_path.read_text()

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
