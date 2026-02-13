# Plex Subtitle Service - Cách hoạt động

## Tổng quan

Service nhận event từ Plex/Tautulli qua webhook, tự động tìm subtitle tiếng Việt trên Subsource, tải về và upload lên Plex. Nếu không tìm được sub tiếng Việt, service có thể tự động dịch từ sub tiếng Anh bằng OpenAI.

## Kiến trúc

```
Plex/Tautulli ──webhook──→ FastAPI ──background task──→ SubtitleService
                                                            │
                                    ┌───────────────────────┼───────────────────────┐
                                    │                       │                       │
                                PlexClient          SubsourceClient        OpenAITranslation
                              (metadata, upload)    (search, download)      (dịch en→vi)
                                    │                       │                       │
                                    │                  CacheClient            TelegramClient
                                    │                 (cache kết quả)         (notification)
                                Plex Server            Subsource API          OpenAI API
```

## Luồng xử lý chi tiết

### 1. Nhận webhook

**File:** `app/main.py` → `POST /webhook`

Service nhận webhook từ 2 nguồn:
- **Plex native** (Plex Pass): `multipart/form-data` với field `payload` chứa JSON
- **Tautulli** (miễn phí): `application/json` trực tiếp

Payload được parse để lấy 3 thông tin:
- `event` — loại sự kiện (`library.new`, `media.play`, ...)
- `rating_key` — ID nội bộ của media trong Plex
- `media_type` — loại media (movie, episode)

**Event filter** (`_should_process_event`):
| Event | Ý nghĩa | Xử lý |
|-------|---------|-------|
| `library.new` | Media mới thêm vào thư viện | ✅ Luôn accept |
| `library.on.deck` | Media xuất hiện trên On Deck | ✅ Luôn accept |
| `media.play` | User bấm play | ✅ Accept (kiểm tra setting ở bước sau) |
| `media.pause/stop/resume` | Playback events khác | ❌ Bỏ qua |
| `media.scrobble` | Xem xong | ❌ Bỏ qua |

Nếu event được accept → tạo **background task** và trả về `202 Accepted` ngay lập tức (không block webhook response).

### 2. Kiểm tra settings

**File:** `app/models/settings.py` → `should_download_on_event()`

Trước khi xử lý, service kiểm tra user settings:
- `library.new` → chỉ xử lý nếu `auto_download_on_add = true`
- `media.play` → chỉ xử lý nếu `auto_download_on_play = true`

Nếu setting tắt → skip, log "Event disabled in settings".

### 3. Lấy metadata từ Plex

**File:** `app/clients/plex_client.py`

Service dùng `rating_key` để gọi Plex API:

```
PlexClient.get_video(rating_key)
    → PlexServer.fetchItem(rating_key)
    → Video object (Movie hoặc Episode)

PlexClient.extract_metadata(video)
    → MediaMetadata {
        title: "Inception"
        year: 2010
        search_title: "Inception"  (dùng để search sub)
        imdb_id: "tt1375666"
        tmdb_id: "27205"
        season_number: null        (có nếu là episode)
        episode_number: null
        rating_key: "12345"
    }
```

### 4. Kiểm tra subtitle hiện có

**File:** `app/services/subtitle_service.py` → `_should_download_subtitle()`

Kiểm tra 4 điều kiện theo thứ tự:

| # | Kiểm tra | Setting | Kết quả nếu match |
|---|----------|---------|-------------------|
| 1 | Đã có subtitle tiếng Việt? | `skip_if_has_subtitle` + `replace_existing` | Skip nếu có sub VÀ không cho replace |
| 2 | Có forced subtitle? | `skip_forced_subtitles` | Skip nếu có forced sub |
| 3 | Có embedded subtitle (PGS/VobSub)? | `skip_if_embedded` | Skip nếu có embedded |
| 4 | Replace mode? | `replace_existing` | Cho phép tải sub mới thay thế |

Nếu tất cả check pass → tiếp tục tìm subtitle.

### 5. Tìm subtitle trên Subsource

**File:** `app/clients/subsource_client.py` → `search_subtitles()`

```
SubtitleSearchParams {
    language: "vi"
    title: "Inception"
    year: 2010
    imdb_id: "tt1375666"
    season: null
    episode: null
}
```

**Quy trình tìm kiếm:**
1. **Kiểm tra cache** — nếu đã search trước đó (trong `cache_ttl_seconds`, mặc định 3600s) → dùng kết quả cache
2. **Gọi Subsource API** — search subtitle bằng title + language
3. **Sắp xếp kết quả** theo priority score (quality type + match score)
4. **Cache kết quả** cho lần sau
5. **Trả về subtitle tốt nhất** (đầu tiên trong danh sách đã sort)

**Kiểm tra quality threshold** (`_meets_quality_threshold`):

```
Quality ranking: retail (3) > translated (2) > ai (1) > unknown (0)
Setting min_quality_threshold mặc định: "translated"

→ Chỉ accept subtitle có quality ≥ translated (tức translated hoặc retail)
→ Reject ai và unknown quality
```

### 6. Tải subtitle

**File:** `app/clients/subsource_client.py` → `download_subtitle()`

- Tải file `.srt` từ Subsource API
- Lưu vào thư mục tạm: `/tmp/plex-subtitles/{rating_key}/`

### 7. Upload lên Plex

**File:** `app/clients/plex_client.py` → `upload_subtitle()`

- Upload file `.srt` lên Plex server qua API
- Plex tự gắn subtitle vào media item tương ứng
- Dọn dẹp file tạm sau khi upload xong

### 8. Notification

**File:** `app/clients/telegram_client.py`

Gửi Telegram notification cho mỗi kết quả:
- ✅ **Tải thành công** — tên phim, tên sub, quality
- ❌ **Không tìm được** — tên phim, language
- ⚠️ **Lỗi** — tên phim, error message

---

## Translation Fallback (dịch sub bằng AI)

Khi **không tìm được subtitle tiếng Việt** VÀ `translation_enabled = true`:

### Luồng dịch

```
Không tìm được sub VI
    │
    ├─→ Search subtitle tiếng Anh (EN) trên Subsource
    │       └─→ Không tìm được EN? → Dừng, báo "not found"
    │
    ├─→ Tìm được sub EN
    │       │
    │       ├─→ translation_requires_approval = true?
    │       │       │
    │       │       YES → Thêm vào pending queue
    │       │             → Gửi Telegram "cần duyệt"
    │       │             → User duyệt qua Web UI (/translation)
    │       │             → Sau khi approve → thực hiện dịch
    │       │
    │       │       NO → Dịch tự động ngay
    │       │
    │       └─→ Thực hiện dịch (execute_translation)
    │               │
    │               ├─→ Tải sub EN về
    │               ├─→ OpenAI dịch EN → VI (batch theo từng nhóm dòng)
    │               ├─→ Lưu file .vi.srt
    │               ├─→ Upload lên Plex
    │               └─→ Gửi Telegram "dịch xong, X dòng"
```

### Chi tiết dịch thuật

**File:** `app/clients/openai_translation_client.py`

- Parse file `.srt` thành các blocks (timestamp + text)
- Chia thành batch (nhóm nhiều dòng)
- Gửi từng batch cho OpenAI API để dịch
- Ghép lại thành file `.srt` mới với timestamp giữ nguyên

**Config:**
- `openai_api_key` — API key
- `openai_base_url` — Base URL (hỗ trợ proxy/custom endpoint)
- `openai_model` — Model dùng để dịch (mặc định `gpt-4o-mini`)

---

## Sơ đồ tổng thể

```
Plex play/add media
        │
        ▼
  Webhook trigger
  (Plex native hoặc Tautulli)
        │
        ▼
  POST /webhook
        │
        ▼
  Event filter ──────────── event không hỗ trợ? → 200 ignored
        │
        ▼
  Settings check ────────── setting tắt? → skip
        │
        ▼
  Plex API: lấy metadata
        │
        ▼
  Kiểm tra sub hiện có ──── đã có sub? → skip (tuỳ setting)
        │
        ▼
  Subsource: tìm sub VI
        │
    ┌───┴───┐
    │       │
  Tìm được  Không tìm được
    │       │
    │       ├─→ translation_enabled?
    │       │       │
    │       │     NO → Telegram "not found" → Dừng
    │       │       │
    │       │     YES → Tìm sub EN
    │       │            │
    │       │        ┌───┴───┐
    │       │      Có EN   Không có EN → Dừng
    │       │        │
    │       │     Dịch EN→VI (OpenAI)
    │       │        │
    │       └────────┘
    │                │
    ▼                ▼
  Quality check ── dưới threshold? → skip
    │
    ▼
  Download .srt
    │
    ▼
  Upload lên Plex
    │
    ▼
  Telegram notification ✅
    │
    ▼
  Cleanup temp files
```

## Files chính

| File | Vai trò |
|------|---------|
| `app/main.py` | FastAPI app, webhook endpoint, routing |
| `app/services/subtitle_service.py` | Orchestrator — điều phối toàn bộ workflow |
| `app/clients/plex_client.py` | Giao tiếp Plex API (metadata, upload) |
| `app/clients/subsource_client.py` | Giao tiếp Subsource API (search, download) |
| `app/clients/openai_translation_client.py` | Dịch subtitle bằng OpenAI |
| `app/clients/telegram_client.py` | Gửi notification Telegram |
| `app/clients/cache_client.py` | Cache kết quả search (in-memory hoặc Redis) |
| `app/models/settings.py` | SubtitleSettings + ServiceConfig |
| `app/models/runtime_config.py` | RuntimeConfig (credentials, URLs) |
| `app/routes/setup.py` | API cho Setup page |
| `app/routes/logs.py` | API cho Log viewer (SSE real-time) |
| `app/routes/translation.py` | API cho Translation approval |
