# Forensic Report: BGM Mixing & Overpowering Audio

## Symptom
Background music drowned out speaker voice

## Impact
Unintelligible dialogue and failed audio quality gates

## Root Cause
amix filter lacked proper volume pre-attenuation and sidechain compression parameters

## Evidence
EBU R128 loudness probe showed speech and BGM colliding at similar dB levels

## Fix
Engineered sidechain ducking filtergraph (-18 dB attenuation during speech, 100ms attack, 350ms release)

## Files Changed
- `backend/autoclip/pipeline/audio_mix/engine.py`

## Commit
`cac3f9f`

## Tests
- `tests/test_bgm_mixing.py`

## Production Verification
VERIFIED IN PRODUCTION

## Remaining Risk
None
