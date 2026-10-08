# Forensic Report: GitHub PAT Persistence Issue

## Symptom
GitHub PAT was lost on Render container redeploy or restart

## Impact
Automatic worker dispatch failed with 'Missing GitHub PAT'

## Root Cause
PAT was stored only in memory or ephemeral environment without disk backup

## Evidence
Web Console prompt asking to re-enter GitHub PAT after each restart

## Fix
Persisted encrypted PAT in SQLite app_credentials table using AES-256 envelope and /data persistent mount

## Files Changed
- `backend/autoclip/security/vault.py`
- `backend/autoclip/api/settings.py`

## Commit
`c3fd33c`

## Tests
- `tests/test_pat_persistence_regression.py`

## Production Verification
VERIFIED IN PRODUCTION

## Remaining Risk
None
