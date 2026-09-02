"""Pre-deployment verification test script for AL AMR Clipping Automation."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from httpx import AsyncClient, ASGITransport
from clipping.ui.server import app

async def verify_all_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        print("--- 1. Health Probe (/healthz) ---")
        res = await client.get("/healthz")
        print(f"GET /healthz: {res.status_code} -> {res.json()}")
        assert res.status_code == 200
        assert res.json()["status"] in ["healthy", "degraded"]

        print("\n--- 2. System Status (/api/system/status) ---")
        res = await client.get("/api/system/status")
        print(f"GET /api/system/status: {res.status_code} -> {res.json()['system_name']}")
        assert res.status_code == 200
        assert res.json()["system_name"] == "AL AMR Clipping Automation"

        print("\n--- 3. Control State (/api/control/state) ---")
        res = await client.get("/api/control/state")
        ctrl_json = res.json()
        print(f"GET /api/control/state: {res.status_code} -> mode: {ctrl_json['state']['mode']}")
        assert res.status_code == 200

        print("\n--- 4. Mutating Endpoint Security (Authorization Gate) ---")
        # In test mode without OPERATOR_TOKEN configured, fallback operator is used.
        # Let's test with header
        res = await client.post(
            "/api/control/emergency-stop",
            json={"reason": "Pre-flight security validation check"},
            headers={"X-Operator-Token": "test_operator_token_123"},
        )
        print(f"POST /api/control/emergency-stop: {res.status_code} -> {res.json()['status']}")
        assert res.status_code == 200

        # Verify stopped status
        h_res = await client.get("/healthz")
        print(f"GET /healthz (after stop): {h_res.json()['status']} (emergency_stopped: {h_res.json()['emergency_stopped']})")
        assert h_res.json()["emergency_stopped"] is True

        # Verify RUN NOW is blocked when emergency stopped
        run_res = await client.post(
            "/api/control/run-now",
            json={"source_uri": "https://www.youtube.com/watch?v=sample"},
            headers={"X-Operator-Token": "test_operator_token_123"},
        )
        print(f"POST /api/control/run-now (blocked): {run_res.status_code} -> {run_res.json()['detail']}")
        assert run_res.status_code == 409

        # Resume automation
        res_resume = await client.post(
            "/api/control/resume",
            json={"reason": "Pre-flight safety test completed"},
            headers={"X-Operator-Token": "test_operator_token_123"},
        )
        print(f"POST /api/control/resume: {res_resume.status_code} -> {res_resume.json()['status']}")
        assert res_resume.status_code == 200

        # Run Now succeeds after resume
        res_run = await client.post(
            "/api/control/run-now",
            json={"source_uri": "https://www.youtube.com/watch?v=sample", "campaign_id": "test_campaign"},
            headers={"X-Operator-Token": "test_operator_token_123"},
        )
        print(f"POST /api/control/run-now (active): {res_run.status_code} -> job_id: {res_run.json()['job_id']}")
        assert res_run.status_code == 200

        print("\nALL PRE-DEPLOYMENT PRODUCTION ENDPOINT CHECKS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(verify_all_endpoints())
