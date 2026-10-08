# AL AMR — Test Strategy Specification

AL AMR adheres to a strict multi-layer test pyramid:
1. **Unit Tests**: Boundary detection, sentence terminals, duration calculation, and audio filtergraph generation.
2. **Integration Tests**: SQLite foreign key constraints, encrypted vault envelope hydration, and Telegram update processing.
3. **End-to-End Regression Tests**: Decoupled clip reconciliation, audio quality gate evaluation, and publishing execution.

All tests are executed via `pytest` with standard fixtures in `tests/conftest.py`.
