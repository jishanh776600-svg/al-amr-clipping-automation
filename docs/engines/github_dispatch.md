# Engine: GitHub Actions Job Dispatcher

- **Source Files**: `backend/autoclip/jobs/dispatcher.py`.
- **Primary Classes / Functions**: `dispatch_job()`, `get_dispatch_preflight_status()`.
- **Purpose**: Dispatches the `worker.yml` workflow on GitHub Actions using the GitHub REST API (`POST /repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches`).
- **Features**:
  - Pulls `GITHUB_PAT` seamlessly from the encrypted SQLite vault.
  - Serializes job settings into safe single-quoted JSON inputs.
  - Records GitHub Run ID and URL for live console progress tracking.
- **Tests**: `tests/test_orchestration.py`, `tests/test_pat_persistence_regression.py`.
