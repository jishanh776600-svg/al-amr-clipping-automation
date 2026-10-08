# Engine: Candidate Discovery & Clipping

- **Source Files**: `backend/autoclip/pipeline/highlights.py`, `backend/autoclip/campaign/candidate_discovery.py`, `backend/autoclip/campaign/clip_assembly.py`, `backend/autoclip/campaign/duration.py`, `backend/autoclip/pipeline/boundaries.py`.
- **Primary Classes / Functions**: `CandidateDiscoveryEngine`, `ClipAssemblyEngine`, `detect()`, `resolve_duration_limits()`, `is_true_sentence_terminal()`.
- **Purpose**: Identifies high-retention vertical clip candidates from transcribed speech and aligns them to strict duration and sentence boundaries.
- **Inputs**: `Transcript` (list of `Word` objects with timestamps), `CampaignBriefRecord` (rules, tone, banned words).
- **Outputs**: List of 5 selected `Clip` objects (`start_s`, `end_s`, `title`, `hook`, `score`).
- **Execution Order**:
  1. Speech density and keyword cluster detection.
  2. Hook score evaluation (first 3–5 seconds velocity and question/statement punchiness).
  3. Climax and narrative resolution detection.
  4. Campaign requirement evaluation (banned words filter, mandatory CTA check).
  5. Candidate deduplication and boundary snapping to true sentence terminals (`.`, `!`, `?`).
- **Safeguards**:
  - Trailing ellipses (`...`, `…`) and filler words (`just`, `really`, `you know`) are rejected as sentence endings.
  - Fails with `INSUFFICIENT_VALID_CLIPS` if fewer than 5 valid non-overlapping candidates exist.
- **Tests**: `tests/test_clip_assembly.py`, `tests/test_candidate_discovery.py`, `tests/test_duration_enforcement.py`.
