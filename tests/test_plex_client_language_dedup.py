import pytest
from unittest.mock import Mock, patch
from app.clients.plex_client import PlexClient
from app.models.runtime_config import RuntimeConfig

@pytest.fixture
def mock_config():
    config = Mock(spec=RuntimeConfig)
    config.plex_url = "http://localhost:32400"
    config.plex_token = "token"
    return config

def test_normalize_language():
    assert PlexClient.normalize_language("vi") == "vi"
    assert PlexClient.normalize_language("vie") == "vi"
    assert PlexClient.normalize_language("VIE ") == "vi"
    assert PlexClient.normalize_language("en") == "en"
    assert PlexClient.normalize_language("eng") == "en"
    assert PlexClient.normalize_language(None) is None
    assert PlexClient.normalize_language("") is None
    assert PlexClient.normalize_language("xyz") == "xyz"

def test_stream_matches_language():
    # Stream with tag "vi", code "vie"
    stream1 = Mock()
    stream1.languageTag = "vi"
    stream1.languageCode = "vie"
    assert PlexClient._stream_matches_language(stream1, "vi") is True
    assert PlexClient._stream_matches_language(stream1, "vie") is True

    # Stream with tag None, code "vie"
    stream2 = Mock()
    stream2.languageTag = None
    stream2.languageCode = "vie"
    assert PlexClient._stream_matches_language(stream2, "vi") is True
    assert PlexClient._stream_matches_language(stream2, "vie") is True

    # Stream with tag "vi", code None
    stream3 = Mock()
    stream3.languageTag = "vi"
    stream3.languageCode = None
    assert PlexClient._stream_matches_language(stream3, "vi") is True
    assert PlexClient._stream_matches_language(stream3, "vie") is True

    # Stream with different language
    stream4 = Mock()
    stream4.languageTag = "en"
    stream4.languageCode = "eng"
    assert PlexClient._stream_matches_language(stream4, "vi") is False

def test_get_existing_subtitle_languages(mock_config):
    with patch("app.clients.plex_client.PlexServer"):
        client = PlexClient(config=mock_config, mock_mode=True)
        
        # Mock video structure
        stream1 = Mock()
        stream1.streamType = 3
        stream1.languageTag = None
        stream1.languageCode = "vie"
        
        stream2 = Mock()
        stream2.streamType = 3
        stream2.languageTag = "en"
        stream2.languageCode = "eng"
        
        part = Mock()
        part.streams = [stream1, stream2]
        
        media = Mock()
        media.parts = [part]
        
        video = Mock()
        video.media = [media]
        
        langs = client._get_existing_subtitle_languages(video)
        assert set(langs) == {"vi", "en"}

def test_remove_external_subtitles(mock_config):
    with patch("app.clients.plex_client.PlexServer"):
        client = PlexClient(config=mock_config, mock_mode=True)
        
        stream_vi = Mock()
        stream_vi.streamType = 3
        stream_vi.languageTag = None
        stream_vi.languageCode = "vie"
        stream_vi.format = "srt"
        stream_vi.key = "/subtitles/123"
        stream_vi.id = 123
        stream_vi.title = "Vietnamese External"
        
        video = Mock()
        video.subtitleStreams.return_value = [stream_vi]
        video.removeSubtitles = Mock()
        client._refresh_metadata = Mock()
        
        removed = client.remove_external_subtitles(video, "vi")
        assert removed == 1
        video.removeSubtitles.assert_called_once_with(subtitleStream=stream_vi)
        client._refresh_metadata.assert_called_once_with(video)
