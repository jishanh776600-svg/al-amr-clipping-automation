# Engine: Autonomous Multi-Platform Publishing

- **Source Files**: `backend/autoclip/publishing/service.py`, `backend/autoclip/publishing/youtube.py`, `backend/autoclip/publishing/instagram.py`, `backend/autoclip/publishing/orchestrator.py`.
- **Primary Classes / Functions**: `PublishingService`, `YouTubePublisher`, `InstagramPublisher`, `PublishingOrchestrator`.
- **Purpose**: Publishes approved video clips to YouTube Shorts and Instagram Reels with strict idempotency and platform fault-isolation.
- **Platform Isolation**: If YouTube upload succeeds but Instagram fails (or vice versa), the clip is marked `PARTIALLY_PUBLISHED`; successful uploads are never duplicated.
- **Media Resolution**: Local file $	o$ Google Drive API $	o$ Google Drive direct HTTP $	o$ Telegram Bot API `getFile`.
- **Tests**: `tests/test_publishing.py`, `tests/test_approval_publish_flow.py`.
