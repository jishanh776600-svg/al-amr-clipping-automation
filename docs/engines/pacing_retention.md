# Engine: Pacing, Retention & Speech Tail Protection

- **Source Files**: `backend/autoclip/pipeline/retention/pacing.py`, `backend/autoclip/pipeline/retention/analyzer.py`.
- **Purpose**: Eliminates awkward dead air between words while preserving a minimum 0.35s room decay tail after the last word.
