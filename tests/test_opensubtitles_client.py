from app.clients.opensubtitles_client import OpenSubtitlesClient
from app.models.runtime_config import RuntimeConfig
from app.models.subtitle import SubtitleSearchParams


def test_opensubtitles_client_follows_redirects() -> None:
    client = OpenSubtitlesClient(RuntimeConfig(opensubtitles_api_key="test-key"))

    assert client._client.follow_redirects is True


def test_opensubtitles_search_query_uses_canonical_order() -> None:
    client = OpenSubtitlesClient(RuntimeConfig(opensubtitles_api_key="test-key"))

    query = client._search_query(
        SubtitleSearchParams(
            language="vi",
            imdb_id="tt9813792",
            year=2023,
            season=2,
            episode=2,
        )
    )

    assert query == [
        ("episode_number", 2),
        ("imdb_id", "9813792"),
        ("languages", "vi"),
        ("order_by", "download_count"),
        ("order_direction", "desc"),
        ("season_number", 2),
        ("year", 2023),
    ]
