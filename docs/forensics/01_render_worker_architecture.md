# Forensic Report: Render Worker Architecture Issue

## Symptom
Worker OOM crashes and CPU exhaustion on Render Free tier

## Impact
Jobs failed or timed out during transcription / FFmpeg encoding

## Root Cause
In-process worker on 512MB RAM container exceeded memory limits during Whisper & libx264 execution

## Evidence
Render memory alerts, SIGKILL exit code 137

## Fix
Offload compute completely to GitHub Actions via workflow_dispatch; disable Render local worker (AUTOCLIP_NO_WORKER=1)

## Files Changed
- `backend/autoclip/jobs/dispatcher.py`
- `backend/autoclip/app.py`
- `.github/workflows/worker.yml`

## Commit
`5f6cc42`

## Tests
- `tests/test_orchestration.py`

## Production Verification
VERIFIED IN PRODUCTION

## Remaining Risk
None (Compute fully decoupled)
