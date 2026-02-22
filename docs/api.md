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

## Sync

### `POST /api/sync/preview`
Kiểm tra subtitle có sẵn cho một media item.

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
    "detail": "Subsource cũng không có VI sub khớp tập này"
  },
  "has_vi_on_plex": false,
  "has_source_on_plex": true,
  "source_candidates": [],
  "vi_candidates": [
    {"id": "abc123", "name": "Breaking.Bad.S01E01.VI", "quality": "translated", "downloads": 1500, "rating": 4.2}
  ],
  "can_sync": true,
  "can_translate": false,
  "source_lang": "en",
  "sync_enabled": true
}
```

### `POST /api/sync/execute`
Thực hiện sync timing.

**Request:**
```json
{
  "rating_key": "12345",
  "subtitle_id": "abc123",
  "source_lang": "en"
}
```
- `subtitle_id`: VI sub ID từ `vi_candidates` (optional, nếu bỏ qua sẽ dùng bản tốt nhất)
- `source_lang`: ngôn ngữ dùng làm timing reference (mặc định `"en"`)

**Response:**
```json
{
  "status": "success",
  "message": "Timing synced (42 anchors, ref: EN from plex, vi: subsource)",
  "stats": {"anchors_found": 42, "avg_offset_ms": 150}
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
