from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.clients.subdl_client import SUBDL_USER_AGENT, SubDLClient, SubDLClientError
from app.models.runtime_config import RuntimeConfig
from app.models.subtitle import SubtitleSearchParams


def test_subdl_client_uses_service_user_agent() -> None:
    client = SubDLClient(RuntimeConfig(subdl_api_key="test-key"))

    assert client._client.headers["User-Agent"] == SUBDL_USER_AGENT


@pytest.mark.asyncio
async def test_subdl_search_retries_once_on_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SubDLClient(RuntimeConfig(subdl_api_key="test-key"))
    monkeypatch.setattr(client, "_retry_after_seconds", Mock(return_value=0))

    rate_limited = httpx.Response(
        429,
        request=httpx.Request("GET", "https://api.subdl.com/api/v1/subtitles"),
        json={"status": False, "retryAfterSeconds": 1},
    )
    ok = httpx.Response(
        200,
        request=httpx.Request("GET", "https://api.subdl.com/api/v1/subtitles"),
        json={"status": True, "subtitles": []},
    )
    client._client.get = AsyncMock(side_effect=[rate_limited, ok])

    results = await client.search_subtitles(SubtitleSearchParams(language="vi", title="Test"))

    assert results == []
    assert client._client.get.await_count == 2


@pytest.mark.asyncio
async def test_subdl_search_error_redacts_api_key_and_includes_body() -> None:
    client = SubDLClient(RuntimeConfig(subdl_api_key="secret-key"))
    request = httpx.Request(
        "GET",
        "https://api.subdl.com/api/v1/subtitles?api_key=secret-key&languages=VI",
    )
    response = httpx.Response(
        403,
        request=request,
        text='{"message":"Forbidden"}',
    )
    client._client.get = AsyncMock(return_value=response)

    with pytest.raises(SubDLClientError) as exc_info:
        await client.search_subtitles(SubtitleSearchParams(language="vi", title="Test"))

    message = str(exc_info.value)
    assert "secret-key" not in message
    assert "api_key=***" in message
    assert "Forbidden" in message
