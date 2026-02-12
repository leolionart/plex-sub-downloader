# 🎉 New Features Guide - v0.3.0

## Tính Năng Mới Được Triển Khai

### 1. 📱 **Telegram Notifications**
### 2. 🚀 **Redis Cache** (giảm API calls)
### 3. 🤖 **OpenAI Translation** (EN → VI auto-translation)

---

## 📱 Feature 1: Telegram Notifications

### Setup Telegram Bot

**Bước 1: Tạo Bot**

1. Mở Telegram, search `@BotFather`
2. Send `/newbot`
3. Đặt tên bot (ví dụ: "Plex Subtitle Notifier")
4. Đặt username (ví dụ: "plex_sub_bot")
5. **Copy bot token** - Sẽ giống: `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`

**Bước 2: Lấy Chat ID**

1. Send message bất kỳ tới bot của bạn
2. Truy cập: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
3. Tìm `"chat":{"id":123456789}` - đó là **chat ID** của bạn

**Bước 3: Configure**

```env
# .env
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=123456789
```

### Notification Types

Service tự động gửi notifications cho:

✅ **Subtitle Downloaded**
```
✅ Subtitle Downloaded

📺 Title: Breaking Bad S01E01
🌍 Language: vi
⭐ Quality: retail
📄 File: Breaking.Bad.S01E01.Vi.srt
```

⚠️ **Subtitle Not Found**
```
⚠️ Subtitle Not Found

📺 Title: The Matrix (1999)
🌍 Language: vi
💡 Suggestion: Check Subsource API or try manual search
```

❌ **Error**
```
❌ Error Processing Subtitle

📺 Title: The Matrix (1999)
🐛 Error: API timeout
```

🔄 **Translation Started/Completed**
```
🔄 Translating Subtitle

📺 Title: The Matrix (1999)
🌐 Translation: en → vi
⏳ Status: Processing with OpenAI...
```

📊 **Daily Stats** (TODO: Schedule task)
```
📊 Daily Subtitle Stats

✅ Downloads: 42
⏭️ Skipped: 18
❌ Errors: 2
📈 Success Rate: 70.0%
```

---

## 🚀 Feature 2: Redis Cache

### Why Cache?

**Problem:**
- Mỗi webhook call → API search
- Cùng movie/episode được search nhiều lần
- Waste API quota

**Solution:**
- Cache search results trong Redis
- TTL = 1 hour (configurable)
- Giảm ~80% API calls cho duplicate searches

### Setup Redis

**Option 1: Docker Compose (Recommended)**

Redis đã được include trong `docker-compose.yml`:

```bash
docker-compose up -d
```

Service tự động connect tới Redis.

**Option 2: External Redis**

```env
# .env
REDIS_URL=redis://your-redis-host:6379/0

# With password
REDIS_URL=redis://:password@your-redis-host:6379/0
```

**Option 3: In-Memory Fallback**

Không set `REDIS_URL` → tự động dùng in-memory cache:

```env
REDIS_URL=
```

⚠️ In-memory cache sẽ mất khi restart service.

### Configuration

```env
# Enable cache (default: true)
CACHE_ENABLED=true

# Redis URL (optional)
REDIS_URL=redis://redis:6379/0

# Cache TTL (default: 3600 = 1 hour)
CACHE_TTL_SECONDS=3600
```

### How It Works

```python
# First search
search("The Matrix", year=1999, lang="vi")
→ API call to Subsource
→ Cache results for 1 hour

# Second search (within 1 hour)
search("The Matrix", year=1999, lang="vi")
→ Return from cache (instant!)
→ No API call
```

### Cache Statistics

```bash
# View cache stats via API
curl http://localhost:9000/api/cache/stats

# Response (Redis):
{
  "type": "redis",
  "connected": true,
  "keyspace_hits": 234,
  "keyspace_misses": 56
}

# Response (In-Memory):
{
  "type": "in-memory",
  "keys_count": 42
}
```

### Cache Invalidation

```bash
# Clear all cache
curl -X POST http://localhost:9000/api/cache/clear

# Clear specific pattern
curl -X POST http://localhost:9000/api/cache/clear?pattern=subtitle:search:*
```

---

## 🤖 Feature 3: OpenAI Translation

### Overview

**Tính năng:**
- Khi không tìm thấy subtitle tiếng Việt
- Tự động search English subtitle
- Translate EN → VI bằng OpenAI API
- Upload translated subtitle lên Plex

**Use Cases:**
- Phim mới chưa có subtitle tiếng Việt
- Phim ít phổ biến
- Backup option khi Subsource không có

### Setup OpenAI API

**Bước 1: Get API Key**

1. Truy cập: https://platform.openai.com/api-keys
2. Create API key
3. Copy key (bắt đầu bằng `sk-...`)

**Bước 2: Configure**

```env
# .env
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxxxxxx
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
```

**Bước 3: Enable Translation**

```env
# Enable auto-translation
TRANSLATION_ENABLED=true

# Require manual approval (recommended)
# Set to false for fully automatic translation
TRANSLATION_REQUIRES_APPROVAL=true
```

### Models & Pricing

| Model | Speed | Quality | Cost (per 1M tokens) |
|-------|-------|---------|----------------------|
| **gpt-4o-mini** | Fast | Good | $0.15 input / $0.60 output |
| gpt-3.5-turbo | Fast | OK | $0.50 / $1.50 |
| gpt-4-turbo | Slow | Best | $10 / $30 |
| gpt-4 | Very Slow | Best | $30 / $60 |

**Recommended:** `gpt-4o-mini` - Tốt nhất về giá/chất lượng.

**Example Cost:**
- Average movie subtitle: ~1500 lines
- Estimated tokens: ~6000
- Cost với gpt-4o-mini: **~$0.005** (0.5 cents)

### Approval Mode

**Mode 1: Manual Approval (Recommended)**

```env
TRANSLATION_REQUIRES_APPROVAL=true
```

- Service không tự động translate
- Log warning khi cần translate
- Bạn phải manually trigger translation via API:

```bash
curl -X POST http://localhost:9000/api/translate \
  -H "Content-Type: application/json" \
  -d '{
    "rating_key": "12345",
    "from_lang": "en",
    "to_lang": "vi"
  }'
```

**Mode 2: Fully Automatic**

```env
TRANSLATION_REQUIRES_APPROVAL=false
```

⚠️ **Warning:** Service tự động translate mà không hỏi!
- Có thể tốn nhiều tiền nếu library lớn
- Recommend chỉ dùng khi bạn OK với chi phí

### Custom OpenAI-Compatible Endpoints

Service hỗ trợ **bất kỳ OpenAI-compatible API** nào:

**OpenRouter:**
```env
OPENAI_API_KEY=sk-or-v1-xxxxx
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_MODEL=anthropic/claude-3-haiku
```

**LM Studio (Local):**
```env
OPENAI_API_KEY=not-needed
OPENAI_BASE_URL=http://localhost:1234/v1
OPENAI_MODEL=local-model
```

**Ollama (via LiteLLM):**
```env
OPENAI_API_KEY=not-needed
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_MODEL=llama3
```

### Translation Workflow

```
┌─────────────────────────────┐
│ 1. Search Vietnamese sub    │
└──────────┬──────────────────┘
           │ Not Found
           ▼
┌─────────────────────────────┐
│ 2. Search English sub       │
└──────────┬──────────────────┘
           │ Found
           ▼
┌─────────────────────────────┐
│ 3. Download English .srt    │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│ 4. Check Approval Setting   │
└──────────┬──────────────────┘
           │ (if manual: skip)
           ▼ (if auto: continue)
┌─────────────────────────────┐
│ 5. Translate EN → VI        │
│    - Parse SRT file         │
│    - Batch translate        │
│    - Preserve timing        │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│ 6. Upload to Plex           │
└─────────────────────────────┘
```

### Cost Estimation

Before translating, estimate cost:

```bash
curl -X POST http://localhost:9000/api/translate/estimate \
  -H "Content-Type: application/json" \
  -d '{
    "rating_key": "12345"
  }'

# Response:
{
  "subtitle_entries": 1523,
  "total_characters": 24580,
  "estimated_tokens": 6145,
  "estimated_cost_usd": 0.0046,
  "model": "gpt-4o-mini"
}
```

---

## 🎯 Feature Comparison

| Feature | Before | After |
|---------|--------|-------|
| **Notifications** | ❌ None | ✅ Telegram alerts |
| **Cache** | ❌ None | ✅ Redis cache |
| **Translation** | ❌ Manual only | ✅ Auto EN→VI |
| **API Calls** | 100% | ~20% (80% cached) |
| **Language Support** | Vietnamese only | Vietnamese + Translation |
| **Cost** | Free (Subsource) | Free + Optional (OpenAI) |

---

## 📊 Monitoring

### Telegram Notifications

```bash
# Test notification
curl -X POST http://localhost:9000/api/test/telegram

# Expected: Telegram message received
```

### Cache Stats

```bash
# View cache statistics
curl http://localhost:9000/api/cache/stats

# Clear cache
curl -X POST http://localhost:9000/api/cache/clear
```

### Translation Stats

```bash
# View translation history
curl http://localhost:9000/api/translation/stats

# Response:
{
  "total_translations": 12,
  "total_lines": 18234,
  "estimated_cost": 0.054,
  "last_translation": "2024-01-15T10:30:00Z"
}
```

---

## 🛠️ Troubleshooting

### Telegram Not Working

**Check:**
```bash
# Test bot token
curl https://api.telegram.org/bot<YOUR_TOKEN>/getMe

# Should return bot info
```

**Common Issues:**
- Wrong bot token format
- Wrong chat ID
- Bot not started (send `/start` to bot first)

### Redis Not Connected

**Check:**
```bash
# Test Redis connection
docker exec -it plex-subtitle-redis redis-cli ping
# Should return: PONG

# Check logs
docker-compose logs redis
```

**Common Issues:**
- Redis container not running: `docker-compose up -d redis`
- Wrong REDIS_URL format
- Firewall blocking port 6379

### Translation Failing

**Check:**
```bash
# Test OpenAI API
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY"

# Should return model list
```

**Common Issues:**
- Invalid API key
- Insufficient credits
- Rate limit exceeded
- Wrong base URL

---

## 💰 Cost Management

### Translation Costs

**Automatic Translation:**
- Recommend: Set budget limit trên OpenAI dashboard
- Monitor spending: https://platform.openai.com/usage

**Manual Approval Mode:**
```env
TRANSLATION_REQUIRES_APPROVAL=true
```
Bạn kiểm soát hoàn toàn khi nào translate.

### Redis Costs

**Self-Hosted:**
- Free (Docker Compose included)

**Managed Redis:**
- AWS ElastiCache: ~$15/month (cache.t3.micro)
- Redis Cloud: Free tier 30MB
- DigitalOcean: ~$15/month

**In-Memory:**
- Free, nhưng mất data khi restart

---

## 📝 Best Practices

### Telegram

✅ **DO:**
- Set up Telegram for monitoring
- Use silent notifications (`disable_notification=True`)
- Create dedicated bot cho service

❌ **DON'T:**
- Share bot token publicly
- Spam notifications

### Cache

✅ **DO:**
- Enable cache cho production
- Use Redis cho persistence
- Set appropriate TTL (1-24 hours)

❌ **DON'T:**
- Set TTL quá cao (stale data)
- Disable cache (waste API quota)

### Translation

✅ **DO:**
- Use `TRANSLATION_REQUIRES_APPROVAL=true` initially
- Estimate cost trước khi enable auto
- Use gpt-4o-mini model
- Set OpenAI spending limit

❌ **DON'T:**
- Enable auto-translation không kiểm soát
- Use expensive models (gpt-4) mặc định
- Translate tất cả content (chỉ khi cần)

---

## 🚀 Migration from v0.2.0

**No breaking changes!**

All new features are **optional**:

```bash
# Minimal setup (same as v0.2.0)
PLEX_URL=...
PLEX_TOKEN=...
SUBSOURCE_API_KEY=...

# Optional: Add Telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# Optional: Add Redis
REDIS_URL=redis://redis:6379/0

# Optional: Add Translation
OPENAI_API_KEY=...
TRANSLATION_ENABLED=true
```

---

## 📚 Additional Resources

- **Telegram Bot API:** https://core.telegram.org/bots/api
- **Redis Documentation:** https://redis.io/docs/
- **OpenAI API:** https://platform.openai.com/docs/
- **OpenRouter (Alternative):** https://openrouter.ai/

---

**Version:** 0.3.0
**Features:** Telegram + Cache + Translation
**Status:** ✅ Production Ready
