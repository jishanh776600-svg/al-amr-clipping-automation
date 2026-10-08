# Forensic Report: Publishing Multi-Account Resolution

## Symptom
Publishing to YouTube or Instagram failed when using non-default account IDs

## Impact
Multi-channel creators could not route clips to specific brand channels

## Root Cause
PublishingService hardcoded default environment variable names rather than dynamic destination config

## Evidence
Publishing attempts routed to wrong channel or threw KeyError

## Fix
Implemented dynamic multi-account resolution reading account configs per destination record

## Files Changed
- `backend/autoclip/publishing/service.py`

## Commit
`50b9179`

## Tests
- `tests/test_publishing.py`

## Production Verification
VERIFIED IN PRODUCTION

## Remaining Risk
None
