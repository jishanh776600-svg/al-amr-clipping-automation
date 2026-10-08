# Engine: TTS & Speech Synthesis (Optional / Fallback)

- **Source Files**: `backend/autoclip/pipeline/transcribe.py`, `backend/autoclip/pipeline/retention/analyzer.py`.
- **Primary Classes / Functions**: `TranscribeEngine`, speech pace analyzers.
- **Purpose**: In typical clipping workflows, speech is ingested directly from the original video. In voiceover / commentary modes, synthesizes narration matching target WPM (140–160 WPM).
- **Inputs**: Clean transcript text.
- **Outputs**: Synthesized audio track with phoneme/word alignment markers.
