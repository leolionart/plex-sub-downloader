# API Reference

Base URL: `http://your-server:8000`

Interactive docs: `/docs` (Swagger UI)

---

## Webhook

### `POST /webhook`
Nhận events từ Plex hoặc Tautulli.

**Plex format** (multipart/form-data):
```
payload={"event":"library.new","Metadata":{"ratingKey":"12345","type":"episode"}}
```

**Tautulli format** (application/json):
```json
{"event": "library.new", "rating_key": "12345", "media_type": "episode"}
```

**Response:** `202 Accepted` — task được queue, xử lý async.

---

## Subtitle Providers

### `GET /api/subtitles/providers`
Xem provider nào đang được bật hoặc bị skip vì thiếu credential.

**Response:**
```json
{
  "enabled": ["subsource", "opensubtitles", "subdl"],
  "cache_scope": "subsource,opensubtitles,subdl",
  "providers": [
    {"name": "subsource", "enabled": true, "configured": true, "reason": "configured"},
    {"name": "opensubtitles", "enabled": true, "configured": true, "reason": "configured"},
    {"name": "subdl", "enabled": false, "configured": false, "reason": "missing subdl_api_key"}
  ]
}
```

### `POST /api/subtitles/search`
Tìm subtitle qua tất cả provider đã bật. Có thể tìm theo `rating_key` Plex hoặc metadata trực tiếp.

**Request theo Plex media:**
```json
{"rating_key": "12345", "language": "vi", "use_cache": false}
```

**Request theo metadata:**
```json
{
  "title": "Breaking Bad",
  "year": 2008,
  "imdb_id": "tt0903747",
  "season": 1,
  "episode": 1,
  "language": "vi",
  "video_filename": "Breaking.Bad.S01E01.1080p.mkv"
}
```

**Response:**
```json
{
  "language": "vi",
  "provider_counts": {"subsource": 3, "opensubtitles": 8, "subdl": 2},
  "candidates": [
    {
      "id": "12345",
      "provider": "opensubtitles",
      "provider_id": "opensubtitles:12345",
      "name": "Breaking.Bad.S01E01.WEB-DL",
      "quality": "translated",
      "downloads": 1500,
      "rating": 8.5,
      "score": 560
    }
  ]
}
```

`provider_id` là giá trị nên truyền vào các API chọn subtitle để tránh trùng ID giữa các nguồn.

### `POST /api/subtitles/download`
Tìm và tải subtitle file về client, không upload vào Plex.

**Request:**
```json
{
  "rating_key": "12345",
  "language": "vi",
  "subtitle_id": "opensubtitles:12345",
  "use_cache": true
}
```

**Response:** file `.srt`.

### `POST /api/subtitles/upload`
Tìm/download và upload target subtitle vào Plex.

**Request:**
```json
{
  "rating_key": "12345",
  "subtitle_id": "subdl:3197651-3213944"
}
```

**Response:**
```json
{
  "status": "success",
  "message": "Uploaded VI subtitle: Breaking.Bad.S01E01.VI",
  "subtitle": {
    "id": "3197651-3213944",
    "provider": "subdl",
    "name": "Breaking.Bad.S01E01.VI",
    "quality": "translated"
  }
}
```

## Sync

### `POST /api/sync/preview`
Kiểm tra subtitle có sẵn cho một media item và trả candidate từ provider đã bật.

**Request:**
```json
{"rating_key": "12345"}
```

**Response:**
```json
{
  "rating_key": "12345",
  "title": "Breaking Bad S01E01",
  "media_type": "episode",
  "source_status": {
    "available": true,
    "source": "plex",
    "detail": "Plex (text-based, English)"
  },
  "vi_status": {
    "available": false,
    "source": null,
    "detail": "Không có VI sub khớp từ provider đã bật"
  },
  "has_vi_on_plex": false,
  "has_source_on_plex": true,
  "source_candidates": [],
  "vi_candidates": [
    {
      "id": "abc123",
      "provider": "opensubtitles",
      "name": "Breaking.Bad.S01E01.VI",
      "quality": "translated",
      "downloads": 1500,
      "rating": 4.2
    }
  ],
  "can_sync": true,
  "can_translate": false,
  "source_lang": "en",
  "sync_enabled": true
}
```

Manual preview sẽ vẫn trả về `vi_candidates` ngay cả khi Plex đã có subtitle target,
để UI/API cho phép thay thử subtitle khác từ provider đã bật.

### `POST /api/sync/execute`
Thực hiện sync timing.

**Request:**
```json
{
  "rating_key": "12345",
  "subtitle_id": "opensubtitles:abc123",
  "source_lang": "en"
}
```
- `subtitle_id`: `provider_id` hoặc raw ID từ `vi_candidates` (optional, nếu bỏ qua sẽ dùng bản tốt nhất)
- `source_lang`: ngôn ngữ dùng làm timing reference (mặc định `"en"`)

**Response:**
```json
{
  "status": "success",
  "message": "Timing synced (42 anchors, ref: EN from plex, vi: opensubtitles)",
  "stats": {"anchors_found": 42, "avg_offset_ms": 150}
}
```

### `POST /api/sync/upload-target`
Tìm và upload target subtitle thủ công từ provider đã bật.

**Request:**
```json
{
  "rating_key": "12345",
  "subtitle_id": "subdl:abc123"
}
```
- `subtitle_id`: `provider_id` hoặc raw ID từ `vi_candidates` (optional, nếu bỏ qua sẽ dùng bản đầu tiên tải được)

**Response:**
```json
{
  "status": "success",
  "message": "Uploaded VI subtitle: Breaking.Bad.S01E01.VI",
  "subtitle": {
    "id": "abc123",
    "provider": "subdl",
    "name": "Breaking.Bad.S01E01.VI",
    "quality": "translated"
  }
}
```

### `POST /api/sync/translate`
Dịch source sub sang target lang bằng AI.

**Request:**
```json
{
  "rating_key": "12345",
  "from_lang": "ko"
}
```
- `from_lang`: ngôn ngữ nguồn (mặc định `"en"`, hỗ trợ mọi ngôn ngữ)

**Response:**
```json
{
  "status": "success",
  "message": "Translation completed"
}
```

### `GET /api/sync/now-playing`
Lấy danh sách sessions đang phát trên Plex.

**Response:**
```json
{
  "sessions": [
    {
      "rating_key": "12345",
      "title": "Breaking Bad",
      "type": "episode",
      "player": {"title": "Chrome", "state": "playing"}
    }
  ]
}
```

### `POST /api/sync/resolve-url`
Convert Plex share link hoặc URL thành rating key.

**Request:**
```json
{"input": "https://app.plex.tv/desktop/#!/server/.../metadata/12345"}
```

**Response:**
```json
{"rating_key": "12345"}
```

### `GET /api/sync/status`
Trạng thái sync feature.

---

## Translation Approval

### `GET /api/translation/pending`
Danh sách translations đang chờ duyệt.

### `POST /api/translation/estimate`
Ước tính chi phí dịch.

**Request:** `{"rating_key": "12345"}`

### `POST /api/translation/approve`
Duyệt và thực hiện translation.

**Request:** `{"rating_key": "12345"}`

### `POST /api/translation/reject`
Từ chối translation.

**Request:** `{"rating_key": "12345"}`

### `GET /api/translation/stats`
Thống kê translation.

---

## Settings

### `GET /api/settings`
Lấy cấu hình hiện tại.

### `POST /api/settings`
Cập nhật cấu hình. Body là object với bất kỳ field nào trong `SubtitleSettings`.

---

## Setup

### `GET /api/setup/config`
Lấy runtime config (credentials).

### `POST /api/setup/config`
Cập nhật credentials và test kết nối.

### `POST /api/setup/test`
Kiểm tra kết nối Plex / Subsource.

---

## Logs

### `GET /api/logs`
Stream log entries (SSE).

Query params: `level=info|warning|error|debug`, `limit=100`

---

## Utilities

### `GET /health`
Health check cho Docker.
```json
{"status": "healthy"}
```

### `GET /api/info`
Thông tin API và endpoints.

### `GET /api/sync/thumb/{rating_key}`
Proxy thumbnail từ Plex (tránh CORS).
