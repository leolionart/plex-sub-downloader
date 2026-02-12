# 🎯 Translation Approval System Guide

## Tổng Quan

Khi `TRANSLATION_REQUIRES_APPROVAL=true`, service sẽ **không tự động translate**. Thay vào đó:

1. ✅ Tìm English subtitle
2. ✅ Add vào pending queue
3. ✅ Gửi Telegram notification
4. ⏸️ **Chờ user approve qua Web UI**
5. ✅ User approve → Translate & upload

---

## 🔔 Nơi Nhận Notification

### **1. Telegram (Realtime)**

Khi cần approve, bạn nhận message:

```
🔔 Translation Approval Required

📺 Title: The Matrix (1999)
🌐 Translation: en → vi
📄 Subtitle: The.Matrix.1999.BluRay.En.srt

⚠️ Action Required:
Open Web UI to approve/reject:
http://your-server:9000/translation

💰 Estimate cost first:
curl -X POST http://your-server:9000/api/translation/estimate \
  -d '{"rating_key": "12345"}'
```

### **2. Web UI (Dashboard)**

Mở browser: **`http://your-server:9000/translation`**

---

## 🖥️ Web UI - Translation Approval Page

### **URL:**
```
http://localhost:9000/translation
```

### **Features:**

#### **Pending List**
Shows all translations waiting for approval:

```
┌─────────────────────────────────────────────────┐
│ 🔄 Translation Approval                         │
├─────────────────────────────────────────────────┤
│                                                 │
│ ┌─────────────────────────────────────────┐   │
│ │ The Matrix (1999)           [Pending]   │   │
│ │ en → vi | Added: 10:30 AM               │   │
│ │                                           │   │
│ │ Click "Estimate Cost" to see cost        │   │
│ │                                           │   │
│ │ [💰 Estimate] [✓ Approve] [✗ Reject]    │   │
│ └─────────────────────────────────────────┘   │
│                                                 │
│ ┌─────────────────────────────────────────┐   │
│ │ Breaking Bad S01E01        [Pending]    │   │
│ │ en → vi | Added: 10:35 AM               │   │
│ │                                           │   │
│ │ Click "Estimate Cost" to see cost        │   │
│ │                                           │   │
│ │ [💰 Estimate] [✓ Approve] [✗ Reject]    │   │
│ └─────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

---

## 📋 Workflow Chi Tiết

### **Step 1: Service Tìm Thấy Cần Translation**

```python
# Không tìm thấy Vietnamese subtitle
→ Search English subtitle
→ Found: The.Matrix.1999.BluRay.En.srt
→ Check TRANSLATION_REQUIRES_APPROVAL
→ TRUE → Add to pending queue
→ Send Telegram notification
```

**Log:**
```
INFO - No Vietnamese subtitle found
INFO - Translation fallback: Searching English subtitle
INFO - Found English subtitle: The.Matrix.1999.BluRay.En.srt
WARNING - Translation requires approval. Added to pending queue.
```

---

### **Step 2: User Nhận Telegram Notification**

Telegram message với:
- 📺 Title
- 🌐 Language pair (en → vi)
- 📄 Subtitle name
- 🔗 Link tới Web UI
- 💰 Command để estimate cost

---

### **Step 3: User Mở Web UI**

**URL:** `http://your-server:9000/translation`

See list of pending translations.

---

### **Step 4: Estimate Cost (Optional but Recommended)**

Click **"💰 Estimate Cost"** button

Service sẽ:
1. Download English subtitle temporarily
2. Count lines, characters, tokens
3. Calculate estimated cost

**Example Result:**
```
┌────────────────────────────────────┐
│ Subtitle Lines: 1,523              │
│ Characters: 24,580                 │
│ Est. Tokens: 6,145                 │
│ Model: gpt-4o-mini                 │
├────────────────────────────────────┤
│ Estimated Cost: $0.0046 USD        │
└────────────────────────────────────┘
```

**"Approve" button is enabled** after estimate.

---

### **Step 5: Approve or Reject**

#### **Option A: Approve ✓**

Click **"✓ Approve & Translate"**

Service sẽ:
1. Download English subtitle
2. Translate en → vi (batched)
3. Upload Vietnamese subtitle to Plex
4. Send Telegram success notification
5. Remove from pending queue

**Progress:**
```
[Processing...] ⏳
→ Downloading English subtitle...
→ Translating (batch 1/15)...
→ Translating (batch 2/15)...
→ ...
→ Uploading to Plex...
→ ✓ Done!
```

**Telegram:**
```
✅ Translation Completed

📺 Title: The Matrix (1999)
🌍 Language: vi
📝 Lines: 1,523
```

#### **Option B: Reject ✗**

Click **"✗ Reject"**

Service sẽ:
1. Remove from pending queue
2. No translation happens
3. No cost incurred

---

## 🔗 API Endpoints

### **1. Get Pending Translations**

```bash
GET /api/translation/pending

# Response:
{
  "count": 2,
  "items": [
    {
      "rating_key": "12345",
      "title": "The Matrix (1999)",
      "from_lang": "en",
      "to_lang": "vi",
      "added_at": "2024-01-15T10:30:00Z"
    }
  ]
}
```

### **2. Estimate Cost**

```bash
POST /api/translation/estimate
Content-Type: application/json

{
  "rating_key": "12345",
  "from_lang": "en",
  "to_lang": "vi"
}

# Response:
{
  "rating_key": "12345",
  "title": "The Matrix (1999)",
  "subtitle_entries": 1523,
  "total_characters": 24580,
  "estimated_tokens": 6145,
  "estimated_cost_usd": 0.0046,
  "model": "gpt-4o-mini"
}
```

### **3. Approve Translation**

```bash
POST /api/translation/approve
Content-Type: application/json

{
  "rating_key": "12345",
  "from_lang": "en",
  "to_lang": "vi"
}

# Response:
{
  "status": "success",
  "message": "Translated subtitle uploaded (1523 lines)",
  "details": {
    "lines_translated": 1523,
    "model": "gpt-4o-mini"
  }
}
```

### **4. Reject Translation**

```bash
POST /api/translation/reject
Content-Type: application/json

{
  "rating_key": "12345"
}

# Response:
{
  "status": "rejected",
  "message": "Translation request rejected"
}
```

### **5. Translation Stats**

```bash
GET /api/translation/stats

# Response:
{
  "total_translations": 12,
  "total_lines": 18234,
  "total_cost": 0.054,
  "pending_count": 2,
  "average_cost": 0.0045
}
```

---

## 🎯 Use Cases

### **Use Case 1: Manual Review Everything**

```env
TRANSLATION_ENABLED=true
TRANSLATION_REQUIRES_APPROVAL=true
```

**Workflow:**
1. Service tìm không có Vietnamese sub
2. Add to pending queue
3. Telegram alert
4. User review & approve qua Web UI
5. Translation executes

**Best for:**
- Cost-conscious users
- Selective translation
- Quality control

---

### **Use Case 2: Fully Automatic**

```env
TRANSLATION_ENABLED=true
TRANSLATION_REQUIRES_APPROVAL=false
```

**Workflow:**
1. Service tìm không có Vietnamese sub
2. Tự động translate luôn
3. Upload subtitle
4. Telegram notification (informational)

**Best for:**
- Trusted content
- Budget set on OpenAI
- Maximum convenience

⚠️ **Warning:** Monitor OpenAI costs!

---

### **Use Case 3: Disabled**

```env
TRANSLATION_ENABLED=false
```

No translation happens. Original behavior.

---

## 💡 Best Practices

### **1. Always Estimate First**

Before approving, click **"Estimate Cost"** để biết:
- Số dòng subtitle
- Estimated cost
- Model being used

### **2. Set OpenAI Budget Limit**

OpenAI Dashboard → Usage limits → Set monthly budget

### **3. Review Quality**

After first few translations:
- Check subtitle quality trong Plex
- Adjust model nếu cần (gpt-4 cho better quality)

### **4. Batch Approve**

Nếu có nhiều pending:
- Estimate tất cả
- Approve những video quan trọng
- Reject những không cần

### **5. Monitor Stats**

```bash
curl http://localhost:9000/api/translation/stats
```

Track:
- Total cost
- Average cost per translation
- Pending count

---

## 🔧 Troubleshooting

### **Issue 1: Không thấy pending translations**

**Check:**
```bash
# Check if translation enabled
echo $TRANSLATION_ENABLED
# Should be: true

# Check if requires approval
echo $TRANSLATION_REQUIRES_APPROVAL
# Should be: true

# Check pending queue
curl http://localhost:9000/api/translation/pending
```

### **Issue 2: Estimate button không work**

**Check logs:**
```bash
docker-compose logs -f subtitle-service
```

**Common causes:**
- No English subtitle found
- Subsource API down
- Network issues

### **Issue 3: Approve button grayed out**

Click **"Estimate Cost"** first!

Approve button only enables after estimate completes.

### **Issue 4: Translation fails after approve**

**Check:**
- OpenAI API key valid?
- Sufficient credits?
- Network connectivity?

**Logs:**
```bash
docker-compose logs -f subtitle-service | grep -i translation
```

---

## 📊 Summary

| Mode | Approval Required? | Where to Approve? | Auto-Execute? |
|------|-------------------|-------------------|---------------|
| **Manual** | YES | Web UI + API | NO |
| **Auto** | NO | N/A | YES |
| **Disabled** | N/A | N/A | NO |

**Recommended:** Manual mode với Web UI approval.

---

## 🎓 Example Session

```bash
# 1. User adds movie to Plex
# Service detects no Vietnamese sub

# 2. Telegram notification
🔔 Translation Approval Required
📺 The Matrix (1999)

# 3. User opens Web UI
http://localhost:9000/translation

# 4. User clicks "Estimate Cost"
Result: $0.0046 USD (1523 lines)

# 5. User clicks "Approve"
[Processing...] ⏳

# 6. Telegram notification
✅ Translation Completed
📺 The Matrix (1999)
📝 Lines: 1,523

# 7. Subtitle available in Plex!
```

---

**Web UI URL:** `http://your-server:9000/translation`

**Happy translating!** 🎉
