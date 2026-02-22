# Features

## 1. Auto-Download (Webhook)

Tự động tải subtitle khi Plex/Tautulli gửi webhook.

**Events được xử lý:**
- `library.new` — media mới thêm vào library
- `library.on.deck` — media on deck
- `media.play` — user bắt đầu xem (nếu bật `auto_download_on_play`)

**Luồng xử lý:**
1. Nhận webhook → queue background task
2. Với `library.new`: chờ `new_media_delay_seconds` (mặc định 30s) để Plex hoàn tất index metadata
3. Kiểm tra điều kiện skip (xem [Configuration](configuration.md))
4. Tìm subtitle trên Subsource → download → upload lên Plex
5. Nếu không tìm được: fallback AI translate (nếu bật)

**Deduplication:** Plex thường gửi nhiều webhook cho cùng media. Service chỉ xử lý một lần, cooldown 5 phút.

---

## 2. Manual Sync UI (`/sync`)

Giao diện web để sync timing và dịch subtitle thủ công cho bất kỳ media nào đang phát.

### Preview
Bấm Preview (hoặc chọn video đang play) → service kiểm tra:
- Sub nào đang có trên Plex (tất cả languages)
- Sub nào có thể tải từ Subsource
- Trạng thái: Ready to Sync / Translate Only / Đã có Vietsub...

**Tối ưu:** Nếu target language sub đã có dạng text-based trên Plex → không gọi Subsource, trả về ngay.

### Status Tags

| Tag | Ý nghĩa |
|-----|---------|
| `Ready to Sync` | Có source sub + target sub, sync khả dụng |
| `Re-sync Timing` | Target đã có trên Plex, có thể re-sync |
| `Translate (KO → VI)` | Có source sub (KO), có thể dịch sang target |
| `Translate Only` | Có EN source, không có target, chỉ translate được |
| `Thiếu source sub` | Target đã có nhưng không tìm được source để sync |
| `Không tìm thấy sub nguồn` | Không có source lẫn target |

### Sync Timing
- Dùng source sub (bất kỳ ngôn ngữ nào) làm timing reference
- Điều chỉnh timing của target sub theo reference
- Upload kết quả lên Plex

**Source sub detection:**
1. Lấy tất cả subtitle langs trên Plex, bỏ target lang
2. Thử download text-based sub (EN ưu tiên, còn lại alphabet)
3. Nếu không có trên Plex → search Subsource

### AI Translate
- Dịch source sub (bất kỳ ngôn ngữ) sang target lang
- `from_lang` được truyền từ preview result
- Prompt AI: `"translator from {source_lang} to {target_lang}"` — hoạt động với mọi cặp ngôn ngữ

---

## 3. AI Translation

Khi không tìm được target lang sub, service dịch từ source lang.

**Nguồn source sub (theo thứ tự ưu tiên):**
1. Sub đã có trên Plex (bất kỳ lang nào ≠ target)
2. Subsource: EN → KO → JA → ZH → FR → ES → DE → ...

**Translation client:**
- Batch processing (10 entries/call) để giảm API calls
- Numbered format `[1]...[2]...` cho parse reliable
- Retry với exponential backoff (3 lần)

**Chế độ:**
- `auto`: dịch ngay, upload lên Plex
- `requires_approval`: gửi Telegram notification, chờ user approve qua Web UI

---

## 4. Translation Approval (`/translation`)

Khi `translation_requires_approval = true`:

1. Service tìm source sub → tính toán → gửi Telegram thông báo
2. User mở Web UI `/translation` → xem danh sách pending
3. Bấm **Estimate Cost** → xem số dòng, token ước tính, chi phí dự kiến
4. Bấm **Approve** → service thực hiện dịch + upload
5. Bấm **Reject** → xóa khỏi queue

**API endpoints:**
- `GET /api/translation/pending` — danh sách chờ duyệt
- `POST /api/translation/estimate` — ước tính chi phí
- `POST /api/translation/approve` — duyệt + thực hiện
- `POST /api/translation/reject` — từ chối

---

## 5. Multi-Language Support

Service hoạt động với **bất kỳ ngôn ngữ target nào** được Subsource hỗ trợ.

Đặt `default_language` trong Settings (hoặc `languages` cho multi-language download).

Supported languages: Vietnamese, English, Korean, Japanese, Chinese, French, Spanish, German, Portuguese, Russian, Italian, Arabic, và nhiều ngôn ngữ khác.

Xem đầy đủ tại `app/clients/subsource_client.py` → `LANGUAGE_MAP`.

---

## 6. Quality Management

Subtitle được phân loại theo chất lượng:
- `retail` — từ bản phát hành chính thức
- `translated` — đã được người dùng dịch
- `ai` — dịch bằng AI

Cấu hình `min_quality_threshold` để bỏ qua sub chất lượng thấp.
Cấu hình `replace_only_if_better_quality` để chỉ thay sub khi tìm được bản tốt hơn.

---

## 7. Telegram Notifications

Thông báo qua Telegram cho:
- Sub tải thành công
- Sync hoàn thành (anchors found, avg offset)
- Translation approval request
- Lỗi xử lý

Cấu hình `telegram_bot_token` và `telegram_chat_id` trong Setup.

---

## 8. Real-time Log Viewer (`/logs`)

Xem log real-time của service qua Web UI, hỗ trợ filter theo level (info/warning/error/debug).
