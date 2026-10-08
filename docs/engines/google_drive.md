# Engine: Google Drive Storage Integration

- **Source Files**: `backend/autoclip/storage/drive.py`.
- **Primary Classes / Functions**: `GoogleDriveStorage`.
- **Purpose**: Autonomous cloud persistence for final MP4 renders, subtitles, and metadata JSON.
- **Methods**:
  - `upload_file(local_path, parent_folder_id, mime_type)`: Resumable multipart upload.
  - `download_file(file_id, dest_path)`: Streams binary content to local scratch disk.
  - `ensure_folder(folder_name, parent_id)`: Idempotently creates directory hierarchy.
- **Tests**: `tests/test_drive_artifact_telegram_delivery.py`.
