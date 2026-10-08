# AL AMR — Worker Architecture (GitHub Actions)

## 1. Responsibilities

The GitHub Actions Worker (`.github/workflows/worker.yml`, `worker_runner.py`) is the high-compute muscle of the system:
- **Zero Cost Compute**: Leverages GitHub Actions free runner minutes (2,000 min/month for private repos, unlimited for public).
- **High Resource Headroom**: Standard GitHub runners provide 4 vCPUs, 16 GB RAM, and fast network egress suitable for YouTube 4K ingestion.
- **Isolation**: Each job executes in a pristine, ephemeral virtual machine, completely eliminating state bleed or disk exhaustion.

---

## 2. Worker Workflow Specification (`.github/workflows/worker.yml`)

The workflow accepts inputs via `workflow_dispatch`:
- `job_id`: Target AL AMR Job ID.
- `source_url`: YouTube or media URL to ingest.
- `job_settings`: Escaped JSON string containing BGM selections, visual filters, caption styling, and duration bounds.
- `callback_url`: Control Plane endpoint (`https://.../api/jobs/worker_callback`).
- `callback_secret`: HMAC authentication secret.

### Workflow Steps:
1. **Checkout Code**: Checks out repository code.
2. **Setup Python 3.11**: Caches pip dependencies.
3. **Install FFmpeg & System Dependencies**: Installs `ffmpeg`, `libegl1`, `fonts-dejavu`.
4. **Cloudflare WARP Egress (Optional)**: Engages WARP client if datacenter IP bot blocks occur during YouTube acquisition.
5. **Execute Worker Runner**: Runs `python -m autoclip.jobs.worker_runner`.
6. **Post-Execution Artifact Upload**: Uploads job logs as workflow artifacts.
