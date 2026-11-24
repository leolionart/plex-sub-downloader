# Development Guide

## Local Setup

```bash
git clone https://github.com/leolionart/plex-sub-downloader
cd plex-sub-downloader

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

Tạo `data/config.json` (hoặc dùng env vars, xem [Configuration](configuration.md)):
```json
{
  "plex_url": "http://192.168.1.x:32400",
  "plex_token": "your_token",
  "subsource_api_key": "your_key"
}
```

Chạy server:
```bash
python -m app.main
# hoặc
uvicorn app.main:app --reload --port 8000
```

## Project Structure

```
app/
├── main.py                  # FastAPI app entry point
├── config.py                # App-level config (host, port, log level)
├── models/
│   ├── runtime_config.py    # RuntimeConfig — credentials
│   ├── settings.py          # SubtitleSettings — user settings
│   ├── subtitle.py          # SubtitleResult, SubtitleSearchParams
│   └── webhook.py           # PlexWebhookPayload, TautulliWebhookPayload
├── clients/
│   ├── plex_client.py       # Plex API
│   ├── subsource_client.py  # Subsource API + LANGUAGE_MAP
│   ├── openai_translation_client.py  # AI translation
│   ├── sync_client.py       # Subtitle timing sync
│   ├── telegram_client.py   # Telegram notifications
│   └── cache_client.py      # Redis/memory cache
├── services/
│   ├── subtitle_service.py  # Core orchestration
│   ├── config_store.py      # Config persistence
│   └── stats_store.py       # Stats persistence
├── routes/
│   ├── sync.py              # /api/sync/*
│   ├── translation.py       # /api/translation/*
│   ├── setup.py             # /api/setup/*
│   └── logs.py              # /api/logs
├── utils/
│   └── logger.py            # Logging setup, RequestContextLogger
├── static/
│   ├── src/tailwind.css     # Input cho Tailwind CLI
│   └── css/                 # CSS build sẵn, commit vào repo (Docker không cần Node)
│       ├── tailwind.css     # npm run build:css
│       └── daisyui.min.css  # DaisyUI 4.12.23 vendored (npm run vendor:daisyui)
└── templates/
    ├── base.html            # Layout chung: sidebar (desktop), tab bar (mobile), design tokens
    ├── sync.html            # Xử lý phụ đề (/workspace; /, /sync chuyển hướng về đây)
    ├── library.html         # Thư viện Plex (/library)
    ├── settings.html        # Cài đặt + credentials (/settings; /setup chuyển hướng về đây)
    └── logs.html            # Nhật ký real-time (/logs)
```

## Build CSS (Tailwind)

UI không dùng Tailwind Play CDN nữa. Sau khi thêm/sửa class Tailwind trong `app/templates/*.html`:

```bash
npm install          # lần đầu (chỉ cần tailwindcss CLI)
npm run build:css    # build lại app/static/css/tailwind.css (đã minify)
npm run watch:css    # hoặc watch khi đang sửa giao diện
```

Commit cả file CSS đã build. Thứ tự cascade trong `base.html`: `daisyui.min.css` → `<style>` nội bộ → `tailwind.css`
(cuối `<head>`), nên utility responsive của Tailwind (`sm:`, `md:`) luôn thắng CSS nội bộ không có `!important`.
`/static` được gzip riêng; không bật GZip cho toàn app vì sẽ buffer SSE của `/api/logs/stream`.

## Key Patterns

### RequestContextLogger
Mọi background task dùng `RequestContextLogger` để attach request_id vào log:
```python
log = RequestContextLogger(logger, request_id)
log.info("Processing...")  # → "[abc12345] Processing..."
```

### Background Tasks
Webhook processing chạy async trong background:
```python
background_tasks.add_task(_process_subtitle_task, rating_key, event, request_id)
```
Cho phép webhook return `202 Accepted` ngay lập tức.

### Service Reinitialization
Sau khi update credentials qua Setup, gọi `reinit_service()` trong `main.py` để reinit `SubtitleService` với config mới.

### Tenacity Retry
Subsource API calls dùng tenacity với retry chỉ cho network errors:
```python
@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
)
```
Không retry khi "movie not found" (deterministic response).

### Subtitle Matching Strategy

Logic matching nằm chủ yếu trong `app/clients/subsource_client.py`:

- `_search_movie()`:
  - ưu tiên IMDb ID từ Plex
  - fallback sang title scoring theo title/year/season khi thiếu external ID
- `_rank_and_filter()`:
  - lọc exact episode trước, rồi season pack, rồi untagged fallback có kiểm soát
  - rerank theo `video_filename` để chọn đúng release hơn
- `_extract_subtitle_from_zip()`:
  - nếu ZIP có nhiều subtitle files, chấm điểm từng file con theo season/episode + filename similarity
  - tránh việc lấy `.srt` đầu tiên trong season pack

## Adding a New Language to Search Fallback

Trong `app/services/subtitle_service.py`:
```python
_FALLBACK_SOURCE_LANGS = ["ko", "ja", "zh", "fr", "es", "de", "pt", "ru", "it", "ar"]
```
Thêm ISO 639-1 code vào list, đảm bảo code tồn tại trong `LANGUAGE_MAP` của subsource_client.

## Docker Build

```bash
docker build -t plex-subtitle-service .
docker compose up -d
```

The image uses Debian Bookworm and NumPy <2.3 to support x86-64 machines
with SSE3 but without x86-64-v2. The build imports NumPy and WebRTC VAD and
exercises NumPy FFT in the runtime image. For a compatibility check, build and
run the image on the target VM (or an x86-64 emulator configured with the same
CPU features); a build on a newer CPU cannot establish compatibility with an
older CPU. Rebuild the image after changing the dependency lockfile.

`docker-compose.yml`:
```yaml
services:
  plex-subtitle-service:
    image: plex-subtitle-service:latest
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
    environment:
      - LOG_LEVEL=INFO
    restart: unless-stopped
```

## Contributing

1. Fork → feature branch → PR
2. Giữ language logic agnostic (không hard-code ngôn ngữ cụ thể)
3. Target lang luôn từ `runtime_config.default_language`
4. Source lang luôn là bất kỳ lang nào ≠ target
