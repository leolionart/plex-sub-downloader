# Configuration

Tất cả cấu hình được lưu tại `data/config.json` và có thể chỉnh qua Web UI.

## Credentials (Setup page — `/setup`)

| Field | Mô tả | Bắt buộc |
|-------|-------|----------|
| `plex_url` | URL Plex server (e.g. `http://192.168.1.x:32400`) | ✓ |
| `plex_token` | Plex authentication token | ✓ |
| `subsource_api_key` | API key từ subsource.net | Cần ít nhất một provider |
| `opensubtitles_api_key` | API key từ OpenSubtitles.com REST API | Cần ít nhất một provider |
| `opensubtitles_username` | Username OpenSubtitles để lấy token download | Không |
| `opensubtitles_password` | Password OpenSubtitles để lấy token download | Không |
| `subdl_api_key` | API key từ subdl.com | Cần ít nhất một provider |
| `openai_api_key` | OpenAI hoặc compatible API key | Chỉ cần cho AI translate/text alignment; không cần cho audio sync |
| `openai_base_url` | Custom API endpoint (OpenRouter, proxy...) | Không |
| `openai_model` | Model name (mặc định: `gpt-4o-mini`) | Không |
| `telegram_bot_token` | Bot token cho notifications | Không |
| `telegram_chat_id` | Chat/group ID nhận notifications | Không |
| `webhook_secret` | Secret header để authenticate webhook | Không |

## Download Settings (Settings page — `/`)

| Field | Mô tả | Mặc định |
|-------|-------|---------|
| `default_language` | Ngôn ngữ target chính (ISO 639-1, e.g. `vi`) | `vi` |
| `languages` | Danh sách ngôn ngữ download (multi-select) | `["vi"]` |
| `auto_download_on_add` | Tự động tải khi có media mới (`library.new`) | `true` |
| `auto_download_on_play` | Tự động tải khi bắt đầu play | `true` |
| `skip_if_has_subtitle` | Bỏ qua nếu đã có target lang sub | `true` |
| `replace_existing` | Thay sub hiện tại nếu tìm được bản tốt hơn | `false` |
| `replace_only_if_better_quality` | Chỉ thay khi chất lượng cao hơn | `true` |
| `min_quality_threshold` | Chất lượng tối thiểu: `ai` / `translated` / `retail` | `translated` |
| `skip_forced_subtitles` | Bỏ qua forced subtitles | `true` |
| `skip_if_embedded` | Bỏ qua nếu đã có embedded sub | `true` |
| `new_media_delay_seconds` | Delay sau `library.new` trước khi xử lý (0-300s) | `30` |

**Lý do `new_media_delay_seconds`:** Plex fire event `library.new` cho Show → Season → Episode liên tiếp. Nếu xử lý ngay, metadata có thể chưa đầy đủ (trả về type `Show` thay vì `episode`). Delay 30s giúp Plex hoàn tất index.

## AI / Sync Settings

| Field | Mô tả | Mặc định |
|-------|-------|---------|
| `auto_sync_timing` | Sync subtitle với Plex audio trước khi upload; không upload nếu audio không khả dụng hoặc kết quả không tin cậy | `false` |
| `ai_validate_subtitle_matches` | Dùng AI để xác thực candidate mơ hồ trước khi auto download/upload | `true` |
| `ai_match_min_confidence` | Confidence tối thiểu để nhận candidate được AI xác thực | `0.7` |
| `translation_enabled` | Bật AI translate fallback | `false` |
| `translation_requires_approval` | Yêu cầu approve trước khi dịch | `true` |
| `auto_translate_if_no_vi` | Tự động dịch khi không tìm được target sub | `false` |
| `translation_context_enabled` | Trích xuất glossary/thuật ngữ trước khi dịch batch (mặc định tắt để tối ưu token) | `false` |
| `translation_context_window` | Số câu trước/sau gửi kèm mỗi batch để giữ hội thoại lân cận | `10` |
| `translation_consistency_review_enabled` | Rà soát toàn tập sau dịch để sửa xưng hô/tên riêng không nhất quán | `true` |

### Audio synchronization

Manual sync and `auto_sync_timing` use the selected audio track of the first Plex
media version, streamed from the configured `plex_url` using `plex_token`.
No local media mount or OpenAI key is needed. Docker includes `ffmpeg`; native
installations must install it separately and install the Python dependencies.
Plex must expose an accessible single-part media URL and a known duration from
30 seconds to four hours. Extraction is limited to ten minutes of wall time.

Audio sync estimates a **constant offset**, not scene edits or frame-rate drift.
It compares WebRTC voice activity to subtitle cue activity at 20 ms resolution,
searching at most ±60 seconds. The normalized correlation must reach 0.15 with
a 0.015 margin over competing peaks outside one second. Three independent thirds
must each reach 0.10 correlation and agree within 600 ms (with 0.005 peak margin).
Sparse dialogue, ambiguous matches, silence, wrong cuts and drift are rejected.
These are conservative heuristic checks, not a calibrated probability or a
guarantee of word-level alignment; cues may include signs/music or span pauses.

On failure no candidate is uploaded and existing subtitles remain untouched.
Successful sync adds a subtitle without deleting existing tracks, even when
`replace_existing` is enabled. The original track therefore remains available
for comparison. Provider-selected targets are honored rather than silently
falling back to another candidate. Source-language subtitles remain useful for
translation, but are not treated as proof of video/audio synchronization.

## Environment Variables (Docker)

Có thể seed giá trị ban đầu qua environment variables (bị override bởi `data/config.json` nếu đã tồn tại):

```yaml
environment:
  - PLEX_URL=http://192.168.1.100:32400
  - PLEX_TOKEN=your_token
  - SUBSOURCE_API_KEY=your_key
  - OPENAI_API_KEY=sk-...       # Optional
  - TELEGRAM_BOT_TOKEN=...      # Optional
  - TELEGRAM_CHAT_ID=...        # Optional
  - DEFAULT_LANGUAGE=vi
  - LOG_LEVEL=INFO
```

## Webhook Configuration

### Plex Webhook
Vào **Plex Settings → Webhooks → Add Webhook**:
```
http://your-server:8000/webhook
```

### Tautulli Webhook
Vào **Tautulli → Notification Agents → Webhook**:
```
URL: http://your-server:8000/webhook
Method: POST
Content-Type: application/json
Triggers: Recently Added
```

Payload JSON:
```json
{
  "event": "library.new",
  "rating_key": "{rating_key}",
  "media_type": "{media_type}"
}
```

Nếu cấu hình `webhook_secret`, thêm header:
```
X-Webhook-Secret: your_secret
```
