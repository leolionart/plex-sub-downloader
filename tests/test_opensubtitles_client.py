from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.clients.opensubtitles_client import (
    OPENSUBTITLES_USER_AGENT,
    OpenSubtitlesClient,
    OpenSubtitlesClientError,
)
from app.models.runtime_config import RuntimeConfig
from app.models.subtitle import SubtitleResult, SubtitleSearchParams

def test_opensubtitles_client_follows_redirects() -> None:
    client = OpenSubtitlesClient(RuntimeConfig(opensubtitles_api_key="test-key"))

    assert client._client.follow_redirects is True
    assert client._client.headers["User-Agent"] == OPENSUBTITLES_USER_AGENT


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


@pytest.mark.asyncio
async def test_opensubtitles_search_error_includes_response_body() -> None:
    client = OpenSubtitlesClient(RuntimeConfig(opensubtitles_api_key="test-key"))
    response = httpx.Response(
        403,
        request=httpx.Request("GET", "https://api.opensubtitles.com/api/v1/subtitles"),
        text='{"message":"Invalid User-Agent"}',
    )
    response.raise_for_status = Mock(side_effect=httpx.HTTPStatusError(
        "Forbidden",
        request=response.request,
        response=response,
    ))
    client._client.get = AsyncMock(return_value=response)

    with pytest.raises(OpenSubtitlesClientError, match="Invalid User-Agent"):
        await client.search_subtitles(SubtitleSearchParams(language="vi", title="Test"))

@pytest.mark.asyncio
async def test_opensubtitles_download_reauths_on_401(tmp_path) -> None:
    client = OpenSubtitlesClient(
        RuntimeConfig(
            opensubtitles_api_key="test-key",
            opensubtitles_username="user",
            opensubtitles_password="pass",
        )
    )
    client._token = "stale-token"

    resp_401 = httpx.Response(401, request=httpx.Request("POST", "https://api.opensubtitles.com/api/v1/download"))
    resp_login = httpx.Response(200, json={"token": "fresh-token"}, request=httpx.Request("POST", "https://api.opensubtitles.com/api/v1/login"))
    resp_200 = httpx.Response(200, json={"link": "https://api.opensubtitles.com/download/file.srt"}, request=httpx.Request("POST", "https://api.opensubtitles.com/api/v1/download"))
    resp_file = httpx.Response(200, content=b"1\n00:00:01,000 --> 00:00:02,000\nHello\n", request=httpx.Request("GET", "https://api.opensubtitles.com/download/file.srt"))

    post_mock = AsyncMock(side_effect=[resp_401, resp_login, resp_200])
    get_mock = AsyncMock(return_value=resp_file)

    client._client.post = post_mock
    client._client.get = get_mock

    sub = SubtitleResult(
        id="12345",
        provider="opensubtitles",
        name="test.srt",
        language="en",
        download_url="https://api.opensubtitles.com/download/12345",
    )

    saved_path = await client.download_subtitle(sub, dest_dir=tmp_path)
    assert saved_path.exists()
    assert client._token == "fresh-token"
    assert post_mock.await_count == 3
