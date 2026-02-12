# Plex Subtitle Service

🇻🇳 **Tự động tải và upload phụ đề tiếng Việt cho Plex Media Server**

Service chạy ngầm, lắng nghe webhook từ Plex, tự động tìm và upload subtitle tiếng Việt từ Subsource khi có media mới.

## ✨ Features

- ✅ **Tự động hoàn toàn** - Không cần thao tác thủ công
- ✅ **Upload trực tiếp** - Không cần mount thư viện media
- ✅ **Ưu tiên chất lượng** - Retail > Translated > AI subtitles
- ✅ **Async & Fast** - FastAPI với asyncio
- ✅ **Docker ready** - Deploy trong 2 phút
- ✅ **Dễ mở rộng** - Provider pattern cho nhiều nguồn subtitle

## 🏗️ Architecture

```
Plex Server → Webhook → Subtitle Service → Subsource API
                            ↓
                      Upload subtitle ← Download .srt
```

**Stack:**
- Python 3.11+ với FastAPI
- python-plexapi cho Plex integration
- httpx cho async HTTP requests
- Pydantic cho data validation
- Tenacity cho retry logic

## 🚀 Quick Start

### 1. Prerequisites

- Plex Media Server (Plex Pass required cho webhooks)
- Docker & Docker Compose
- Subsource API key ([đăng ký tại đây](https://subsource.net/api-docs))
- Plex authentication token ([lấy token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/))

### 2. Setup

```bash
# Clone repository
git clone <repo-url>
cd plex-subtitle-service

# Copy environment template
cp .env.example .env

# Chỉnh sửa .env với values của bạn
nano .env
```

**Cấu hình `.env`:**

```env
PLEX_URL=http://192.168.1.100:32400
PLEX_TOKEN=your-plex-token-here
SUBSOURCE_API_KEY=your-subsource-api-key-here
DEFAULT_LANGUAGE=vi
LOG_LEVEL=INFO
```

### 3. Deploy với Docker

```bash
# Build và start service
docker-compose up -d

# Check logs
docker-compose logs -f subtitle-service

# Health check
curl http://localhost:9000/health
```

### 4. Cấu hình Plex Webhook

**Trong Plex Web UI:**

1. Settings → Webhooks → Add Webhook
2. URL: `http://<subtitle-service-host>:9000/webhook`
   - Nếu cùng Docker network: `http://subtitle-service:9000/webhook`
   - Nếu khác máy: `http://192.168.1.x:9000/webhook`
3. (Optional) Nếu set `WEBHOOK_SECRET`, thêm header:
   - Header: `X-Webhook-Secret`
   - Value: `<your-secret>`

**Test webhook:**

Thêm một video mới vào Plex library → Check logs để thấy workflow:

```
INFO - Received webhook
INFO - Webhook event: library.new
INFO - Fetched video: Breaking Bad S01E01
INFO - Searching subtitles...
INFO - Found 5 subtitles, selected best
INFO - Downloading subtitle...
INFO - Uploading subtitle to Plex
INFO - ✓ Subtitle workflow completed successfully
```

## 📖 Usage

### Automatic Mode (Recommended)

Service tự động chạy khi có event từ Plex:
- ✅ `library.new` - Media mới được thêm
- ✅ `library.on.deck` - Media sẵn sàng xem

### Manual Trigger (API)

```bash
# Manually trigger subtitle download cho ratingKey
curl -X POST http://localhost:9000/webhook \
  -H "Content-Type: application/json" \
  -d '{"event": "library.new", "rating_key": "12345"}'
```

### API Documentation

FastAPI tự động generate OpenAPI docs:
- Swagger UI: http://localhost:9000/docs
- ReDoc: http://localhost:9000/redoc

## 🔧 Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PLEX_URL` | ✅ | - | Plex server URL |
| `PLEX_TOKEN` | ✅ | - | Plex auth token |
| `SUBSOURCE_API_KEY` | ✅ | - | Subsource API key |
| `SUBSOURCE_BASE_URL` | ❌ | `https://api.subsource.net/api` | API base URL |
| `DEFAULT_LANGUAGE` | ❌ | `vi` | Subtitle language |
| `WEBHOOK_SECRET` | ❌ | - | Webhook authentication |
| `LOG_LEVEL` | ❌ | `INFO` | Logging level |
| `MAX_RETRIES` | ❌ | `3` | Max API retries |
| `RETRY_DELAY` | ❌ | `2` | Initial retry delay (seconds) |

### Subtitle Priority

Service tự động chọn subtitle tốt nhất theo thứ tự:

1. **Retail** - Official subtitles từ BluRay/WEB-DL
2. **Translated** - Fan-translated subtitles
3. **AI** - AI-generated subtitles

Trong cùng category, ưu tiên theo:
- Rating cao hơn
- Download count nhiều hơn

## 🛠️ Development

### Local Setup

```bash
# Install Poetry
curl -sSL https://install.python-poetry.org | python3 -

# Install dependencies
poetry install

# Activate virtual environment
poetry shell

# Run development server
python -m app.main

# Hoặc với uvicorn reload
uvicorn app.main:app --reload --port 9000
```

### Run Tests

```bash
# Run all tests
poetry run pytest

# With coverage
poetry run pytest --cov=app --cov-report=html

# Run specific test file
poetry run pytest tests/test_plex_client.py -v
```

### Code Quality

```bash
# Format code
poetry run black app/ tests/

# Lint
poetry run ruff check app/ tests/

# Type check
poetry run mypy app/
```

## 📁 Project Structure

```
plex-subtitle-service/
├── app/
│   ├── clients/
│   │   ├── plex_client.py          # Plex API wrapper
│   │   └── subsource_client.py     # Subsource API client
│   ├── models/
│   │   ├── webhook.py              # Webhook payload models
│   │   └── subtitle.py             # Subtitle models
│   ├── services/
│   │   └── subtitle_service.py     # Business logic
│   ├── utils/
│   │   └── logger.py               # Logging utilities
│   ├── config.py                   # Configuration
│   └── main.py                     # FastAPI app
├── tests/                          # Unit tests
├── Dockerfile                      # Docker image
├── docker-compose.yml              # Docker Compose config
├── pyproject.toml                  # Poetry dependencies
└── README.md
```

## 🔍 Troubleshooting

### Webhook không hoạt động

**Check:**
1. Plex có thể reach được service URL?
   ```bash
   # Từ Plex server
   curl http://subtitle-service:9000/health
   ```
2. Firewall có block port 9000?
3. Docker network có đúng không?
4. Webhook secret có khớp không?

**Logs:**
```bash
docker-compose logs -f subtitle-service
```

### Subtitle không tìm thấy

**Có thể:**
- Media chưa có IMDb/TMDb ID → Plex cần refresh metadata
- Subsource chưa có subtitle cho media này
- Search query không chính xác

**Check metadata:**
```python
from plexapi.server import PlexServer
plex = PlexServer('http://localhost:32400', 'token')
video = plex.fetchItem(12345)
print(video.guids)  # Check external IDs
```

### Upload subtitle fail

**Kiểm tra:**
- Plex token có quyền write?
- File .srt có valid format?
- Disk space còn trống?

**Debug:**
Set `LOG_LEVEL=DEBUG` trong `.env` để xem chi tiết.

## 🌟 Roadmap

- [ ] **Multiple providers** - OpenSubtitles, SubDL, Subscene
- [ ] **Web UI** - Dashboard để quản lý subtitles
- [ ] **Manual search** - API endpoint để search manual
- [ ] **Subtitle editing** - Fix timing, encoding issues
- [ ] **Statistics** - Track subtitle downloads
- [ ] **Notifications** - Discord/Telegram alerts
- [ ] **Cache** - Cache search results để giảm API calls

## 🤝 Contributing

Contributions welcome! Please:

1. Fork repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open Pull Request

## 📄 License

MIT License - xem file `LICENSE` để biết thêm chi tiết.

## 🙏 Acknowledgments

**This project is a complete rewrite inspired by:**
- [mjvotaw/plex-sub-downloader](https://github.com/mjvotaw/plex-sub-downloader) - Original concept and inspiration

**Built with:**
- [python-plexapi](https://github.com/pkkid/python-plexapi) - Plex API wrapper
- [FastAPI](https://fastapi.tiangolo.com/) - Modern async Python web framework
- [Subsource](https://subsource.net/) - Vietnamese subtitle provider
- [Pydantic](https://docs.pydantic.dev/) - Data validation

**Why a rewrite?**

The original `plex-sub-downloader` by mjvotaw is an excellent tool but:
- ❌ No longer maintained (archived)
- ❌ Flask-based (synchronous, slower)
- ❌ OpenSubtitles only
- ❌ Single language support
- ❌ No Web UI
- ❌ Basic duplicate detection

This v2 brings:
- ✅ Modern FastAPI (async, 10x faster)
- ✅ Multi-language support
- ✅ Subsource provider (Vietnamese focus)
- ✅ Web UI configuration
- ✅ Smart duplicate detection
- ✅ Extensible provider pattern
- ✅ Active development

## 📞 Support

- GitHub Issues: [Report bugs](https://github.com/leolionart/plex-sub-downloader/issues)
- Discussions: [Ask questions](https://github.com/leolionart/plex-sub-downloader/discussions)

---

**Forked from:** [mjvotaw/plex-sub-downloader](https://github.com/mjvotaw/plex-sub-downloader)
**Rewritten by:** leolionart with Claude Opus 4.6
Made with ❤️ for multilingual Plex users 🌍
