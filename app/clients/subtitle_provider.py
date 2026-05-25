"""
Common subtitle provider protocol and generic download helpers.
"""

from pathlib import Path
from typing import Protocol, runtime_checkable
import gzip
import zipfile

import httpx

from app.models.subtitle import SubtitleResult, SubtitleSearchParams


class SubtitleProviderError(Exception):
    """Base exception for subtitle provider errors."""
    pass


@runtime_checkable
class SubtitleProvider(Protocol):
    name: str

    async def search_subtitles(self, params: SubtitleSearchParams) -> list[SubtitleResult]:
        """Search subtitles for one language."""
        ...

    async def download_subtitle(
        self,
        subtitle: SubtitleResult,
        dest_dir: Path,
        expected_season: int | None = None,
        expected_episode: int | None = None,
        video_filename: str | None = None,
    ) -> Path:
        """Download a selected subtitle into dest_dir and return an .srt path."""
        ...

    async def close(self) -> None:
        """Close provider resources."""
        ...


async def search_subtitles_multi_lang(
    provider: SubtitleProvider,
    base_params: SubtitleSearchParams,
    languages: list[str],
) -> dict[str, list[SubtitleResult]]:
    """Provider-neutral multi-language search with native method fallback."""
    native = getattr(provider, "search_subtitles_multi_lang", None)
    if native:
        return await native(base_params, languages)

    import asyncio

    tasks = [
        provider.search_subtitles(base_params.model_copy(update={"language": lang}))
        for lang in languages
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return {
        lang: result if isinstance(result, list) else []
        for lang, result in zip(languages, results)
    }


def safe_filename(value: str) -> str:
    """Return a filesystem-safe filename."""
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in value)[:180]


def rank_and_filter_subtitles(
    results: list[SubtitleResult],
    params: SubtitleSearchParams,
) -> list[SubtitleResult]:
    """Apply the service's conservative episode matching and release ranking."""
    from app.clients.subsource_client import SubsourceClient

    if not results:
        return []

    results = [
        r for r in results
        if not params.language or r.language.lower() == params.language.lower()
    ]
    if not results:
        return []

    if params.season is not None and params.episode is not None:
        exact = [r for r in results if r.season == params.season and r.episode == params.episode]
        season_pack = [
            r for r in results
            if r.season == params.season and r.episode is None
        ]
        unknown = [r for r in results if r.season is None and r.episode is None]
        if exact:
            results = exact
        elif season_pack:
            results = season_pack
        elif params.video_filename:
            results = [
                r for r in unknown
                if SubsourceClient._filename_similarity(params.video_filename, r.name) >= 0.75
            ]
        else:
            results = []

    def sort_key(result: SubtitleResult) -> tuple[float, int]:
        similarity = 0.0
        if params.video_filename:
            similarity = SubsourceClient._filename_similarity(params.video_filename, result.name)
        return similarity, result.priority_score

    return sorted(results, key=sort_key, reverse=True)


def save_subtitle_response(
    response: httpx.Response,
    dest_dir: Path,
    stem: str,
) -> Path:
    """Save a subtitle HTTP response, extracting ZIP files and normalizing to .srt."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    content_type = response.headers.get("content-type", "").lower()
    filename = response.headers.get("content-disposition", "")
    is_zip = (
        "zip" in content_type
        or filename.lower().endswith(".zip")
        or response.content.startswith(b"PK\x03\x04")
    )
    is_gzip = (
        "gzip" in content_type
        or filename.lower().endswith(".gz")
        or response.content.startswith(b"\x1f\x8b")
    )

    if is_zip:
        zip_path = dest_dir / f"{safe_filename(stem)}.zip"
        zip_path.write_bytes(response.content)
        return extract_subtitle_from_zip(zip_path, dest_dir)

    content = gzip.decompress(response.content) if is_gzip else response.content

    suffix = ".srt"
    if "vtt" in content_type:
        suffix = ".vtt"
    path = dest_dir / f"{safe_filename(stem)}{suffix}"
    path.write_bytes(content)
    return convert_to_srt(path)


def extract_subtitle_from_zip(zip_path: Path, dest_dir: Path) -> Path:
    """Extract the first useful subtitle file from a ZIP archive."""
    subtitle_exts = {".srt", ".vtt", ".ass", ".ssa", ".sub"}
    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            candidates = [
                name for name in zip_ref.namelist()
                if Path(name).suffix.lower() in subtitle_exts
            ]
            if not candidates:
                raise SubtitleProviderError(f"No subtitle file found in ZIP: {zip_ref.namelist()}")
            srt_candidates = [name for name in candidates if name.lower().endswith(".srt")]
            chosen = (srt_candidates or candidates)[0]
            zip_ref.extract(chosen, dest_dir)
            return convert_to_srt(dest_dir / chosen)
    except zipfile.BadZipFile as e:
        raise SubtitleProviderError("Invalid ZIP file") from e
    finally:
        zip_path.unlink(missing_ok=True)


def convert_to_srt(path: Path) -> Path:
    """Convert simple text subtitle formats to .srt filename for Plex upload."""
    if path.suffix.lower() == ".srt":
        return path
    srt_path = path.with_suffix(".srt")
    content = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() == ".vtt":
        lines = [
            line.replace(".", ",") if "-->" in line else line
            for line in content.splitlines()
            if line.strip() != "WEBVTT"
        ]
        content = "\n".join(lines).strip() + "\n"
    srt_path.write_text(content, encoding="utf-8")
    path.unlink(missing_ok=True)
    return srt_path
