# Configuration

Tất cả cấu hình được lưu tại `data/config.json` và có thể chỉnh qua Web UI.

## Credentials (Setup page — `/setup`)

| Field | Mô tả | Bắt buộc |
|-------|-------|----------|
| `plex_url` | URL Plex server (e.g. `http://192.168.1.x:32400`) | ✓ |
| `plex_token` | Plex authentication token | ✓ |
| `subsource_api_key` | API key từ subsource.net | ✓ |
| `openai_api_key` | OpenAI hoặc compatible API key | Chỉ cần cho AI translate/sync |
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
| `auto_sync_timing` | Tự động sync timing sau khi download subtitle | `false` |
| `translation_enabled` | Bật AI translate fallback | `false` |
| `translation_requires_approval` | Yêu cầu approve trước khi dịch | `true` |
| `auto_translate_if_no_vi` | Tự động dịch khi không tìm được target sub | `false` |

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
