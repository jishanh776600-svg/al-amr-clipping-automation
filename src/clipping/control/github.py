"""GitHub Actions Workflow Dispatcher for AL AMR Master Control Plane."""

from typing import Any, Dict, List, Optional, Tuple
import httpx
from clipping.config.settings import Settings
from clipping.logging.logger import get_logger

logger = get_logger("clipping.control.github")


class GitHubWorkflowDispatcher:
    """Interacts with GitHub REST API to trigger and observe ephemeral worker workflows."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or Settings()

    @property
    def is_configured(self) -> bool:
        return bool(self.settings.GITHUB_PAT and self.settings.GITHUB_REPO)

    async def dispatch_workflow(
        self,
        workflow_name: str = "pipeline_orchestration.yml",
        inputs: Optional[Dict[str, Any]] = None,
        ref: str = "main",
    ) -> Tuple[bool, str]:
        """
        Dispatches a workflow execution via GitHub API:
        POST /repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches
        """
        if not self.is_configured:
            msg = "GitHub integration not fully configured (GITHUB_PAT or GITHUB_REPO missing)"
            logger.info("Skipping live GitHub dispatch; recorded request in durable state", reason=msg)
            return False, msg

        repo = self.settings.GITHUB_REPO
        token = self.settings.GITHUB_PAT.get_secret_value()
        url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_name}/dispatches"

        payload = {
            "ref": ref,
            "inputs": inputs or {},
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "AL-AMR-Control-Plane/1.0",
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 204:
                    logger.info("Successfully dispatched GitHub Actions workflow", workflow=workflow_name, repo=repo)
                    return True, "Workflow dispatched successfully"
                else:
                    error_msg = f"GitHub API error {resp.status_code}: {resp.text}"
                    logger.error("Failed to dispatch GitHub workflow", status_code=resp.status_code, error=resp.text)
                    return False, error_msg
        except Exception as e:
            logger.error("Exception during GitHub workflow dispatch", error=str(e))
            return False, f"Dispatch exception: {str(e)}"

    async def list_recent_runs(
        self,
        workflow_name: str = "pipeline_orchestration.yml",
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Retrieves the recent execution status of a specified workflow from GitHub."""
        if not self.is_configured:
            return []

        repo = self.settings.GITHUB_REPO
        token = self.settings.GITHUB_PAT.get_secret_value()
        url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_name}/runs"

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "AL-AMR-Control-Plane/1.0",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers, params={"per_page": limit})
                if resp.status_code == 200:
                    runs = resp.json().get("workflow_runs", [])
                    return [
                        {
                            "id": r.get("id"),
                            "name": r.get("name"),
                            "status": r.get("status"),
                            "conclusion": r.get("conclusion"),
                            "html_url": r.get("html_url"),
                            "created_at": r.get("created_at"),
                            "updated_at": r.get("updated_at"),
                        }
                        for r in runs
                    ]
                return []
        except Exception as e:
            logger.error("Failed to query workflow runs", error=str(e))
            return []
