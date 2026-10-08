# Forensic Report: Duration Mismatch & Inconsistent Bounds

## Symptom
Clips rendered with erratic lengths (e.g. 11s or 75s) instead of standard short-form duration

## Impact
Reduced viewer retention and algorithm rejection on YouTube Shorts

## Root Cause
Different subsystems applied conflicting duration defaults (e.g. 15s, 60s, or unconstrained)

## Evidence
Video duration inspection showed lengths outside target bounds

## Fix
Centralized duration resolution in resolve_duration_limits() with canonical default strictly clamped to [20.0s, 30.0s]

## Files Changed
- `backend/autoclip/campaign/duration.py`
- `backend/autoclip/pipeline/highlights.py`

## Commit
`4d22998`

## Tests
- `tests/test_duration_enforcement.py`

## Production Verification
VERIFIED IN PRODUCTION

## Remaining Risk
None
