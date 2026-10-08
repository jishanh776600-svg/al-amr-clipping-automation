# Forensic Report: BGM Streaming & Auth Issue

## Symptom
Web Console preview player failed to stream BGM WAV files

## Impact
Operator could not audition music tracks before clipping

## Root Cause
FastAPI streaming response required auth headers that standard HTML5 audio elements could not supply

## Evidence
HTTP 401 Unauthorized in browser media element console

## Fix
Allowed query token authentication for preview streaming and configured proper CORS preflight headers

## Files Changed
- `backend/autoclip/api/bgm.py`
- `backend/autoclip/app.py`

## Commit
`31d5439`

## Tests
- `tests/test_api.py`

## Production Verification
VERIFIED IN PRODUCTION

## Remaining Risk
None
