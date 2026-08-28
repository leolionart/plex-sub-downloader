from app.models.subtitle import SubtitleSearchParams
from app.models.webhook import MediaMetadata
from app.services.subtitle_service import SubtitleService
from app.routes.subtitles import ManualSubtitleSearchRequest


def _episode_metadata() -> MediaMetadata:
    return MediaMetadata(
        rating_key="12688",
        media_type="episode",
        title="Shatter",
        year=2024,
        imdb_id="tt9813792",
        tmdb_id="123",
        show_title="FROM",
        season_number=3,
        episode_number=1,
    )


def test_search_params_for_media_uses_plex_metadata_without_override() -> None:
    params = SubtitleService._search_params_for_media(
        _episode_metadata(),
        language="vi",
        video_filename="From.S03E01.mkv",
    )

    assert params == SubtitleSearchParams(
        language="vi",
        title="FROM",
        year=2024,
        imdb_id="tt9813792",
        tmdb_id="123",
        season=3,
        episode=1,
        video_filename="From.S03E01.mkv",
    )


def test_search_params_for_media_override_relaxes_external_ids_and_year() -> None:
    params = SubtitleService._search_params_for_media(
        _episode_metadata(),
        language="vi",
        video_filename="From.S03E01.mkv",
        search_override=SubtitleSearchParams(
            language="vi",
            title="FROM",
            season=3,
            episode=1,
        ),
    )

    assert params == SubtitleSearchParams(
        language="vi",
        title="FROM",
        year=None,
        imdb_id=None,
        tmdb_id=None,
        season=3,
        episode=1,
        video_filename="From.S03E01.mkv",
    )


def test_manual_search_allows_filename_only_and_disables_cache_by_default() -> None:
    request = ManualSubtitleSearchRequest(video_filename="From.S03E01.1080p.WEB-DL.mkv")

    assert request.video_filename.endswith(".mkv")
    assert request.use_cache is False
