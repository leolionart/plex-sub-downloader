# Release Notes

## v0.5.1 - 2026-11-24

- Bỏ qua tìm sub chủ động: đánh dấu phim/series (chọn nhiều được) để không tự tìm sub, không dịch AI, ẩn khỏi danh sách "Thiếu Vietsub"; tab "Bỏ qua" để xem và khôi phục. Bỏ qua series áp dụng cho mọi tập.
- Tuỳ chọn mới "Bỏ qua phim nói sẵn ngôn ngữ chính" (mặc định bật): media có audio cùng ngôn ngữ đích (phim Việt, bản lồng tiếng) không được tìm sub và được loại khỏi danh sách "Thiếu" ngay phía Plex.
- Sửa lỗi lưu Cài đặt reset các field không có trên form (danh sách bỏ qua dịch AI, ngữ cảnh dịch, kiểm tra kết quả bằng AI...) về mặc định.
- Sửa lỗi "Dịch AI hàng loạt" trong Thư viện không chạy được (biến `task_id` chưa được tạo).

## v0.5.0 - 2026-11-24

- Chỉnh lại toàn bộ UI cho mobile và desktop: không còn nút tràn ngang; toolbar Thư viện gom vào 2-3 hàng gọn với nút sắp xếp/lưới/danh sách/làm mới chỉ còn icon; bulk bar thành một hàng; dòng tập phim trong drawer series có nút icon và nằm trên một hàng; form Xử lý phụ đề gọn lại, thêm tuỳ chọn thu gọn.
- Sửa lỗi CSS fallback có `!important` đè lên class responsive của Tailwind (`sm:flex-row`, `sm:justify-end`...), nguyên nhân khiến nhiều layout desktop bị xếp dọc.
- Mobile chỉ còn thanh tab dưới làm điều hướng chính (bỏ hamburger trùng), hỗ trợ safe-area cho iPhone, tăng vùng chạm trên thiết bị cảm ứng; thống nhất nhãn điều hướng tiếng Việt.
- Bỏ Tailwind Play CDN: CSS được build sẵn (`npm run build:css`) và phục vụ từ `/static` có gzip; DaisyUI 4.12.23 được vendor. Giao diện giữ nguyên từng pixel (đã so ảnh chụp trước/sau).
- Cài đặt: một thanh "Lưu" chung, chỉ hiện khi có thay đổi (thay cho hai nút Save Settings và Save Configuration riêng); cảnh báo khi rời trang mà chưa lưu. Ô secret (token, API key) hiện rõ trạng thái "Đã lưu / Chưa có / Sẽ thay" thay vì trông như bị trống.
- Việt hoá phần lớn nhãn còn lẫn tiếng Anh (Cài đặt, kết quả kiểm tra, lịch sử đồng bộ, sidebar). Bảng lịch sử đồng bộ gọn lại trên mobile.
- Xoá template không dùng `translation.html`, `setup.html` (các route đã chuyển hướng từ trước).
- **Cải tổ toàn diện UI/UX Mobile & Flow thao tác**:
  - **Hệ thống Component dùng chung chuẩn (Plex Design System)**: Chuẩn hóa hệ thống Button (`.btn`, `.btn-primary`, `.btn-secondary`, `.btn-ghost`, `.btn-danger`, `.btn-warning`, `.btn-xs`, `.btn-sm`, `.btn-md`), loại bỏ hoàn toàn thuộc tính cố định chiều cao `height: 32px !important` gây lỗi chữ nhảy dòng bị đè và đè chữ lên nhau.
  - **Xử lý triệt để tình trạng Loading cản trở thao tác**: Bổ sung cơ chế chạy ngầm (Background Task Runner) với widget tiến trình nổi (`#floatingTaskWidget`). Người dùng có thể thu nhỏ hoặc đóng Modal tác vụ bất cứ lúc nào để tiếp tục duyệt phim, kiểm tra thư viện hoặc chuyển tab mà không bị khóa màn hình.
  - **Thanh điều hướng Mobile (Bottom Navigation & Top Bar)**: Thêm Mobile Bottom Nav cố định với 4 tab chính (Thư viện, Đồng bộ, Nhật ký, Cài đặt) và Mobile Top Bar gọn gàng, loại bỏ khoảng trống thừa và tối ưu hóa thao tác bằng một tay trên điện thoại.
  - **Giao diện lưới 2 cột và Modal dạng Bottom Sheet trên Mobile**: Thư viện hiển thị lưới 2 cột cân đối trên màn hình nhỏ thay vì 1 cột khổng lồ, các Modal tự động thích ứng thành Bottom Sheet trượt mượt mà với thanh cuộn độc lập. Danh sách tập phim trong Show được tái cấu trúc hiển thị theo hàng linh hoạt, chống tràn và đè nút.
- Không còn gửi Telegram "đang tìm sub" khi media đã có sub target. Với `replace_existing`, upgrade check chỉ chạy khi `library.new`, bỏ qua `media.play` và sub retail, và chạy im lặng trừ khi thay sub thành công.

## Unreleased - 2026-11-11 16:01 +07

Generated before push from commits:

- `610da7d` Match subtitle timing against AI audio samples

## Unreleased - 2026-11-11 14:32 +07

Generated before push from commits:

- `372b2b0` Fix audio sync CPU compatibility and memory usage

## Unreleased - 2026-11-11 12:41 +07

Generated before push from commits:

- `bcfd4e4` Include lockfile for Docker builds

## Unreleased - 2026-11-11

- Synchronize manual and automatic subtitles directly against accessible Plex audio without an AI key or a media mount.
- Reject unavailable, weak, ambiguous or inconsistent audio alignments before upload; preserve existing subtitles even when replacement is configured.
- Include ffmpeg in Docker and expose audio synchronization capability in preview responses.

## Unreleased - 2026-11-04 17:01 +07

Generated before push from commits:

- `b9a201f` fix: re-authenticate OpenSubtitles when cached token expires on download

## Unreleased - 2026-10-28 16:40 +07

Generated before push from commits:

- `66f9eba` fix: resolve webhook dedup lock, subsource quality_type parsing, and settings API

## Unreleased - 2026-10-22 10:29 +07

Generated before push from commits:

- `af87548` fix: stabilize library toolbar UI and legacy subtitle parsing

## Unreleased - 2026-10-22

- **Persistent Per-Media Translation Skip & Subtitle Usability Audit**:
  - Added persistent `translation_skip_rating_keys` to SubtitleSettings via ConfigStore with full backend enforcement on direct and batch translation.
  - Exposed GET/POST/DELETE `/api/library/translation-skip/{rating_key}` and integrated one-click skip toggles in movie detail and episode rows.
  - Refined subtitle usability: embedded-only and image-based subtitles are excluded from downloadable source/target status, preventing false "Có thể dịch AI" or missing-target warnings.
  - Replaced action/status emoji labels in workspace and library templates with FontAwesome icons, removed outdated flow notes block, and ensured mobile-safe non-wrapping action controls.
  - Auto-closed task progress popup upon completion while preserving fallback actions when needed.

## Unreleased - 2026-10-21

- **Subtitle Workspace and Library Navigation**:
  - Added direct navigation from a playing TV episode to its complete Series in Plex Library Manager.
  - Added URL state for library sections, movies, series, filters, search, and sorting so deep links restore the selected context.
  - Preserved cached library, show-tree, and movie-detail scans across modal close and browser history navigation; explicit Refresh and completed subtitle actions remain the refresh points.
  - Added regression coverage for episode parent-series session metadata and library URL-state behavior.


Generated before push from commits:

- `7ffe692` fix(resilience): defer Plex connect on boot, auto-heal uninitialized service, and return 503 on /health when uninitialized

## Unreleased - 2026-10-15 21:00 +07

- **Character Context & Pronoun Consistency Module (Dramatis Personae)**:
  - Extracted actor cast and character roles directly from Plex server metadata (`video.roles`), inheriting show-level roles for episodes.
  - Implemented `generate_character_matrix` to generate gender-aware character profiles and pairwise Vietnamese pronoun rules.
  - Integrated character rules and pronoun guidelines into translation batch system prompts for 100% pronoun consistency.
  - Added Show-level caching for TV series so all episodes share the same consistent character graph.
  - Added optional user pronoun override support via API and Web UI (`Ghi đè xưng hô`) in Workspace and Library drawers.
  - Fixed Select All for Movies by ensuring cached items are assigned during SessionCache hits.
  - Optimized Plex section search with native container_start and maxresults for 10x faster library loading.
  - Added multi-criteria sorting (Recently Added, Oldest Added, Release Date, Title A-Z/Z-A, Rating) on UI and API.
## Unreleased - 2026-10-15 20:46 +07

Generated before push from commits:

- `ad5ffa0` fix(library): resolve poster right-border vertical stretch, shrink checkbox, and fix EN badge contrast

## Unreleased - 2026-10-15 18:25 +07

- **Plex Library Manager UI**:
  - Added Grid View and List View toggle with persistent layout state in localStorage.
  - Implemented standard Plex 2:3 vertical poster aspect ratio (no distortion or awkward cropping).
  - Added subtitle filter bar (Thiếu Vietsub [default], Tất cả, Có thể dịch AI) with instant in-memory client filtering and native Plex subtitle filters.
  - Added Series Details Drawer with season/episode breakdown, subtitle status badges, and episode-level filtering.
  - Fixed bulk action execution on seasons and shows (`Tìm sub Mùa`, `Dịch tập thiếu`, `Tìm sub cả Show`, `Dịch tập thiếu cả Show`).
  - Fixed TV series thumbnail proxying (`/api/sync/thumb/{rating_key}`) by supporting Show media types in addition to Movies.
  - Fixed quick action hover button icon centering with precision vector SVGs.
  - Added multi-selection and floating Bulk Action Bar for batch subtitle search and AI translation across multiple movies and TV series.
  - Added automatic backend expansion of Show and Season rating keys to individual video/episode rating keys for batch processing.
  - Fixed Safari iOS/macOS horizontal overflow and style clipping with strict max-width constraints.
  - Added prominent high-contrast selection boxes on poster cards and dedicated Select All button on search bar.
  - Added native checkbox styling fallbacks ensuring 100% visibility even under strict Safari Content Blockers.
  - Added data-cfasync="false" to prevent Cloudflare Rocket Loader from blocking/deferring scripts on Safari iOS.
  - Embedded full native CSS utility fallbacks in base layout ensuring complete card, button, and badge styling even if external CDNs are blocked.
  - Added document.readyState guards ensuring reliable page initialization regardless of script deferral.
- **Docker Reliability & Self-Healing**:
  - Configured 15s timeout and connection retry for PlexServer client to prevent indefinite startup hangs.
  - Added background watchdog in `entrypoint.sh` to auto-terminate frozen container processes and trigger Docker restart policy.
  - Updated `docker-compose.yml` restart policy to `always` with extended 30s start period and `autoheal` label.
  - Fixed entrypoint permissions check to tolerate read-only/NFS/Synology mount filesystems.
## Unreleased - 2026-08-28 22:10 +07

Generated before push from commits:

- `6f1a6ab` refactor: make workspace single flow and unify settings

## Unreleased - 2026-08-28 21:58 +07

Generated before push from commits:

- `9536e38` feat: make workspace home and unify configuration

## Unreleased - 2026-08-28 21:48 +07

Generated before push from commits:

- `f03bbd4` feat: add workspace home and unified configuration route

## Unreleased - 2026-08-28 21:45 +07

Generated before push from commits:

- `42cb127` feat: unify subtitle actions under workspace

## Unreleased - 2026-08-28 20:52 +07

Generated before push from commits:

- `20e2b1e` style: keep manual search controls inline

## Unreleased - 2026-08-28 20:46 +07

Generated before push from commits:

- `47ad799` feat: add independent manual search controls to sync UI

## Unreleased - 2026-08-28 20:44 +07

Generated before push from commits:

- `f5f502a` feat: expose manual subtitle search in sync UI

## Unreleased - 2026-08-28 20:30 +07

Generated before push from commits:

- `07fcff2` feat: add manual subtitle search and telegram actions

## Unreleased - 2026-07-19 20:01 +07

Generated before push from commits:

- `089407f` fix: reduce health check log noise

## Unreleased - 2026-07-19 17:48 +07

Generated before push from commits:

- `e69193c` feat: add context-aware subtitle translation

## Unreleased - 2026-06-25 19:05 +07

Generated before push from commits:

- `622fc15` Fix duplicate language extension naming when uploading subtitle

## Unreleased - 2026-06-25 19:05 +07

Generated before push from commits:

- `fc97657` Implement existing subtitle quality detection and compare quality before replacing or translating

## Unreleased - 2026-06-25 17:58 +07

Generated before push from commits:

- `676ed33` Fix duplicate processing for Vietnamese subtitles using robust language normalization

## Unreleased - 2026-06-16 08:09 +07

Generated before push from commits:

- `25a095c` feat: allow explicit subtitle search overrides

## Unreleased - 2026-06-16 07:53 +07

Generated before push from commits:

- `9a63445` feat: validate ambiguous subtitle matches with ai

## Unreleased - 2026-06-16 07:37 +07

Generated before push from commits:

- `a8f2209` fix: handle subdl rate limits safely

## Unreleased - 2026-06-16 07:19 +07

Generated before push from commits:

- `7630aa3` fix: adjust opensubtitles user agent

## Unreleased - 2026-06-16 07:10 +07

Generated before push from commits:

- `defae87` fix: use opensubtitles canonical query order

## Unreleased - 2026-06-16 07:06 +07

Generated before push from commits:

- `114233c` fix: follow opensubtitles redirects

## Unreleased - 2026-06-16 06:51 +07

Generated before push from commits:

- `da19027` fix: expose subtitle provider diagnostics api

