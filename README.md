# Plex Subtitle Service

Tự động tải phụ đề tiếng Việt cho Plex Media Server. Nhận webhook từ Plex hoặc Tautulli, tìm subtitle trên Subsource, upload thẳng vào Plex — không cần mount thư viện media.

Khi không tìm được sub tiếng Việt, service có thể tự dịch từ sub tiếng Anh bằng OpenAI.

```
Plex/Tautulli ──webhook──▸ Subtitle Service ──▸ Subsource (tìm + tải sub)
                                │                       │
                                ▾                       ▾
                          Plex API (upload)      OpenAI (dịch EN→VI)
```

## Tính năng

- **Tự động hoàn toàn** — subtitle được tải và upload khi có media mới hoặc khi bấm play
- **Dịch thuật AI** — fallback dịch EN → VI bằng OpenAI khi không tìm được sub Việt
- **Web UI** — setup wizard, cài đặt, quản lý dịch thuật, xem log real-time
- **Telegram notifications** — thông báo khi tìm thấy, tải xong, hoặc lỗi
- **Redis cache** — cache kết quả tìm kiếm, giảm API calls
- **Multi-format** — hỗ trợ .srt, .vtt, .ass, .ssa, .sub (tự convert về SRT)
- **Download retry** — thử nhiều subtitle candidates nếu bản đầu lỗi
- **Ưu tiên chất lượng** — Retail > Translated > AI, có threshold tùy chỉnh
- **Multi-platform Docker** — image sẵn cho cả amd64 và arm64

## Cài đặt

### Yêu cầu

- Docker & Docker Compose
- Plex Media Server
- Subsource API key — [đăng ký tại subsource.net](https://subsource.net)
- Plex token — [hướng dẫn lấy token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/) (hoặc login qua Setup wizard)

> **Webhook:** Plex yêu cầu Plex Pass để gửi webhook. Nếu không có Plex Pass, dùng [Tautulli](https://tautulli.com/) (miễn phí) làm trung gian.

### 1. Tạo docker-compose.yml

```bash
mkdir plex-subtitle-service && cd plex-subtitle-service
```

Tạo file `docker-compose.yml`:

```yaml
services:
  subtitle-service:
    image: ghcr.io/leolionart/plex-sub-downloader:latest
    container_name: plex-subtitle-service
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data

  # Optional: Redis cache (giảm API calls)
  redis:
    image: redis:7-alpine
    container_name: plex-subtitle-redis
    restart: unless-stopped
    command: redis-server --appendonly yes
    volumes:
      - ./data/redis:/data
```

### 2. Khởi chạy

```bash
docker compose up -d
```

### 3. Cấu hình qua Web UI

Mở `http://<ip-máy>:8000/setup` — wizard sẽ hướng dẫn cấu hình:

1. **Kết nối Plex** — login bằng Plex account hoặc nhập URL + token thủ công
2. **Subsource API** — nhập API key
3. **OpenAI Translation** — (tùy chọn) API key và model để dịch sub
4. **Notifications** — (tùy chọn) Telegram bot token và chat ID
5. **Cache** — (tùy chọn) Redis URL

Toàn bộ cấu hình được lưu tại `./data/config.json` trong thư mục triển khai.

### 4. Cấu hình webhook

#### Plex (cần Plex Pass)

1. Plex Web → Settings → Webhooks → Add Webhook
2. URL: `http://<ip-máy-chạy-service>:8000/webhook`

#### Tautulli (miễn phí)

1. Tautulli → Settings → Notification Agents → Add → Webhook
2. URL: `http://<ip-máy-chạy-service>:8000/webhook`
3. Method: `POST`
4. Triggers: bật **Recently Added** và/hoặc **Playback Start**
5. Tab Data → Recently Added → paste:

```json
{
  "event": "library.new",
  "rating_key": "{rating_key}",
  "media_type": "{media_type}"
}
```

### 5. Auto-update với Watchtower (tùy chọn)

Thêm Watchtower vào docker-compose để tự động cập nhật khi có version mới:

```yaml
  watchtower:
    image: containrrr/watchtower
    container_name: watchtower
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    command: --interval 300 plex-subtitle-service
```

Watchtower sẽ kiểm tra GHCR mỗi 5 phút và tự restart container khi có image mới.

## Web UI

Service có 4 trang web tại `http://<ip>:8000`:

| Trang | Đường dẫn | Mô tả |
|-------|-----------|-------|
| Settings | `/` | Cài đặt hành vi subtitle (auto-download, quality threshold, replace) |
| Setup | `/setup` | Wizard cấu hình Plex, Subsource, OpenAI, Telegram, Redis |
| Translation | `/translation` | Xem và duyệt các bản dịch đang chờ (khi bật approval mode) |
| Logs | `/logs` | Xem log real-time với filter và search |

## Lưu trữ dữ liệu

Toàn bộ dữ liệu nằm trong thư mục `./data` cạnh `docker-compose.yml`:

```
plex-subtitle-service/
├── docker-compose.yml
└── data/
    ├── config.json          ← cấu hình service (từ Setup wizard)
    └── redis/               ← Redis data (cache)
```

Backup: chỉ cần copy thư mục `data/`. Di chuyển máy: copy cả thư mục `plex-subtitle-service/`.

## Biến môi trường (nâng cao)

Cấu hình chính được quản lý qua Web UI. Các biến môi trường dưới đây chỉ dành cho infrastructure — hiếm khi cần thay đổi:

| Biến | Mặc định | Mô tả |
|------|----------|-------|
| `APP_PORT` | `8000` | Port bind của service |
| `LOG_LEVEL` | `INFO` | Mức log (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `MAX_RETRIES` | `3` | Số lần retry khi API lỗi |
| `RETRY_DELAY` | `2` | Delay giữa các lần retry (giây) |
| `CONFIG_FILE` | `data/config.json` | Đường dẫn file config |

## Cách hoạt động

Xem [HOW_IT_WORKS.md](HOW_IT_WORKS.md) cho sơ đồ chi tiết toàn bộ luồng xử lý.

Tóm tắt:

1. Nhận webhook event (`library.new`, `media.play`)
2. Lấy metadata từ Plex (title, year, IMDb ID)
3. Kiểm tra đã có subtitle chưa (skip nếu có, tùy setting)
4. Tìm subtitle tiếng Việt trên Subsource
5. Nếu không tìm được + translation enabled → dịch từ sub tiếng Anh
6. Kiểm tra quality threshold
7. Download subtitle (retry với candidates khác nếu lỗi)
8. Upload lên Plex
9. Gửi Telegram notification

## Development

```bash
# Clone
git clone https://github.com/leolionart/plex-sub-downloader.git
cd plex-sub-downloader

# Install dependencies
pip install poetry
poetry install

# Chạy dev server
poetry run uvicorn app.main:app --reload --port 8000

# Tests
poetry run pytest
poetry run pytest --cov=app

# Lint
poetry run ruff check app/
poetry run mypy app/
```

### Build Docker image locally

```bash
docker build -t plex-subtitle-service .
```

> Trong `docker-compose.yml`, uncomment phần `build:` và comment `image:` để dùng bản build local.

## Troubleshooting

**Webhook không hoạt động:**
- Kiểm tra service có chạy: `curl http://<ip>:8000/health`
- Kiểm tra firewall không block port 8000
- Xem log tại `/logs` hoặc `docker compose logs -f subtitle-service`
- Nếu dùng Tautulli: kiểm tra JSON payload đúng format

**Không tìm được subtitle:**
- Media cần có IMDb/TMDb ID — refresh metadata trong Plex nếu thiếu
- Subsource có thể chưa có sub cho media này
- Thử hạ `min_quality_threshold` trong Settings

**Upload lỗi:**
- Plex token cần quyền write
- Set `LOG_LEVEL=DEBUG` để xem chi tiết

## Credits

Rewrite từ [mjvotaw/plex-sub-downloader](https://github.com/mjvotaw/plex-sub-downloader).

Sử dụng [FastAPI](https://fastapi.tiangolo.com/), [python-plexapi](https://github.com/pkkid/python-plexapi), [Subsource](https://subsource.net/), [OpenAI API](https://platform.openai.com/).

## License

MIT
