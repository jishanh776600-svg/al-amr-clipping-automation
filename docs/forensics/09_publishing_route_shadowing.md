# Forensic Report: Publishing Route Shadowing

## Symptom
GET /api/publishing/destinations returned 404 or routed to /{publication_id}

## Impact
Publishing destinations could not be listed in Web Console

## Root Cause
FastAPI route declaration order placed parameterized path /{publication_id} above static /destinations path

## Evidence
Request for /destinations tried to parse 'destinations' as a UUID publication ID

## Fix
Reordered route handlers in publishing router placing static subpaths before parameterized paths

## Files Changed
- `backend/autoclip/api/publishing.py`

## Commit
`c6aee65`

## Tests
- `tests/test_publishing.py`

## Production Verification
VERIFIED IN PRODUCTION

## Remaining Risk
None
