# Forensic Report: Render Persistent Disk Auto-Detection

## Symptom
Database placed in ~/.autoclip instead of /data, losing data on restart

## Impact
All jobs and settings disappeared on redeploy

## Root Cause
paths.root() checked only AUTOCLIP_HOME env var; Render mount /data was not checked by default

## Evidence
Disk inspection showed data being written to container overlay filesystem

## Fix
Added automatic /data detection and write verification in paths.root()

## Files Changed
- `backend/autoclip/paths.py`

## Commit
`61a7c45`

## Tests
- `tests/test_paths.py`

## Production Verification
VERIFIED IN PRODUCTION

## Remaining Risk
None
