from app.clients.subtitle_match_validator_client import SubtitleMatchValidatorClient
from app.models.runtime_config import RuntimeConfig
from app.models.subtitle import SubtitleResult, SubtitleSearchParams


def test_subtitle_match_validator_payload_contains_target_and_candidates() -> None:
    params = SubtitleSearchParams(
        language="vi",
        title="FROM",
        year=2026,
        imdb_id="tt9813792",
        tmdb_id="124364",
        season=4,
        episode=1,
        video_filename="FROM - S04E01 - The Arrival WEBDL-1080p.mkv",
    )
    candidate = SubtitleResult(
        id="sub-1",
        provider="subdl",
        name="FROM.S04E01.WEB-DL",
        language="vi",
        download_url="https://example.com/sub.srt",
        season=None,
        episode=None,
    )

    payload = SubtitleMatchValidatorClient._validation_payload(params, [candidate])

    assert payload["target"]["season"] == 4
    assert payload["target"]["episode"] == 1
    assert payload["candidates"][0]["subtitle_id"] == "sub-1"
    assert payload["candidates"][0]["name"] == "FROM.S04E01.WEB-DL"


def test_subtitle_match_validator_parses_json_decisions() -> None:
    decisions = SubtitleMatchValidatorClient._parse_decisions(
        """
        {
          "decisions": [
            {
              "subtitle_id": "sub-1",
              "is_match": true,
              "confidence": 0.91,
              "reason": "Filename contains S04E01"
            }
          ]
        }
        """
    )

    assert len(decisions) == 1
    assert decisions[0].subtitle_id == "sub-1"
    assert decisions[0].is_match is True
    assert decisions[0].confidence == 0.91


def test_subtitle_match_validator_disabled_without_api_key() -> None:
    client = SubtitleMatchValidatorClient(RuntimeConfig(openai_api_key=None))

    assert client.enabled is False
