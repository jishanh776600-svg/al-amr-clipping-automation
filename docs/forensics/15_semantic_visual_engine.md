# Forensic Report: Semantic Visual Engine Repetition & Idiom Errors

## Symptom
B-roll engine inserted literal animal stock footage for metaphors or repeated the same stock video multiple times

## Impact
Degraded visual quality and nonsensical video overlays

## Root Cause
Semantic parser lacked idiom translation and query deduplication

## Evidence
Visual audit showed stock cat video when speaker said 'let the cat out of the bag'

## Fix
Added figurative language dictionary, contextual business concepts, and anti-repetition query filter

## Files Changed
- `backend/autoclip/pipeline/broll/semantic_parser.py`
- `backend/autoclip/pipeline/broll/engine.py`

## Commit
`bb878e7`

## Tests
- `tests/test_broll_semantic_engine.py`

## Production Verification
VERIFIED IN PRODUCTION

## Remaining Risk
None
