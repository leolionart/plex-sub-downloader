# Release Notes


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

