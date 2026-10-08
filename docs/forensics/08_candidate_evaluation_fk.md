# Forensic Report: CandidateEvaluation FK Constraint Failure

## Symptom
Job failed at export stage with SQLite FOREIGN KEY constraint error

## Impact
Job execution aborted before final video render

## Root Cause
CandidateEvaluation records inserted with clip_id before clip row was persisted to clips table

## Evidence
sqlite3.IntegrityError: FOREIGN KEY constraint failed

## Fix
Corrected database transaction ordering: persist Clip before attaching evaluations

## Files Changed
- `backend/autoclip/campaign/candidate_discovery.py`
- `backend/autoclip/db/store.py`

## Commit
`623738b`

## Tests
- `tests/test_fk_integrity_regression.py`

## Production Verification
VERIFIED IN PRODUCTION

## Remaining Risk
None
