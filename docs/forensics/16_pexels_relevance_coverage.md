# Forensic Report: Pexels Relevance & Portrait Aspect Ratio

## Symptom
Pexels returned horizontal 16:9 stock videos that cropped awkwardly on vertical reels

## Impact
Distorted or low-resolution overlays

## Root Cause
Pexels search query omitted orientation parameter

## Evidence
Downloaded videos were 1920x1080 horizontal videos scaled down

## Fix
Enforced orientation=portrait and minimum 1080p resolution in Pexels API calls

## Files Changed
- `backend/autoclip/pipeline/broll/pexels_client.py`

## Commit
`5192744`

## Tests
- `tests/test_pexels_and_clips_failure.py`

## Production Verification
VERIFIED IN PRODUCTION

## Remaining Risk
None
