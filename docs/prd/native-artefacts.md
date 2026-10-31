# PRD: Native artefact service (canonical)

| | |
|---|---|
| **Status** | **Proposed. Not implemented.** No code, endpoint, storage or UI described here exists yet. |
| Owner split | Bridge (this repo): registry, storage, authenticated delivery. App (`../app`): rendering, cache, Android Save/Share. |
| Android companion | `docs/prd/native-artefacts.md` in the Hermione Android repository |
| Date | 2026-10-31 |

## 1. Problem

Hermes agents produce files: images, PDFs, spreadsheets, documents and generated text files. The Android app can't currently receive or keep them.

## 2. Current state (inspected 2026-10-31)

- **Chat input:** `/v1/runs` accepts text and image parts only. Images go as image parts, text files are inlined into the message (`app/src/files.ts`), and other types are refused (`app/src/attach.ts`). Limits: 4 attachments, 512 KB per file, 48,000 characters of inlined text per message.
- **History:** the app loads `GET /api/sessions/{id}/messages?inline_images=false` (`app/src/hermes.ts`). Images in history are **counted, not rendered** (`imageCount` in `app/src/chat-model.ts`). Locally attached image URIs show only on the phone that sent them.
- **Reconnect:** `/v1/runs/{id}/events` with `Last-Event-ID` and de-duplication by `seq` (`app/src/run-stream.ts`). This was verified live server-side.
- **Share:** only text, through React Native `Share.share({ message })` (message menu, chat export). There is no file share, no save-to-device and no `expo-sharing` dependency. `expo-file-system` is installed.
- **Bridge:** JSON-only, stdlib HTTP server, `MAX_BODY` 64 KB, bearer auth, mobile scope. There are no artefact, file or binary endpoints. `GET /v1/status` returns `{bridge:{version}, ok, hermes, …}` and has no feature list.
- **Permissions:** `READ_/WRITE_EXTERNAL_STORAGE` are blocked in `app.json`, and `allowBackup` is false.
- **Unknown:** how Hermes itself marks a file as a deliverable output. This must be confirmed from hermes-agent source before ingestion is designed (see open question Q1).

## 3. Goals and non-goals

**Goals**

- G1. Deliver agent-produced files to the phone by opaque **artefact ID**, over the bridge's existing authenticated, tailnet-only origin.
- G2. **Durable** server-side storage, so artefacts survive app restarts, reconnects, bridge restarts and phone changes.
- G3. **Inline images** in the conversation, and **document cards** for everything else.
- G4. **Native Save** (Android Storage Access Framework) and **Share** (Android share sheet) for any artefact.
- G5. Correct placement after **history reload and stream reconnect**, without duplicates.
- G6. **Capability negotiation**, so old bridges and apps degrade cleanly with no broken UI.

**Non-goals for v1**

- Rendering active content inside the app (HTML, SVG, scripts or macros).
- Uploading arbitrary files from phone to agent. That is a separate increment, limited by Runs API input types.
- Public links, Funnel, or links that work without the bridge key.
- Editing artefacts, or syncing them back to the agent's workspace.
- Push delivery while the app is closed.

## 4. Concepts

- **Artefact:** an immutable file plus metadata, owned by one profile. Fields: `id`, `profile`, `session_id`, `run_id` (when known), `name`, `media_type`, `size`, `sha256`, `created_at`, `kind` (`image` | `document`), and optional `width` and `height` for images.
- **Artefact ID:** `art_` followed by at least 128 bits of CSPRNG randomness, base32 or hex. It's opaque and contains no path, profile or time. An unknown ID and an out-of-scope ID are indistinguishable to the client.
- **Ingestion:** how a file becomes an artefact. The mechanism depends on Q1. Whatever it is, the registry copies the bytes into bridge-owned storage. It never serves files by reference to agent-writable paths.

## 5. Proposed bridge contract (draft v1)

All routes require `Authorization: Bearer <bridge key>`. They are profile-scoped and pass `_profile_allowed` **before** any disk or upstream access. They are `gate=False` with respect to the dashboard compatibility probe, but report storage health in status.

| Method and path | Purpose |
|---|---|
| `GET /v1/status` | Adds `features.artefacts = {version: 1, max_bytes, inline_media_types[], document_media_types[]}`. Absent means unsupported. |
| `GET /v1/profiles/<p>/artefacts?session_id=<id>&after=<cursor>&limit=<n>` | Lists metadata, newest last, with cursor pagination and `limit` ≤ 100. `session_id` is required in v1. |
| `GET /v1/profiles/<p>/artefacts/<id>` | Returns metadata for one artefact. |
| `GET /v1/profiles/<p>/artefacts/<id>/content` | Returns the bytes. Headers: `Content-Type` (allowlisted), `Content-Length`, `ETag: "<sha256>"`, `Content-Disposition: attachment; filename*=…`, `X-Content-Type-Options: nosniff`, `Cache-Control: private, no-store`. `If-None-Match` returns 304. Single `Range` requests are optional. |
| `GET /v1/profiles/<p>/artefacts/<id>/preview` | Optional server-made image thumbnail (bounded size). Omit if it needs non-stdlib dependencies. Otherwise the app downsamples. |

Errors use the existing `{error:{code,message}}` envelope: `401 unauthorized`, `403 profile_denied`, `404 artefact_not_found` (also returned for IDs in other profiles), `413 artefact_too_large` at ingestion, `503 artefact_store_unavailable`.

The streaming content path is a new server capability, because the current server only writes JSON. It must stream from disk in chunks and keep the 30 s socket timeout semantics. It must not read whole files into memory.

## 6. Durable storage

- Root: a bridge-owned directory outside `~/.hermes` and outside any agent workspace, for example `~/.local/share/hermes-bridge/artefacts/`. Directories are mode 0700 and files 0600.
- Layout: content-addressed blobs `blobs/<sha256[:2]>/<sha256>`, plus a stdlib `sqlite3` index (`artefacts.db`) mapping `id → profile, session, run, name, media_type, size, sha256, created_at`.
- Writes: temp file, then fsync, then atomic rename, then index insert in a transaction. A crash leaves either no artefact or a complete one. Orphan blobs are garbage-collected.
- Limits: a per-file maximum (proposed 25 MB) and a per-profile quota. Retention is operator-configured, with no default deletion in v1. Deleting a profile's artefacts is an explicit operator action.
- Backup: the store is plain files plus SQLite and is included in normal home backups. It contains user data, so treat it as such.

## 7. Client behaviour summary

The full Android detail is in the companion brief.

- **Inline images:** `image/png`, `image/jpeg`, `image/webp` and non-animated `image/gif` are downloaded with the bearer header into the app-private cache. They're shown downsampled in the bubble, and tapping opens a full-screen viewer. Never pass the key in a URL to an image component.
- **Document cards:** everything else allowlisted (PDF, CSV, TXT, MD, JSON, DOCX, XLSX, PPTX, ZIP). The card shows the name, type, size and time, with Save, Share and Open with… actions. SVG and HTML are shown only as document cards, never rendered.
- **Save:** Storage Access Framework create-document, so no storage permission is needed.
- **Share:** a `content://` URI from the app's FileProvider to the Android share sheet.

## 8. History and reconnect

Hermes owns session history and the bridge can't inject into it. Association therefore works like this:

1. Each artefact records its `session_id`, plus `run_id` when known.
2. When a conversation opens, after history loads, the app calls the list endpoint for that `session_id` and places each card after the assistant turn of its `run_id`. If the run is unknown, the card goes at the nearest preceding turn by `created_at`.
3. During a live run, the app re-lists when the run reaches a terminal state, and also after any stream reconnect. A streamed artefact event is optional, needs Q1, and is de-duplicated by artefact ID.
4. Re-listing is idempotent. Cards are keyed by artefact ID, so reloads and reconnects never duplicate them.
5. Artefacts survive bridge restarts because the store is durable. Phone-side cache misses re-download by ID.

## 9. Capability negotiation

- The app reads `GET /v1/status`. If `features.artefacts.version` is missing or unsupported, all artefact UI is hidden and no artefact endpoints are called.
- A 404 on artefact routes (an older bridge) is treated as "unsupported", not as an error banner.
- The server advertises the media-type allowlists. The client intersects them with its own and shows unsupported types as a non-actionable "unsupported file" card.
- Without a bridge key (bridge not configured), artefacts are unavailable. Chat keeps working unchanged.
- Demo mode ships labelled sample artefacts with no network calls.

## 10. Security boundaries

- **Authentication:** the bridge bearer key only, in the `Authorization` header. No query-string tokens and no signed public URLs in v1. The Hermes chat API key is never sent to the bridge.
- **Authorization:** the existing mobile scope applies unchanged. The profile check happens before disk access. IDs are unguessable, but they're defence in depth, not the access control.
- **Exposure:** the same tailnet-only Serve origin (`/bridge`). No Funnel. The private backend stays unexposed.
- **Path safety:** ingestion reads only from an approved source (Q1) under a canonicalised root. It rejects symlinks, `..`, devices and FIFOs, and copies bytes into the store. Serving never touches source paths.
- **Content safety:** the media type is decided by a server-side sniff plus allowlist, not by the agent-supplied extension. `nosniff` and `attachment` disposition are always set. No server-side rendering of documents.
- **Size and DoS:** per-file and per-profile caps, streamed I/O and bounded list pages.
- **Logging:** IDs, sizes and status codes only. Never content, filenames from other profiles, or keys.
- **Client storage:** the app-private cache only (no external storage), excluded from backup (`allowBackup:false`), and cleared on disconnect or unpair. Saved and shared copies leave the app's control by the user's explicit action.
- **Model boundary:** artefacts may contain data that came from a hosted model provider. This feature doesn't change what is sent to providers.

## 11. End-to-end acceptance tests

These run against a **non-production** Hermes, bridge and store instance, plus the physical phone. Live services aren't restarted or reconfigured for testing without approval. Bridge tests extend `tests/test_mobile_scope.py` and `tests/test_bridge.py`. App tests extend `test/poc-*.test.mjs`. Device steps are recorded with evidence.

| ID | Scenario | Pass criteria |
|---|---|---|
| AT-01 | Missing or wrong bridge key on each artefact route | 401, no disk access |
| AT-02 | Out-of-scope profile, or an ID from another profile | 403 `profile_denied` and 404 respectively, before any disk access; the response is identical to an unknown ID |
| AT-03 | Malformed, traversal-shaped or overlong ID | 400 or 404, no filesystem lookup outside the index |
| AT-04 | Ingest a PNG; open the conversation on the phone | Inline image appears after the right turn; tap gives full screen; bytes match `sha256` |
| AT-05 | Ingest a PDF and an XLSX | Document cards with correct name, type and size; no inline rendering |
| AT-06 | Ingest SVG and HTML | Shown only as document cards; never rendered; served as `attachment` with `nosniff` |
| AT-07 | Save the PDF via SAF | File created at the user-chosen location; no storage permission requested; content matches |
| AT-08 | Share the image and the PDF | Android share sheet offers targets; recipient receives an identical file via `content://` |
| AT-09 | Kill the app mid-run; reopen | Artefacts produced during the run appear exactly once after history reload |
| AT-10 | Drop the network during a run; reconnect | Stream resumes via `Last-Event-ID`; artefacts appear once after terminal-state re-list |
| AT-11 | Restart the test bridge | Previously listed artefacts are still listed and downloadable; `ETag` unchanged |
| AT-12 | Crash during ingestion (fault injection) | Either no artefact or a complete one; no partial blob is served |
| AT-13 | Bridge without the feature (current 0.4.0) | App hides all artefact UI; chat unaffected; no error banners |
| AT-14 | Over-size file at ingestion | 413 `artefact_too_large`; nothing stored |
| AT-15 | Disconnect or unpair in the app | Artefact cache deleted; nothing remains in app-private storage |
| AT-16 | Secrets audit | Key never in URLs, logs, cache filenames or the APK bundle; bridge logs contain no content |
| AT-17 | Demo mode | Labelled sample artefacts render; zero network requests |

## 12. Open questions

- **Q1 (blocking):** how does Hermes signal a deliverable file? Candidates: (a) a designated per-profile outbox directory that the bridge watches; (b) an explicit Hermes tool or plugin that registers a file with the bridge; (c) structured markers in run output. Option (c) is not recommended unless strictly confined to an allowlisted root. Confirm against hermes-agent source. Don't invent Hermes endpoints.
- Q2: should artefact events appear in the `/v1/runs` event stream (needs Hermes support), or is re-listing enough?
- Q3: retention defaults and quotas for a small single-host deployment.
- Q4: whether thumbnails are generated server-side (stdlib can't decode images) or client-side only.
- Q5: whether artefacts outlive deleting a session or profile.
