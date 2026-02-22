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
└── templates/
    ├── settings.html        # Main UI (/)
    ├── sync.html            # Sync/Translate UI (/sync)
    ├── translation.html     # Translation approval (/translation)
    ├── logs.html            # Log viewer (/logs)
    └── setup.html           # Setup wizard (/setup)
```

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
