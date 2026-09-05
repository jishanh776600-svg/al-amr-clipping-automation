/**
 * AL AMR CLIPPING // Central API Client & State Synchronization Layer
 * Zero mock data. Authoritative connection to backend control layer.
 */

class AlAmrAPI {
    static getOperatorToken() {
        return localStorage.getItem("al_amr_operator_token") || "";
    }

    static setOperatorToken(token) {
        if (token) {
            localStorage.setItem("al_amr_operator_token", token.trim());
        } else {
            localStorage.removeItem("al_amr_operator_token");
        }
    }

    static getHeaders(extra = {}) {
        const headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            ...extra
        };
        const token = this.getOperatorToken();
        if (token) {
            headers["Authorization"] = `Bearer ${token}`;
            headers["X-Operator-Token"] = token;
        }
        return headers;
    }

    static async request(endpoint, options = {}) {
        const url = endpoint.startsWith("/") ? endpoint : `/${endpoint}`;
        const config = {
            ...options,
            headers: this.getHeaders(options.headers || {})
        };

        try {
            const resp = await fetch(url, config);
            if (resp.status === 401) {
                throw new Error("Unauthorized: Valid Operator Token required for this action.");
            }
            if (!resp.ok) {
                let errMsg = `HTTP ${resp.status} ${resp.statusText}`;
                try {
                    const errData = await resp.json();
                    if (errData && errData.detail) errMsg = errData.detail;
                } catch (_) {}
                throw new Error(errMsg);
            }
            return await resp.json();
        } catch (err) {
            console.error(`[AlAmrAPI] Request to ${url} failed:`, err);
            throw err;
        }
    }

    // --- SUB-SYSTEM API METHODS ---

    // 1. Unified Dashboard Overview
    static async getDashboardOverview() {
        return this.request("/api/dashboard/overview");
    }

    // 2. Control & Health Telemetry
    static async getSystemStatus() {
        return this.request("/api/system/status");
    }

    static async getControlState() {
        return this.request("/api/control/state");
    }

    // 3. Pipeline Stages
    static async getPipelineStages() {
        return this.request("/api/pipeline/stages");
    }

    // 4. Clipping Production Jobs
    static async listJobs(limit = 50) {
        return this.request(`/api/jobs?limit=${limit}`);
    }

    static async getJobDetail(jobId) {
        return this.request(`/api/jobs/${encodeURIComponent(jobId)}`);
    }

    static async getJobClips(jobId) {
        return this.request(`/api/jobs/${encodeURIComponent(jobId)}/clips`);
    }

    static async makeClipDecision(jobId, clipId, action, notes = "") {
        return this.request(`/api/jobs/${encodeURIComponent(jobId)}/clips/${encodeURIComponent(clipId)}/decision`, {
            method: "POST",
            body: JSON.stringify({ action, notes })
        });
    }

    // 5. Agent & Task Queue
    static async getAgentStatus() {
        return this.request("/api/agent/status");
    }

    static async listTasks(status = null, taskType = null, limit = 50) {
        const params = new URLSearchParams();
        if (status) params.append("task_status", status);
        if (taskType) params.append("task_type", taskType);
        params.append("limit", limit.toString());
        return this.request(`/api/agent/tasks?${params.toString()}`);
    }

    static async getTaskDetail(taskId) {
        return this.request(`/api/agent/tasks/${encodeURIComponent(taskId)}`);
    }

    static async retryTask(taskId, reason = "") {
        return this.request(`/api/agent/tasks/${encodeURIComponent(taskId)}/retry`, {
            method: "POST",
            body: JSON.stringify({ reason })
        });
    }

    static async cancelTask(taskId, reason = "") {
        return this.request(`/api/agent/tasks/${encodeURIComponent(taskId)}/cancel`, {
            method: "POST",
            body: JSON.stringify({ reason })
        });
    }

    static async getQueueStatus() {
        return this.request("/api/agent/queue");
    }

    // 6. Cloud Workers & Leases
    static async listWorkers(limit = 50) {
        return this.request(`/api/agent/workers?limit=${limit}`);
    }

    static async reclaimStaleWorkers(staleThresholdSeconds = 0) {
        return this.request("/api/agent/workers/reclaim-stale", {
            method: "POST",
            body: JSON.stringify({ stale_threshold_seconds: staleThresholdSeconds })
        });
    }

    // 7. Campaign Operations
    static async listCampaigns() {
        return this.request("/api/campaigns");
    }

    static async getCampaignDetail(campaignId) {
        return this.request(`/api/campaigns/${encodeURIComponent(campaignId)}`);
    }

    static async updateCampaignStatus(campaignId, status, reason = "") {
        return this.request(`/api/campaigns/${encodeURIComponent(campaignId)}/status`, {
            method: "POST",
            body: JSON.stringify({ status, reason })
        });
    }

    // 8. Account & Vault Management
    static async listAccounts() {
        return this.request("/api/accounts");
    }

    static async getAccountDetail(platform, accountId) {
        return this.request(`/api/accounts/${encodeURIComponent(platform)}/${encodeURIComponent(accountId)}`);
    }

    static async updateAccountStatus(platform, accountId, status) {
        return this.request(`/api/accounts/${encodeURIComponent(platform)}/${encodeURIComponent(accountId)}/status`, {
            method: "POST",
            body: JSON.stringify({ status })
        });
    }

    // 9. Human Approval Gateway
    static async listPendingApprovals(limit = 50) {
        return this.request(`/api/approvals/pending?limit=${limit}`);
    }

    static async listApprovalHistory(limit = 50) {
        return this.request(`/api/approvals/history?limit=${limit}`);
    }

    // 10. Publishing Queue
    static async listPublishingQueue(limit = 50) {
        return this.request(`/api/publishing/queue?limit=${limit}`);
    }

    // 11. Escalations & Telemetry
    static async listEscalations(status = null, limit = 50) {
        const query = status ? `?status=${encodeURIComponent(status)}&limit=${limit}` : `?limit=${limit}`;
        return this.request(`/api/agent/escalations${query}`);
    }

    static async getEscalationDetail(escalationId) {
        return this.request(`/api/agent/escalations/${encodeURIComponent(escalationId)}`);
    }

    static async resolveEscalation(escalationId, action, notes = "") {
        return this.request(`/api/agent/escalations/${encodeURIComponent(escalationId)}/resolve`, {
            method: "POST",
            body: JSON.stringify({ action, notes })
        });
    }

    static async listTelemetry(limit = 50) {
        return this.request(`/api/agent/telemetry?limit=${limit}`);
    }

    // 12. Safety & Master Control Mutations
    static async emergencyStop(reason) {
        return this.request("/api/control/emergency-stop", {
            method: "POST",
            body: JSON.stringify({ reason })
        });
    }

    static async resume(reason = "Operator manual resume") {
        return this.request("/api/control/resume", {
            method: "POST",
            body: JSON.stringify({ reason })
        });
    }

    static async pause(reason = "Operator manual pause") {
        return this.request("/api/control/pause", {
            method: "POST",
            body: JSON.stringify({ reason })
        });
    }

    static async setPublishLock(locked, reason = "") {
        return this.request("/api/control/publish-lock", {
            method: "POST",
            body: JSON.stringify({ locked, reason })
        });
    }

    static async runNow(sourceUri, campaignId = "default_campaign") {
        return this.request("/api/control/run-now", {
            method: "POST",
            body: JSON.stringify({ source_uri: sourceUri, campaign_id: campaignId })
        });
    }
}

window.AlAmrAPI = AlAmrAPI;
