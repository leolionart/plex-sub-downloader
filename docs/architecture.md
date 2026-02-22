# Architecture

## Overview

```
Plex / Tautulli
      │ webhook
      ▼
┌─────────────────────────────────────┐
│         Subtitle Service            │
│                                     │
│  FastAPI  ──▶  SubtitleService      │
│                    │                │
│          ┌─────────┼──────────┐     │
│          ▼         ▼          ▼     │
│      PlexClient  Subsource  OpenAI  │
│                  Client     Client  │
└─────────────────────────────────────┘
      │                  │
      ▼                  ▼
 Plex API            Subsource API
 (upload sub)        (search sub)
```

## Key Components

| File | Role |
|------|------|
| `app/main.py` | FastAPI app, webhook handling, background tasks |
| `app/services/subtitle_service.py` | Core orchestration logic |
| `app/clients/plex_client.py` | Plex API wrapper |
| `app/clients/subsource_client.py` | Subsource search/download, `LANGUAGE_MAP` |
| `app/clients/openai_translation_client.py` | AI translation (language-agnostic) |
| `app/clients/sync_client.py` | Subtitle timing sync via AI |
| `app/models/runtime_config.py` | Runtime config (credentials, URLs) |
| `app/models/settings.py` | User-facing settings (`SubtitleSettings`) |
| `app/routes/sync.py` | `/api/sync/*` — preview, execute, translate |
| `app/routes/translation.py` | `/api/translation/*` — approval queue |
| `app/templates/sync.html` | Sync/Translate Web UI |

## Language Design (quan trọng)

**Không có gì bị hard-code theo ngôn ngữ cụ thể.**

- **Target lang** = `runtime_config.default_language` (cấu hình trong Settings, mặc định `"vi"`)
- **Source lang** = bất kỳ ngôn ngữ nào ≠ target, dùng làm tham chiếu cho sync/translate

Ví dụ: nếu user đặt default language = `"vi"`:
- Tìm bất kỳ sub nào không phải VI để làm source (EN, KO, JA, ZH...)
- Translate source → VI
- Sync timing VI theo source

Nếu user đặt default language = `"fr"`:
- Tương tự nhưng target là FR

## Webhook Processing Flow

```
Nhận webhook (library.new / media.play)
    │
    ├─ Dedup check (đang xử lý ratingKey này chưa?)
    │
    ├─ Delay (nếu library.new): chờ subtitle_settings.new_media_delay_seconds
    │   Lý do: Plex fire event trước khi metadata đầy đủ (có thể trả về type=Show/Season)
    │
    ├─ Fetch video metadata từ Plex
    │
    ├─ Check điều kiện bỏ qua:
    │   - skip_if_has_subtitle: đã có target lang sub?
    │   - skip_forced_subtitles: là forced sub?
    │   - skip_if_embedded: là embedded?
    │   - min_quality_threshold: chất lượng sub hiện tại có đủ cao?
    │
    ├─ Search target lang sub trên Subsource
    │
    ├─ Download + upload lên Plex
    │
    └─ Nếu không tìm được: fallback AI translate
        (nếu translation_enabled và có OpenAI key)
```

## Preview Flow (Sync UI)

`preview_sync_for_media()` trong `subtitle_service.py`:

```
1. Fetch video từ Plex

2. Check target sub trên Plex TRƯỚC (fast, không gọi API ngoài)
   → has_target_on_plex = True?
   → Nếu có: skip toàn bộ Subsource searches

3. Source sub detection (language-agnostic):
   a. Lấy tất cả langs có trên Plex:
      plex_client._get_existing_subtitle_languages(video)
   b. Filter ra langs ≠ target
   c. Sort: EN ưu tiên, còn lại alphabet
   d. Try download text-based sub từng lang
   e. Nếu không có trên Plex VÀ target chưa có:
      → Search Subsource: EN trước → ko, ja, zh, fr, es, de...
      → Dừng ngay khi tìm được

4. Target sub candidates (chỉ khi chưa có trên Plex):
   → Search Subsource cho target lang

5. Return:
   can_sync = has_source AND has_target
   can_translate = has_source AND translation_enabled
   source_lang = ngôn ngữ tìm được (e.g. "ko")
```

**Nguyên tắc:** nếu target sub đã có trên Plex dạng text-based → không chạy bất kỳ AI/search feature nào.

## Execute Sync Flow

`execute_sync_for_media(rating_key, subtitle_id, source_lang)`:

```
1. Tìm reference (source) sub:
   a. Download source_lang từ Plex
   b. Nếu không có: search Subsource cho source_lang
   c. Fallback: thử EN (nếu source_lang ≠ EN), rồi các langs khác

2. Tìm target sub:
   a. Download target lang từ Plex
   b. Nếu không có: search Subsource (dùng subtitle_id nếu user chọn)

3. sync_client.sync_subtitles(reference=source, target=vi_sub)

4. Upload synced sub lên Plex
```

## Execute Translate Flow

`execute_translate_for_media(rating_key, from_lang)`:

```
1. Download source sub (from_lang) từ Plex hoặc Subsource
2. translation_client.translate_srt_file(from_lang=from_lang, to_lang=default_language)
   → Prompt: "translator from {from_lang} to {to_lang}" — hoạt động với mọi ngôn ngữ
3. Upload translated sub lên Plex
```

## Settings Architecture

Có 2 lớp config:

| | `RuntimeConfig` | `SubtitleSettings` |
|---|---|---|
| File | `app/models/runtime_config.py` | `app/models/settings.py` |
| Cập nhật qua | `/api/setup/config` | `/api/settings` |
| UI | Setup page (`/setup`) | Settings page (`/`) |
| Chứa | Credentials (Plex URL, token, API keys) | Tất cả behavioral settings |
| Persisted | `data/config.json` | `data/config.json` (nested) |

`SubtitleSettings` bao gồm: `default_language`, `languages`, `skip_if_has_subtitle`, `auto_sync_timing`, `translation_enabled`, `new_media_delay_seconds`, v.v.

## Subsource Fallback Language Order

Khi EN không tìm được, thử theo thứ tự:
```python
["ko", "ja", "zh", "fr", "es", "de", "pt", "ru", "it", "ar"]
```
