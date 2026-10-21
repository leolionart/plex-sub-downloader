"""
Unit tests for PlexClient.

Mocks PlexAPI để test logic mà không cần Plex server thật.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

from plexapi.video import Movie, Episode, Show
from plexapi.exceptions import NotFound, Unauthorized

from app.clients.plex_client import PlexClient, PlexClientError
from app.models.webhook import MediaMetadata
from app.models.runtime_config import RuntimeConfig


@pytest.fixture
def mock_plex_server():
    """Mock PlexServer instance."""
    with patch("app.clients.plex_client.PlexServer") as mock:
        server = Mock()
        server.friendlyName = "Test Plex Server"
        mock.return_value = server
        yield server


@pytest.fixture
def test_config():
    return RuntimeConfig(
        plex_url="http://localhost:32400",
        plex_token="test-token",
        default_language="vi",
    )


@pytest.fixture
def plex_client(mock_plex_server, test_config):
    """PlexClient instance với mocked server."""
    return PlexClient(test_config)

class TestPlexClientConnection:
    """Test Plex server connection."""

    def test_connect_success(self, mock_plex_server, test_config):
        """Test successful connection."""
        client = PlexClient(test_config)
        assert client.server.friendlyName == "Test Plex Server"

    @patch("app.clients.plex_client.PlexServer")
    def test_connect_invalid_token(self, mock_server):
        """Test connection với invalid token."""
        mock_server.side_effect = Unauthorized("Invalid token")

        with pytest.raises(PlexClientError, match="Invalid Plex token"):
            PlexClient(RuntimeConfig(plex_url="http://localhost:32400", plex_token="test-token"))


    @patch("app.clients.plex_client.PlexServer")
    def test_connect_deferred_when_plex_booting(self, mock_server):
        """When Plex is booting/unreachable at startup, PlexClient defers connection instead of crashing init."""
        ready_server = Mock()
        ready_server.friendlyName = "Recovered Plex Server"
        mock_server.side_effect = [
            ConnectionError("Connection refused"),
            ConnectionError("Connection refused"),
            ConnectionError("Connection refused"),
            ready_server,
        ]

        client = PlexClient(
            RuntimeConfig(plex_url="http://localhost:32400", plex_token="test-token", max_retries=3, retry_delay=0)
        )
        assert client._server is None
        assert client.server.friendlyName == "Recovered Plex Server"

    @pytest.mark.asyncio
    async def test_health_check_returns_503_when_uninitialized_and_auto_heals(self, monkeypatch):
        """Health check returns 503 if ensure_service_initialized fails, and 200 when service is active."""
        import app.main as main_app

        monkeypatch.setattr(main_app, "subtitle_service", None)
        monkeypatch.setattr(main_app, "runtime_config", None)
        monkeypatch.setattr(main_app, "ensure_service_initialized", lambda: None)

        resp = await main_app.health_check()
        assert getattr(resp, "status_code", 200) == 503

        monkeypatch.setattr(main_app, "subtitle_service", Mock())
        monkeypatch.setattr(main_app, "runtime_config", RuntimeConfig())
        ok_resp = await main_app.health_check()
        assert ok_resp == {"status": "healthy"}

class TestGetVideo:
    """Test get_video method."""

    def test_get_movie_success(self, plex_client, mock_plex_server):
        """Test fetch movie by ratingKey."""
        # Mock movie object
        mock_movie = Mock(spec=Movie)
        mock_movie.title = "Test Movie"
        mock_movie.type = "movie"
        mock_movie.ratingKey = 12345

        mock_plex_server.fetchItem.return_value = mock_movie

        video = plex_client.get_video("12345")

        assert video.title == "Test Movie"
        mock_plex_server.fetchItem.assert_called_once_with(12345)

    def test_get_episode_success(self, plex_client, mock_plex_server):
        """Test fetch episode by ratingKey."""
        mock_episode = Mock(spec=Episode)
        mock_episode.title = "Pilot"
        mock_episode.type = "episode"
        mock_episode.ratingKey = 67890

        mock_plex_server.fetchItem.return_value = mock_episode

        video = plex_client.get_video("67890")

        assert video.title == "Pilot"

    def test_get_video_not_found(self, plex_client, mock_plex_server):
        """Test fetch non-existent video."""
        mock_plex_server.fetchItem.side_effect = NotFound("Not found")

        with pytest.raises(PlexClientError, match="not found"):
            plex_client.get_video("99999")

    def test_get_video_invalid_type(self, plex_client, mock_plex_server):
        """Test fetch non-video item (e.g., album)."""
        mock_album = Mock()  # Not Movie or Episode
        mock_album.type = "album"
        mock_plex_server.fetchItem.return_value = mock_album

        with pytest.raises(PlexClientError, match="not a supported video type"):
            plex_client.get_video("12345")


class TestGetItem:
    """Test get_item method supporting Show, Season, Movie, Episode."""

    def test_get_show_item_success(self, plex_client, mock_plex_server):
        mock_show = Mock(spec=Show)
        mock_show.title = "Test Show"
        mock_show.type = "show"
        mock_show.thumb = "/thumb/show.jpg"
        mock_plex_server.fetchItem.return_value = mock_show

        item = plex_client.get_item("8888")
        assert item.title == "Test Show"
        assert item.thumb == "/thumb/show.jpg"
        mock_plex_server.fetchItem.assert_called_once_with(8888)

class TestExtractMetadata:
    """Test extract_metadata method."""

    def test_extract_movie_metadata(self, plex_client):
        """Test extract metadata từ Movie object."""
        # Mock movie
        mock_movie = Mock(spec=Movie)
        mock_movie.ratingKey = 12345
        mock_movie.title = "The Matrix"
        mock_movie.year = 1999
        mock_movie.media = []

        # Mock GUIDs
        mock_guid_imdb = Mock()
        mock_guid_imdb.id = "imdb://tt0133093"
        mock_guid_tmdb = Mock()
        mock_guid_tmdb.id = "tmdb://603"
        mock_movie.guids = [mock_guid_imdb, mock_guid_tmdb]

        metadata = plex_client.extract_metadata(mock_movie)

        assert metadata.media_type == "movie"
        assert metadata.title == "The Matrix"
        assert metadata.year == 1999
        assert metadata.imdb_id == "tt0133093"
        assert metadata.tmdb_id == "603"

    def test_extract_episode_metadata(self, plex_client):
        """Test extract metadata từ Episode object."""
        # Mock show
        mock_show = Mock()
        mock_show.title = "Breaking Bad"
        mock_guid = Mock()
        mock_guid.id = "tvdb://81189"
        mock_show.guids = [mock_guid]

        # Mock episode
        mock_episode = Mock(spec=Episode)
        mock_episode.ratingKey = 67890
        mock_episode.title = "Pilot"
        mock_episode.year = 2008
        mock_episode.seasonNumber = 1
        mock_episode.episodeNumber = 1
        mock_episode.media = []
        mock_episode.show.return_value = mock_show

        metadata = plex_client.extract_metadata(mock_episode)

        assert metadata.media_type == "episode"
        assert metadata.title == "Pilot"
        assert metadata.show_title == "Breaking Bad"
        assert metadata.season_number == 1
        assert metadata.episode_number == 1


class TestHasSubtitle:
    """Test has_subtitle method."""

    def test_has_subtitle_found(self, plex_client):
        """Test video có subtitle."""
        mock_stream = Mock()
        mock_stream.streamType = 3  # Subtitle
        mock_stream.languageCode = "vie"
        mock_stream.languageTag = "vi"
        mock_part = Mock()
        mock_part.streams = [mock_stream]

        mock_media = Mock()
        mock_media.parts = [mock_part]

        mock_video = Mock()
        mock_video.media = [mock_media]
        mock_video.title = "Test Video"

        assert plex_client.has_subtitle(mock_video, "vi") is True

    def test_has_subtitle_not_found(self, plex_client):
        """Test video không có subtitle."""
        mock_video = Mock()
        mock_video.media = []
        mock_video.title = "Test Video"

        assert plex_client.has_subtitle(mock_video, "vi") is False


class TestUploadSubtitle:
    """Test upload_subtitle method."""

    def test_upload_success(self, plex_client, tmp_path):
        """Test upload subtitle thành công."""
        # Create temp .srt file
        srt_file = tmp_path / "test.srt"
        srt_file.write_text("1\n00:00:00,000 --> 00:00:05,000\nTest subtitle\n")

        mock_video = Mock()
        mock_video.title = "Test Video"
        mock_video.uploadSubtitles = Mock()
        mock_video.refresh = Mock()

        success = plex_client.upload_subtitle(mock_video, srt_file, "vi")

        assert success is True
        mock_video.uploadSubtitles.assert_called_once()
        mock_video.refresh.assert_called_once()

    def test_upload_file_not_found(self, plex_client):
        """Test upload với file không tồn tại."""
        mock_video = Mock()
        fake_path = Path("/nonexistent/subtitle.srt")

        with pytest.raises(PlexClientError, match="not found"):
            plex_client.upload_subtitle(mock_video, fake_path, "vi")

    def test_upload_invalid_format(self, plex_client, tmp_path):
        """Test upload với file không phải .srt."""
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("Not a subtitle")

        mock_video = Mock()

        with pytest.raises(PlexClientError, match="Only .srt files supported"):
            plex_client.upload_subtitle(mock_video, txt_file, "vi")


class TestSectionItemsAndShowTree:
    """Test get_section_items and get_show_tree subtitle logic."""

    def test_get_section_items_missing_vi_filter(self, plex_client, mock_plex_server):
        mock_section = Mock()
        mock_section.title = "Movies"
        mock_section.type = "movie"
        mock_plex_server.library.sectionByID.return_value = mock_section

        # Mock items
        item1 = Mock()
        item1.ratingKey = 101
        item1.title = "Movie Without Sub"
        item1.year = 2023
        item1.type = "movie"
        item1.thumb = None
        item1.summary = "Desc"
        item1.genres = []

        item2 = Mock()
        item2.ratingKey = 102
        item2.title = "Movie With Vi"
        item2.year = 2024
        item2.type = "movie"
        item2.thumb = None
        item2.summary = "Desc 2"
        item2.genres = []

        def mock_search(**kwargs):
            filters = kwargs.get("filters", {})
            if filters == {"subtitleLanguage": "vi"}:
                return [item2]
            if filters == {"subtitleLanguage": "en"}:
                return [item1, item2]
            return [item1, item2]
        mock_section.search.side_effect = mock_search

        result = plex_client.get_section_items("1", subtitle_filter="missing_vi")

        first_call = mock_section.search.call_args_list[0]
        assert first_call.kwargs["filters"] == {"subtitleLanguage!": "vi"}

        # Verify item status
        items = result["items"]
        assert len(items) == 2
        assert items[0]["rating_key"] == "101"
        assert items[0]["has_vi"] is False
        assert items[0]["can_translate"] is True

        assert items[1]["rating_key"] == "102"
        assert items[1]["has_vi"] is True
        assert items[1]["can_translate"] is False

    def test_get_section_items_can_translate_filter(self, plex_client, mock_plex_server):
        mock_section = Mock()
        mock_section.title = "Movies"
        mock_section.type = "movie"
        mock_plex_server.library.sectionByID.return_value = mock_section
        mock_section.search.return_value = []

        plex_client.get_section_items("1", subtitle_filter="can_translate")
        first_call = mock_section.search.call_args_list[0]
        assert first_call.kwargs["filters"] == {
            "subtitleLanguage!": "vi",
            "subtitleLanguage": "en",
        }

    def test_get_section_items_caches_subtitle_keys(self, plex_client, mock_plex_server):
        mock_section = Mock()
        mock_section.title = "Movies"
        mock_section.type = "movie"
        mock_plex_server.library.sectionByID.return_value = mock_section
        mock_section.search.return_value = []

        # First call populates cache
        plex_client.get_section_items("1", offset=0, limit=50)
        assert mock_section.search.call_count == 3

        # Second call (pagination) reuses cached vi_keys / en_keys
        plex_client.get_section_items("1", offset=50, limit=50)
        assert mock_section.search.call_count == 4

    def test_get_show_tree_vi_and_vie(self, plex_client, mock_plex_server):
        mock_show = Mock()
        mock_show.type = "show"
        mock_show.title = "Test Series"
        mock_show.year = 2024
        mock_show.thumb = None
        mock_show.summary = "A test show"
        mock_plex_server.fetchItem.return_value = mock_show

        mock_season = Mock()
        mock_season.ratingKey = 201
        mock_season.index = 1
        mock_season.title = "Season 1"
        mock_show.seasons.return_value = [mock_season]

        # Ep 1: subtitle with languageCode="vie"
        stream1 = Mock()
        stream1.streamType = 3
        stream1.languageCode = "vie"
        stream1.languageTag = None
        stream1.codec = "subrip"
        stream1.key = None
        stream1.forced = False
        stream1.title = "Vietnamese"
        stream1.format = "srt"
        part1 = Mock()
        part1.streams = [stream1]
        media1 = Mock()
        media1.parts = [part1]
        ep1 = Mock()
        ep1.ratingKey = 301
        ep1.index = 1
        ep1.title = "Episode 1"
        ep1.thumb = None
        ep1.media = [media1]

        # Ep 2: subtitle with languageTag="vi"
        stream2 = Mock()
        stream2.streamType = 3
        stream2.languageCode = "vie"
        stream2.languageTag = "vi"
        stream2.codec = "subrip"
        stream2.key = None
        stream2.forced = False
        stream2.title = "Vietnamese"
        stream2.format = "srt"
        part2 = Mock()
        part2.streams = [stream2]
        media2 = Mock()
        media2.parts = [part2]
        ep2 = Mock()
        ep2.ratingKey = 302
        ep2.index = 2
        ep2.title = "Episode 2"
        ep2.thumb = None
        ep2.media = [media2]

        mock_season.episodes.return_value = [ep1, ep2]

        tree = plex_client.get_show_tree("100")
        assert tree["total_episodes"] == 2
        assert tree["vi_count"] == 2
        assert tree["missing_vi_count"] == 0
        eps = tree["seasons"][0]["episodes"]
        assert eps[0]["has_vi"] is True
        assert eps[1]["has_vi"] is True


class TestPlexClientCachingAndInvalidation:
    """Test TTL caching and invalidation logic on PlexClient."""

    def test_get_section_items_ttl_caching(self, plex_client, mock_plex_server):
        mock_section = Mock()
        mock_section.title = "Movies"
        mock_section.type = "movie"
        mock_plex_server.library.sectionByID.return_value = mock_section
        mock_section.search.return_value = []

        # First call populates cache (3 searches: items, vi_keys, en_keys)
        res1 = plex_client.get_section_items("1", offset=0, limit=50)
        assert mock_section.search.call_count == 3

        # Second call with use_cache=True returns cached result without searches
        res2 = plex_client.get_section_items("1", offset=0, limit=50, use_cache=True)
        assert res2 == res1
        assert mock_section.search.call_count == 3

        # Call with use_cache=False bypasses cache and re-queries
        res3 = plex_client.get_section_items("1", offset=0, limit=50, use_cache=False)
        assert mock_section.search.call_count > 3

    def test_get_show_tree_caching(self, plex_client, mock_plex_server):
        mock_show = Mock()
        mock_show.type = "show"
        mock_show.title = "Test Series"
        mock_show.year = 2024
        mock_show.thumb = None
        mock_show.summary = "A test show"
        mock_show.seasons.return_value = []
        mock_plex_server.fetchItem.return_value = mock_show

        # First call fetches from server
        tree1 = plex_client.get_show_tree("100")
        assert mock_plex_server.fetchItem.call_count == 1

        # Second call hits cache
        tree2 = plex_client.get_show_tree("100", use_cache=True)
        assert tree2 == tree1
        assert mock_plex_server.fetchItem.call_count == 1

        # Call with use_cache=False fetches again
        tree3 = plex_client.get_show_tree("100", use_cache=False)
        assert mock_plex_server.fetchItem.call_count == 2

    def test_get_movie_details_caching(self, plex_client, mock_plex_server):
        mock_movie = Mock(spec=Movie)
        mock_movie.type = "movie"
        mock_movie.title = "Inception"
        mock_movie.year = 2010
        mock_movie.ratingKey = 500
        mock_movie.thumb = None
        mock_movie.summary = "Desc"
        mock_movie.media = []
        mock_plex_server.fetchItem.return_value = mock_movie
        # First call
        det1 = plex_client.get_movie_details("500")
        assert mock_plex_server.fetchItem.call_count == 1

        # Second call hits cache
        det2 = plex_client.get_movie_details("500", use_cache=True)
        assert det2 == det1
        assert mock_plex_server.fetchItem.call_count == 1

        # Bypass cache
        det3 = plex_client.get_movie_details("500", use_cache=False)
        assert mock_plex_server.fetchItem.call_count == 2

    def test_invalidate_item_and_section_cache(self, plex_client, mock_plex_server):
        mock_show = Mock()
        mock_show.type = "show"
        mock_show.title = "Show"
        mock_show.year = 2024
        mock_show.thumb = None
        mock_show.summary = ""

        mock_season = Mock()
        mock_season.ratingKey = 201
        mock_season.index = 1
        mock_season.title = "Season 1"

        mock_ep = Mock()
        mock_ep.ratingKey = 301
        mock_ep.index = 1
        mock_ep.title = "Ep 1"
        mock_ep.thumb = None
        mock_ep.media = []
        mock_season.episodes.return_value = [mock_ep]
        mock_show.seasons.return_value = [mock_season]
        mock_plex_server.fetchItem.return_value = mock_show

        # Populate show tree cache
        plex_client.get_show_tree("100")
        assert ("100", "vi", "en") in plex_client._show_tree_cache

        # Invalidating episode 301 should invalidate show tree 100
        plex_client.invalidate_item_cache("301")
        assert ("100", "vi", "en") not in plex_client._show_tree_cache

        # Invalidate section cache
        plex_client._section_items_cache[("1", None, "missing_vi", 0, 50)] = {"data": {}, "timestamp": 123}
        plex_client.invalidate_section_cache("1")
        assert ("1", None, "missing_vi", 0, 50) not in plex_client._section_items_cache

    def test_upload_and_remove_subtitles_invalidates_cache(self, plex_client, tmp_path):
        srt_file = tmp_path / "test.vi.srt"
        srt_file.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")

        mock_video = Mock()
        mock_video.title = "Test Video"
        mock_video.ratingKey = 777
        mock_video.grandparentRatingKey = 888
        mock_video.librarySectionID = 1
        mock_video.uploadSubtitles = Mock()
        mock_video.refresh = Mock()

        plex_client._movie_details_cache[("777", "vi", "en")] = {"data": {}, "timestamp": 123}
        plex_client._show_tree_cache[("888", "vi", "en")] = {"data": {}, "timestamp": 123}
        plex_client._section_items_cache[("1", None, "missing_vi", 0, 50)] = {"data": {}, "timestamp": 123}

        plex_client.upload_subtitle(mock_video, srt_file, "vi")

        # Caches should be cleared
        assert ("777", "vi", "en") not in plex_client._movie_details_cache
        assert ("888", "vi", "en") not in plex_client._show_tree_cache
        assert ("1", None, "missing_vi", 0, 50) not in plex_client._section_items_cache

        # Test remove_external_subtitles invalidation
        plex_client._movie_details_cache[("777", "vi", "en")] = {"data": {}, "timestamp": 123}
        mock_stream = Mock()
        mock_stream.languageCode = "vi"
        mock_stream.languageTag = "vi"
        mock_stream.format = "srt"
        mock_stream.key = "/sub/1"
        mock_video.subtitleStreams.return_value = [mock_stream]
        mock_video.removeSubtitles = Mock()

        plex_client.remove_external_subtitles(mock_video, "vi")
        assert ("777", "vi", "en") not in plex_client._movie_details_cache

    def test_get_sections_caching(self, plex_client, mock_plex_server):
        mock_s1 = Mock()
        mock_s1.key = "1"
        mock_s1.title = "Movies"
        mock_s1.type = "movie"
        mock_plex_server.library.sections.return_value = [mock_s1]

        # First call fetches from server
        sec1 = plex_client.get_sections(use_cache=True)
        assert len(sec1) == 1
        assert sec1[0]["title"] == "Movies"
        assert mock_plex_server.library.sections.call_count == 1

        # Second call hits cache
        sec2 = plex_client.get_sections(use_cache=True)
        assert sec2 == sec1
        assert mock_plex_server.library.sections.call_count == 1

        # Bypass cache
        sec3 = plex_client.get_sections(use_cache=False)
        assert sec3 == sec1
        assert mock_plex_server.library.sections.call_count == 2

    def test_clear_all_caches_and_stats(self, plex_client):
        plex_client._sections_cache.set("all", [{"key": "1"}])
        plex_client._movie_details_cache.set(("100", "vi", "en"), {"title": "Test"})
        plex_client._show_tree_cache.set(("200", "vi", "en"), {"title": "Show"})

        stats_before = plex_client.get_cache_stats()
        assert stats_before["sections"]["size"] >= 1
        assert stats_before["movie_details"]["size"] >= 1
        assert stats_before["show_tree"]["size"] >= 1

        plex_client.clear_all_caches()
        stats_after = plex_client.get_cache_stats()
        assert stats_after["sections"]["size"] == 0
        assert stats_after["movie_details"]["size"] == 0
        assert stats_after["show_tree"]["size"] == 0


class TestGetSessions:
    """Test normalized Now Playing session metadata."""

    def test_episode_includes_parent_show_and_section_metadata(self, plex_client, mock_plex_server):
        episode = Mock(spec=Episode)
        episode.ratingKey = 67890
        episode.title = "Pilot"
        episode.type = "episode"
        episode.year = 2008
        episode.seasonNumber = 1
        episode.episodeNumber = 1
        episode.media = []
        episode.grandparentRatingKey = 12345
        episode.grandparentTitle = "Breaking Bad"
        episode.librarySectionID = "2"
        episode.thumb = "/library/metadata/67890/thumb"
        episode.player = Mock(title="Living Room", state="playing")
        episode.duration = 1000
        episode.viewOffset = 250
        mock_plex_server.sessions.return_value = [episode]

        sessions = plex_client.get_sessions()

        assert sessions[0]["media_type"] == "episode"
        assert sessions[0]["show_rating_key"] == "12345"
        assert sessions[0]["show_title"] == "Breaking Bad"
        assert sessions[0]["section_key"] == "2"

    def test_movie_does_not_get_parent_show_fields(self, plex_client, mock_plex_server):
        movie = Mock(spec=Movie)
        movie.ratingKey = 111
        movie.title = "Movie"
        movie.type = "movie"
        movie.year = 2024
        movie.media = []
        movie.thumb = None
        movie.player = None
        movie.duration = 0
        movie.viewOffset = 0
        mock_plex_server.sessions.return_value = [movie]

        sessions = plex_client.get_sessions()

        assert sessions[0]["media_type"] == "movie"
        assert "show_rating_key" not in sessions[0]
        assert "show_title" not in sessions[0]
        assert "section_key" not in sessions[0]
