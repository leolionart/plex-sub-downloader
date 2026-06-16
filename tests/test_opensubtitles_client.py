from app.clients.opensubtitles_client import OpenSubtitlesClient
from app.models.runtime_config import RuntimeConfig


def test_opensubtitles_client_follows_redirects() -> None:
    client = OpenSubtitlesClient(RuntimeConfig(opensubtitles_api_key="test-key"))

    assert client._client.follow_redirects is True
