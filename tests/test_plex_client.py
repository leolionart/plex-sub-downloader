"""
Unit tests for PlexClient.

Mocks PlexAPI để test logic mà không cần Plex server thật.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

from plexapi.video import Movie, Episode
from plexapi.exceptions import NotFound, Unauthorized

from app.clients.plex_client import PlexClient, PlexClientError
from app.models.webhook import MediaMetadata


@pytest.fixture
def mock_plex_server():
    """Mock PlexServer instance."""
    with patch("app.clients.plex_client.PlexServer") as mock:
        server = Mock()
        server.friendlyName = "Test Plex Server"
        mock.return_value = server
        yield server


@pytest.fixture
def plex_client(mock_plex_server):
    """PlexClient instance với mocked server."""
    return PlexClient()


class TestPlexClientConnection:
    """Test Plex server connection."""

    def test_connect_success(self, mock_plex_server):
        """Test successful connection."""
        client = PlexClient()
        assert client.server.friendlyName == "Test Plex Server"

    @patch("app.clients.plex_client.PlexServer")
    def test_connect_invalid_token(self, mock_server):
        """Test connection với invalid token."""
        mock_server.side_effect = Unauthorized("Invalid token")

        with pytest.raises(PlexClientError, match="Invalid Plex token"):
            PlexClient()


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

        with pytest.raises(PlexClientError, match="not a movie or episode"):
            plex_client.get_video("12345")


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
        mock_stream.languageCode = "vi"

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
