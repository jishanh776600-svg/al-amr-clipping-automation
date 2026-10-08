# Engine: Semantic Visual Matching & B-Roll Engine

- **Source Files**: `backend/autoclip/pipeline/broll/engine.py`, `backend/autoclip/pipeline/broll/semantic_parser.py`, `backend/autoclip/pipeline/broll/pexels_client.py`, `backend/autoclip/pipeline/broll/scorer.py`.
- **Primary Classes / Functions**: `BRollEngine`, `SemanticParser`, `PexelsClient`, `BRollScorer`.
- **Purpose**: Eliminates boring static talking heads by analyzing spoken concepts and overlaying relevant stock video footage.
- **Logic**:
  1. **Semantic Extraction**: Extracts concrete visual entities (e.g. "laptop", "server rack", "handshake", "shipping container").
  2. **Figurative Language Guard**: Ignores metaphors and conversational idioms (e.g. "time flies", "spill the beans").
  3. **Gap Hunter**: Flags continuous speaker A-roll segments exceeding 3.0 seconds without visual changes.
  4. **Pexels Querying**: Queries Pexels API for 9:16 portrait stock videos with minimum 1080p resolution.
  5. **Overlay Assembly**: Trims and scales B-roll clips, generating FFmpeg overlay instructions with 2.0s–3.5s duration.
- **Tests**: `tests/test_broll_semantic_engine.py`, `tests/test_pexels_and_clips_failure.py`.
