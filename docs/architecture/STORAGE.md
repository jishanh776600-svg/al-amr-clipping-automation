# AL AMR — Storage Architecture & Artifact Persistence

## 1. Storage Tiers

| Tier | Media / Data Type | Location | Retention Policy | Fallback Mechanism |
| :--- | :--- | :--- | :--- | :--- |
| **Tier 1: Ephemeral Scratch** | Intermediate audio, WAV, raw frames | Worker disk (`/tmp` or runner disk) | Deleted on runner exit | Re-generated on retry |
| **Tier 2: Persistent State** | SQLite DB (`autoclip.db`), `.master_key` | Render Persistent Volume (`/data`) | Permanent across restarts | Daily DB backup / recovery |
| **Tier 3: Permanent Artifacts** | Rendered MP4s, cover images, subtitles | Google Drive (`clips/<job_id>/...`) | Permanent cloud archival | Direct download link |
| **Tier 4: Distributed Previews** | Native MP4 previews sent to Telegram | Telegram Cloud CDN | Permanent in channel | Telegram Bot API `getFile` |

---

## 2. Google Drive Directory Layout

All artifacts uploaded by `GoogleDriveStorage` follow a standardized hierarchy under `GOOGLE_DRIVE_ROOT_FOLDER_ID`:

```
Google Drive Root Folder/
└── clips/
    └── <job_id>/
        ├── clip_<clip_id_1>_9x16.mp4
        ├── clip_<clip_id_1>_metadata.json
        ├── clip_<clip_id_2>_9x16.mp4
        ├── clip_<clip_id_2>_metadata.json
        ├── clip_<clip_id_3>_9x16.mp4
        ├── clip_<clip_id_3>_metadata.json
        ├── clip_<clip_id_4>_9x16.mp4
        ├── clip_<clip_id_4>_metadata.json
        ├── clip_<clip_id_5>_9x16.mp4
        └── clip_<clip_id_5>_metadata.json
```

---

## 3. Media Resolution Hierarchy for Publishing

When `PublishingService.publish_clip(clip_id)` is invoked, media is resolved in the following priority order:
1. **Local Disk**: `FinalRenderRecord.output_path` or `Export.path` if file exists and size $> 0$.
2. **Google Drive Storage**: Downloads via `GoogleDriveStorage.download_file(drive_file_id)`.
3. **Google Drive Direct HTTP**: Downloads via `https://drive.google.com/uc?export=download&id={drive_file_id}`.
4. **Telegram Bot API Fallback**: Downloads native MP4 using `telegram_file_id` stored in telemetry via Telegram Bot API `getFile`.
5. **Fail-Closed**: If all four sources are unreachable, the publication record is marked `FAILED_PERMANENT` with `error_code="invalid_media"`.
