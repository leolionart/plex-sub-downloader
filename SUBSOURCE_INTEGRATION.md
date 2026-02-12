# Subsource API Integration Guide

## 📚 API Documentation

Official Subsource API: https://subsource.net/api-docs

## 🔑 Authentication

Subsource API sử dụng Bearer token authentication:

```bash
Authorization: Bearer YOUR_API_KEY
```

## 📡 Endpoints

### 1. Search Subtitles

**TODO: Cập nhật endpoint chính xác từ Subsource docs**

```
GET /subtitles/search
```

**Query Parameters:**

```json
{
  "query": "Movie title",          // Text search
  "imdb_id": "tt0133093",          // IMDb ID (preferred)
  "tmdb_id": "603",                // TMDb ID (alternative)
  "year": 1999,                    // Release year
  "season": 1,                     // For TV shows
  "episode": 1,                    // For TV shows
  "language": "vi"                 // Language code
}
```

**Response Format (EXPECTED):**

```json
{
  "results": [
    {
      "id": "12345",
      "name": "Movie.2024.WEB-DL.Vi.srt",
      "language": "vi",
      "download_url": "https://subsource.net/api/download/12345",
      "release_info": "WEB-DL",
      "uploader": "UserName",
      "rating": 8.5,
      "downloads": 1234,
      "created_at": "2024-01-01T00:00:00Z"
    }
  ],
  "total": 1,
  "page": 1
}
```

### 2. Download Subtitle

**TODO: Verify download endpoint**

```
GET /download/{subtitle_id}
```

**Response:**
- Direct .srt file download, hoặc
- ZIP archive chứa .srt file

## 🛠️ Implementation Checklist

### Phase 1: API Research ✅ TODO

- [ ] Đăng ký Subsource API key tại https://subsource.net
- [ ] Test API endpoints với curl/Postman
- [ ] Xác định chính xác response structure
- [ ] Check rate limits và pagination

**Example curl test:**

```bash
# Search test
curl -X GET "https://api.subsource.net/api/subtitles/search?query=Matrix&language=vi" \
  -H "Authorization: Bearer YOUR_API_KEY"

# Download test
curl -X GET "https://api.subsource.net/api/download/12345" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -o subtitle.srt
```

### Phase 2: Update Code ✅ TODO

File cần update: `app/clients/subsource_client.py`

**Update `_search_by_id()` method:**

```python
async def _search_by_id(self, params: SubtitleSearchParams) -> list[SubtitleResult]:
    """Update với actual Subsource API endpoint."""
    # TODO: Replace placeholder endpoint
    endpoint = f"{self.base_url}/subtitles/search"  # Verify này

    # TODO: Verify parameter names
    search_params = {
        "imdb_id": params.imdb_id,  # Hoặc "imdbId"?
        "language": params.language,
    }

    response = await self._client.get(endpoint, params=search_params)
    # ...
```

**Update `_parse_search_results()` method:**

```python
def _parse_search_results(self, data: dict) -> list[SubtitleResult]:
    """Parse actual API response."""
    # TODO: Update dựa trên real response structure
    results_key = "results"  # Hoặc "data"? "subtitles"?

    for item in data.get(results_key, []):
        result = SubtitleResult(
            id=item["id"],  # Verify field names
            name=item["name"],  # Hoặc "filename"?
            # ... rest of fields
        )
```

### Phase 3: Error Handling ✅ TODO

```python
# Rate limiting
if response.status_code == 429:
    retry_after = response.headers.get("Retry-After", 60)
    logger.warning(f"Rate limited, retry after {retry_after}s")
    # Implement backoff

# Pagination
if "next_page" in data:
    # Handle multiple pages
    pass
```

## 🔍 Field Mapping

| Our Model | Subsource API | Notes |
|-----------|---------------|-------|
| `id` | `id` / `subtitle_id` | Primary key |
| `name` | `filename` / `name` | Subtitle filename |
| `download_url` | `download_url` / `url` | Download link |
| `language` | `language` / `lang` | ISO 639-1 code |
| `release_info` | `release` / `release_info` | Release type |
| `rating` | `rating` / `score` | User rating |
| `downloads` | `download_count` / `downloads` | Download count |

**TODO: Fill in actual field names từ API docs**

## 🧪 Testing Strategy

### 1. Manual API Testing

```python
# scripts/test_subsource.py
import httpx
import asyncio

async def test_search():
    client = httpx.AsyncClient(
        headers={"Authorization": "Bearer YOUR_API_KEY"}
    )

    # Test với IMDb ID
    response = await client.get(
        "https://api.subsource.net/api/subtitles/search",
        params={"imdb_id": "tt0133093", "language": "vi"}
    )

    print(response.status_code)
    print(response.json())

asyncio.run(test_search())
```

### 2. Integration Testing

```bash
# Set API key
export SUBSOURCE_API_KEY=your_test_key

# Run test
poetry run pytest tests/test_subsource_client.py -v
```

## 📊 Quality Detection Heuristics

**Retail indicators:**
- Keywords: `BluRay`, `WEB-DL`, `Retail`, `Official`
- High rating (>8.0)
- Many downloads (>1000)

**Translated indicators:**
- Keywords: `Translated`, `Fan`, `Sub`
- Medium rating (5.0-8.0)

**AI indicators:**
- Keywords: `AI`, `Auto`, `Machine`, `Generated`
- Low rating (<5.0)
- Few downloads

## 🚨 Common Issues

### Issue 1: Search returns empty

**Debug:**
```python
logger.debug(f"Search params: {search_params}")
logger.debug(f"Response: {response.text}")
```

**Solutions:**
- Check IMDb ID format (với/không "tt" prefix?)
- Verify language code (ISO 639-1 vs ISO 639-2?)
- Try title search fallback

### Issue 2: Download fails

**Debug:**
```python
logger.debug(f"Download URL: {download_url}")
logger.debug(f"Content-Type: {response.headers.get('content-type')}")
```

**Solutions:**
- Check URL expiration
- Verify authentication header
- Handle redirects

### Issue 3: Rate limiting

**Solution:**
```python
from tenacity import retry, wait_random_exponential

@retry(wait=wait_random_exponential(multiplier=1, max=60))
async def search_with_retry(params):
    return await search_subtitles(params)
```

## 📈 Monitoring

**Metrics to track:**
- Search success rate
- Download success rate
- Average response time
- API errors by type

**Logging:**
```python
logger.info(
    "Subsource API call",
    extra={
        "endpoint": endpoint,
        "status_code": response.status_code,
        "duration_ms": duration,
    }
)
```

## 🔗 Resources

- Subsource Homepage: https://subsource.net
- API Documentation: https://subsource.net/api-docs
- Support/Contact: [TODO: Add contact info]

---

**Next Steps:**
1. ✅ Đăng ký API key
2. ✅ Test endpoints với real data
3. ✅ Update code với actual API structure
4. ✅ Run integration tests
5. ✅ Deploy và monitor
