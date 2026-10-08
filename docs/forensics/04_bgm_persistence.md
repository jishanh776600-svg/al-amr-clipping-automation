# Forensic Report: BGM Persistence Issue

## Symptom
User-selected BGM in Web Console reverted to default or random track on render

## Impact
Loss of branding consistency and user intent

## Root Cause
Job settings serialization dropped BGM ID during dispatcher handoff to worker

## Evidence
Worker logs showed BGM ID fallback to auto-selected default

## Fix
Ensured explicit BGM selection propagates through job_settings JSON and takes absolute precedence

## Files Changed
- `backend/autoclip/jobs/dispatcher.py`
- `backend/autoclip/pipeline/audio_mix/engine.py`

## Commit
`03e88e2`

## Tests
- `tests/test_bgm_vault.py`

## Production Verification
VERIFIED IN PRODUCTION

## Remaining Risk
None
