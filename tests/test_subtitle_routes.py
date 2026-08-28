"""Focused tests for the public manual subtitle-search request contract."""

import pytest
from pydantic import ValidationError

from app.models.subtitle import SubtitleSearchParams
from app.routes.subtitles import SubtitleSearchRequest, _search_params_from_override


def test_search_request_requires_media_reference() -> None:
    """A manual search must identify media by key or searchable metadata."""
    with pytest.raises(ValidationError):
        SubtitleSearchRequest(language="vi")


def test_search_request_accepts_filename_only_with_title() -> None:
    request = SubtitleSearchRequest(
        title="From",
        season=3,
        episode=1,
        video_filename="From.S03E01.1080p.WEB-DL.mkv",
        language="vi",
    )

    assert request.title == "From"
    assert request.video_filename.endswith(".mkv")


def test_search_override_preserves_explicit_language_and_matching_fields() -> None:
    request = SubtitleSearchRequest(
        title="From",
        year=2024,
        season=3,
        episode=1,
        video_filename="From.S03E01.mkv",
        language="en",
    )

    params = _search_params_from_override(request, language="vi")

    assert params == SubtitleSearchParams(
        language="en",
        title="From",
        year=2024,
        season=3,
        episode=1,
        video_filename="From.S03E01.mkv",
    )
