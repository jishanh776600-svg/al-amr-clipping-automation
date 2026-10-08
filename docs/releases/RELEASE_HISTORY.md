# AL AMR — Release History & Milestone Groupings

The evolution of AL AMR is grouped into four major generational releases:

### Generation 1: Foundation & Local Engine (Commits 8ca0a86 – 5ac7a2d)
- Initial pipeline prototype, transcript alignment, candidate discovery, and local UI.

### Generation 2: Decoupled Architecture & Cloud Persistence (Commits 2f51161 – c3fd33c)
- Transitioned compute from Render to GitHub Actions on-demand workers.
- Introduced Google Drive persistent storage and Telegram review delivery.
- Hardened SQLite WAL checkpoints and Fernet credential vault.

### Generation 3: Acoustic & Visual Intelligence (Commits 622bdac – bb878e7)
- Implemented Semantic B-Roll Engine with Pexels portrait stock integration.
- Calibrated BGM sidechain ducking (-14 LUFS standard) and 5-clip strict guarantee.
- Added multi-platform publishing adapters for YouTube Shorts and Instagram Reels.

### Generation 4: Production Closure & Final Two Blockers (Current State)
- Resolved audio end-of-clip cutoff / mute elimination (`apad`, safe margin, natural decay tail $\ge 0.35$s).
- Resolved Telegram review inline buttons hanging via immediate callback query acknowledgment and remote clip reconciliation.
- Certified 70/70 tests passing with zero regressions.
