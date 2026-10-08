# Engine: BGM Vault & Persistence

- **Source Files**: `backend/autoclip/bgm/vault.py`, `backend/autoclip/bgm/metadata.py`, `backend/autoclip/bgm/validation.py`.
- **Primary Classes / Functions**: `BGMVault`, `BGMAssetRecord`, `scan_bgm_directory()`.
- **Purpose**: Manages authoritative background music tracks, preserves explicit operator selection across restarts, and reconciles database entries.
- **Inputs**: WAV audio files in `backend/autoclip/bgm/assets/`.
- **Outputs**: `BGMAssetRecord` entries in SQLite with duration, tempo, mood, and genre tags.
- **Precedence Rules**:
  1. Explicit operator selection in Web Console takes absolute priority.
  2. Campaign Brief mood/tempo recommendation is used if no operator override exists.
  3. Graceful fallback to No-BGM mode if selected track is missing from disk.
- **Tests**: `tests/test_bgm_vault.py`, `tests/test_bgm_filters_subtitles_campaign.py`.
