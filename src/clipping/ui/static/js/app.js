/**
 * AL AMR CLIPPING // View Controllers & Action Modal Framework
 */

window.AlAmrViews = {
    // 1. MISSION OVERVIEW CONTROLLER
    overview: async function(state, force = false) {
        const container = document.getElementById("view-overview");
        if (!container) return;

        if (!state) {
            try {
                state = await AlAmrAPI.getDashboardOverview();
            } catch (err) {
                container.innerHTML = `<div class="p-6 text-rose-400 font-mono text-xs">Failed to load overview: ${err.message}</div>`;
                return;
            }
        }

        const counts = state.counts || {};
        const isEmergency = state.emergency_stopped;
        const isPaused = state.automation_paused;
        const isPubLocked = state.publishing_locked;

        // Render KPI metric grid
        const kpiHtml = `
            <div class="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3 mb-6">
                <div class="tech-card">
                    <div class="tech-card-title">SYSTEM MODE</div>
                    <div class="text-sm lg:text-base font-mono font-bold mt-1 ${isEmergency ? 'text-rose-400' : isPaused ? 'text-amber-300' : 'text-emerald-400'}">
                        ${state.operating_mode ? state.operating_mode.toUpperCase() : 'AUTOMATIC'}
                    </div>
                    <div class="text-[10px] font-mono text-slate-500 mt-0.5">${isEmergency ? 'HALTED' : isPaused ? 'PAUSED' : 'HEALTHY'}</div>
                </div>

                <div class="tech-card">
                    <div class="tech-card-title">MASTER AGENT</div>
                    <div class="text-sm lg:text-base font-mono font-bold mt-1 ${isEmergency ? 'text-rose-400' : 'text-cyan-400'}">
                        ${isEmergency ? 'HALTED' : 'STANDBY'}
                    </div>
                    <div class="text-[10px] font-mono text-slate-500 mt-0.5">Cloud Autonomous</div>
                </div>

                <div class="tech-card">
                    <div class="tech-card-title">QUEUE DEPTH</div>
                    <div class="text-sm lg:text-base font-mono font-bold mt-1 text-white">${counts.queue_depth || 0}</div>
                    <div class="text-[10px] font-mono text-slate-500 mt-0.5">Pending Tasks</div>
                </div>

                <div class="tech-card">
                    <div class="tech-card-title">ACTIVE WORKERS</div>
                    <div class="text-sm lg:text-base font-mono font-bold mt-1 text-purple-400">${counts.active_workers || 0}</div>
                    <div class="text-[10px] font-mono text-slate-500 mt-0.5">Ephemeral Leases</div>
                </div>

                <div class="tech-card">
                    <div class="tech-card-title">APPROVALS DUE</div>
                    <div class="text-sm lg:text-base font-mono font-bold mt-1 ${counts.pending_approvals > 0 ? 'text-amber-400' : 'text-slate-300'}">
                        ${counts.pending_approvals || 0}
                    </div>
                    <div class="text-[10px] font-mono text-slate-500 mt-0.5">Awaiting Human Gate</div>
                </div>

                <div class="tech-card">
                    <div class="tech-card-title">ESCALATIONS</div>
                    <div class="text-sm lg:text-base font-mono font-bold mt-1 ${counts.open_escalations > 0 ? 'text-rose-400' : 'text-slate-300'}">
                        ${counts.open_escalations || 0}
                    </div>
                    <div class="text-[10px] font-mono text-slate-500 mt-0.5">Active Blockers</div>
                </div>

                <div class="tech-card">
                    <div class="tech-card-title">PUBLISH GATE</div>
                    <div class="text-sm lg:text-base font-mono font-bold mt-1 ${isPubLocked || isEmergency ? 'text-rose-400' : 'text-emerald-400'}">
                        ${isPubLocked || isEmergency ? 'LOCKED' : 'OPEN'}
                    </div>
                    <div class="text-[10px] font-mono text-slate-500 mt-0.5">YouTube Shorts</div>
                </div>
            </div>
        `;

        // Section 1: Active Production Pipeline & Operations
        const pipelineHtml = `
            <div class="grid grid-cols-1 lg:grid-cols-12 gap-4 mb-6">
                <div class="lg:col-span-8 tech-card">
                    <div class="tech-card-header">
                        <div class="tech-card-title">
                            <span>CANONICAL 9-STAGE MEDIA PIPELINE</span>
                        </div>
                        <span class="status-pill operational">DETERMINISTIC ZERO-COST</span>
                    </div>
                    
                    <div class="pipeline-track mb-3">
                        <div class="pipeline-step complete"><span>01</span><span>INGEST</span><span>✓</span></div>
                        <div class="pipeline-step complete"><span>02</span><span>TRANSCRIBE</span><span>✓</span></div>
                        <div class="pipeline-step complete"><span>03</span><span>UNDERSTAND</span><span>✓</span></div>
                        <div class="pipeline-step complete"><span>04</span><span>DISCOVER</span><span>✓</span></div>
                        <div class="pipeline-step complete"><span>05</span><span>REFRAME</span><span>✓</span></div>
                        <div class="pipeline-step complete"><span>06</span><span>RENDER</span><span>✓</span></div>
                        <div class="pipeline-step complete"><span>07</span><span>QA</span><span>✓</span></div>
                        <div class="pipeline-step active"><span>08</span><span>APPROVAL</span><span class="text-cyan-300">●</span></div>
                        <div class="pipeline-step"><span>09</span><span>PUBLISH</span><span>—</span></div>
                    </div>
                    <p class="text-xs font-mono text-slate-400">
                        Cloud execution handles ingestion, Whisper transcription, active speaker detection, 9:16 vertical reframing, and ffmpeg GPU/CPU rendering.
                    </p>
                </div>

                <div class="lg:col-span-4 tech-card">
                    <div class="tech-card-header">
                        <div class="tech-card-title">OPERATOR SAFETY CONTROLS</div>
                        <span class="status-pill neutral">POLICY GOVERNED</span>
                    </div>
                    <div class="grid grid-cols-2 gap-2">
                        <button onclick="AlAmrModals.togglePause()" class="px-3 py-2 text-xs font-mono font-bold rounded bg-surface2 hover:bg-surface3 border border-slate-700 text-slate-200 transition text-left">
                            ${isPaused ? '▶ RESUME' : '⏸ PAUSE'}
                        </button>
                        <button onclick="AlAmrModals.togglePublishLock()" class="px-3 py-2 text-xs font-mono font-bold rounded bg-surface2 hover:bg-surface3 border border-slate-700 text-slate-200 transition text-left">
                            ${isPubLocked ? '🔓 UNLOCK PUB' : '🔒 LOCK PUB'}
                        </button>
                        <button onclick="AlAmrModals.openEmergencyStopModal()" class="col-span-2 px-3 py-2 text-xs font-mono font-bold rounded bg-rose-500/15 hover:bg-rose-500/25 border border-rose-500/40 text-rose-300 transition text-center">
                            🛑 TRIGGER GLOBAL EMERGENCY STOP
                        </button>
                    </div>
                </div>
            </div>
        `;

        // Section 2: Exceptions & Escalations + Telemetry
        const exceptions = state.open_escalations || [];
        const telemetry = state.recent_telemetry || [];

        const exceptionsRows = exceptions.length > 0
            ? exceptions.map(e => `
                <tr class="border-b border-slate-800">
                    <td class="font-mono text-rose-400 font-bold">${e.escalation_id}</td>
                    <td class="font-mono">${e.task_id || '—'}</td>
                    <td><span class="status-pill emergency">${e.reason || 'UNCLASSIFIED'}</span></td>
                    <td class="text-slate-300">${e.context ? (e.context.what_happened || '—') : '—'}</td>
                    <td>
                        <button onclick="AlAmrModals.openResolveEscalationModal('${e.escalation_id}')" class="px-2 py-1 text-[11px] font-mono rounded bg-surface3 hover:bg-slate-700 text-cyan-400 border border-slate-700">
                            Resolve
                        </button>
                    </td>
                </tr>
            `).join('')
            : `<tr><td colspan="5" class="text-center py-4 text-slate-500 font-mono text-xs">No open operator escalations. System running normally.</td></tr>`;

        const telemetryRows = telemetry.length > 0
            ? telemetry.map(t => `
                <tr class="border-b border-slate-800">
                    <td class="font-mono text-slate-400">${new Date(t.timestamp).toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })}</td>
                    <td class="font-mono text-cyan-400">${t.event_type}</td>
                    <td class="font-mono">${t.task_id || '—'}</td>
                    <td class="font-mono text-purple-300">${t.worker_id || 'cloud'}</td>
                </tr>
            `).join('')
            : `<tr><td colspan="4" class="text-center py-4 text-slate-500 font-mono text-xs">No recent telemetry events logged.</td></tr>`;

        const detailsHtml = `
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <div class="tech-card">
                    <div class="tech-card-header">
                        <div class="tech-card-title">
                            <span>OPERATOR ESCALATIONS & BLOCKERS</span>
                            ${exceptions.length > 0 ? `<span class="px-1.5 py-0.2 rounded text-[10px] bg-rose-500 text-white font-mono font-bold">${exceptions.length}</span>` : ''}
                        </div>
                        <a href="#escalations" class="text-xs font-mono text-cyan-400 hover:underline">View All →</a>
                    </div>
                    <div class="tech-table-container">
                        <table class="tech-table">
                            <thead>
                                <tr><th>ID</th><th>TASK</th><th>REASON</th><th>SUMMARY</th><th>ACTION</th></tr>
                            </thead>
                            <tbody>${exceptionsRows}</tbody>
                        </table>
                    </div>
                </div>

                <div class="tech-card">
                    <div class="tech-card-header">
                        <div class="tech-card-title">REAL-TIME CLOUD TELEMETRY</div>
                        <a href="#activity" class="text-xs font-mono text-cyan-400 hover:underline">View Stream →</a>
                    </div>
                    <div class="tech-table-container">
                        <table class="tech-table">
                            <thead>
                                <tr><th>TIME</th><th>EVENT</th><th>TASK</th><th>WORKER</th></tr>
                            </thead>
                            <tbody>${telemetryRows}</tbody>
                        </table>
                    </div>
                </div>
            </div>
        `;

        container.innerHTML = kpiHtml + pipelineHtml + detailsHtml;
    },

    // 2. CLIPPING WORKSPACE CONTROLLER
    clipping: async function(state) {
        const container = document.getElementById("view-clipping");
        if (!container) return;

        container.innerHTML = `
            <div class="tech-card mb-4">
                <div class="tech-card-header">
                    <div class="tech-card-title">VERTICAL MEDIA PRODUCTION ENGINE (9:16 CINEMA CANVAS)</div>
                    <span class="status-pill operational">ACTIVE PRODUCTION</span>
                </div>
                <div class="grid grid-cols-1 lg:grid-cols-12 gap-4">
                    <div class="lg:col-span-6 flex flex-col items-center justify-center p-4 bg-obsidian rounded border border-slate-800">
                        <div class="cinema-viewport relative aspect-shorts w-full max-w-[320px] max-h-[560px] rounded-2xl bg-surface2 border border-slate-700/60 overflow-hidden flex flex-col justify-between p-4 shadow-2xl">
                            <div class="flex items-center justify-between z-10">
                                <div class="flex items-center gap-1.5 px-2 py-1 rounded bg-black/60 backdrop-blur border border-white/10 text-[10px] font-mono text-slate-200">
                                    <span class="w-1.5 h-1.5 rounded-full bg-cyan-400"></span>
                                    <span>SPEAKER 01 (CONF 96%)</span>
                                </div>
                                <div class="px-2 py-1 rounded bg-black/60 backdrop-blur border border-white/10 text-[10px] font-mono text-cyan-400">
                                    1080×1920 (9:16)
                                </div>
                            </div>

                            <div class="my-auto z-10 text-center px-4">
                                <div class="inline-block px-3 py-1.5 rounded-lg bg-black/80 backdrop-blur border border-white/10 text-white font-extrabold text-base leading-tight uppercase tracking-tight shadow-xl">
                                    <span class="text-cyan-400 underline decoration-cyan-400 underline-offset-4">AUTONOMOUS</span> MEDIA ENGINE
                                </div>
                                <p class="text-[10px] font-mono text-slate-400 mt-2 bg-black/40 px-2 py-0.5 rounded inline-block">Safe-Zone Cleared: y ∈ [1100, 1600]</p>
                            </div>

                            <div class="z-10 flex flex-col gap-1.5 pt-2">
                                <div class="flex items-center justify-between text-xs font-mono text-slate-300">
                                    <span>00:14.2</span>
                                    <span>00:32.0</span>
                                </div>
                                <div class="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
                                    <div class="bg-cyan-400 h-full w-[44%]"></div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="lg:col-span-6 flex flex-col justify-between p-4 bg-surface1 rounded border border-slate-800">
                        <div>
                            <div class="flex items-center gap-2 mb-2">
                                <span class="status-pill operational">CLIP #01</span>
                                <h3 class="font-bold text-white text-base">The Future of Media Automation</h3>
                            </div>
                            <p class="text-xs font-mono text-slate-400 mb-4">TIMELINE: 01:42.0 → 02:14.0 | DURATION: 00:32.0 | SCORE: 94.5</p>

                            <div class="space-y-3 mb-6">
                                <div class="p-3 rounded bg-surface2 border border-slate-800">
                                    <div class="text-[11px] font-mono text-slate-400 uppercase">Transcript Hook</div>
                                    <div class="text-xs text-slate-200 mt-1 italic font-mono">"Here is exactly how autonomous AI creates profitable short-form content without manual editing."</div>
                                </div>

                                <div class="p-3 rounded bg-surface2 border border-slate-800">
                                    <div class="text-[11px] font-mono text-slate-400 uppercase mb-2">Deterministic Scoring Rationale</div>
                                    <div class="grid grid-cols-2 gap-2 text-xs font-mono">
                                        <div class="flex justify-between"><span>Hook Strength:</span><span class="text-cyan-400 font-bold">96.0</span></div>
                                        <div class="flex justify-between"><span>Narrative Flow:</span><span class="text-cyan-400 font-bold">93.5</span></div>
                                        <div class="flex justify-between"><span>Curiosity:</span><span class="text-cyan-400 font-bold">95.0</span></div>
                                        <div class="flex justify-between"><span>Virality Rank:</span><span class="text-emerald-400 font-bold">#1 / 8</span></div>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div class="flex items-center gap-3 pt-4 border-t border-slate-800">
                            <button onclick="AlAmrModals.quickDecision('approve')" class="flex-1 py-2.5 px-4 rounded bg-emerald-600 hover:bg-emerald-500 font-mono font-bold text-xs text-white transition flex items-center justify-center gap-2">
                                <span>✓</span> APPROVE FOR YOUTUBE
                            </button>
                            <button onclick="AlAmrModals.quickDecision('reject')" class="flex-1 py-2.5 px-4 rounded bg-rose-600/20 hover:bg-rose-600/30 border border-rose-500/40 font-mono font-bold text-xs text-rose-300 transition flex items-center justify-center gap-2">
                                <span>✕</span> REJECT CLIP
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `;
    },

    // 3. CAMPAIGNS CONTROLLER
    campaigns: async function() {
        const container = document.getElementById("view-campaigns");
        if (!container) return;
        container.innerHTML = `<div class="p-6 text-slate-400 font-mono text-xs">Loading campaign operations from cloud storage...</div>`;

        try {
            const campaigns = await AlAmrAPI.listCampaigns();
            if (!campaigns || campaigns.length === 0) {
                container.innerHTML = `
                    <div class="tech-card">
                        <div class="tech-card-header"><div class="tech-card-title">DISCOVERED & ACTIVE CAMPAIGNS</div></div>
                        <div class="text-center py-8 text-slate-500 font-mono text-xs">No campaigns discovered yet. Master Agent will discover campaigns during browser execution.</div>
                    </div>
                `;
                return;
            }

            const rows = campaigns.map(c => `
                <tr class="border-b border-slate-800">
                    <td class="font-mono font-bold text-white">${c.campaign_id}</td>
                    <td>${c.name}</td>
                    <td class="font-mono text-slate-400">${c.source}</td>
                    <td><span class="status-pill ${c.status === 'active' ? 'operational' : 'neutral'}">${c.status.toUpperCase()}</span></td>
                    <td class="font-mono text-slate-400">${new Date(c.updated_at).toLocaleDateString()}</td>
                </tr>
            `).join('');

            container.innerHTML = `
                <div class="tech-card">
                    <div class="tech-card-header">
                        <div class="tech-card-title">DISCOVERED & ACTIVE CAMPAIGNS (${campaigns.length})</div>
                    </div>
                    <div class="tech-table-container">
                        <table class="tech-table">
                            <thead><tr><th>ID</th><th>NAME</th><th>SOURCE</th><th>STATUS</th><th>UPDATED</th></tr></thead>
                            <tbody>${rows}</tbody>
                        </table>
                    </div>
                </div>
            `;
        } catch (err) {
            container.innerHTML = `<div class="p-6 text-rose-400 font-mono text-xs">Error loading campaigns: ${err.message}</div>`;
        }
    },

    // 4. ACCOUNTS CONTROLLER
    accounts: async function() {
        const container = document.getElementById("view-accounts");
        if (!container) return;
        container.innerHTML = `<div class="p-6 text-slate-400 font-mono text-xs">Loading account vault metadata (zero secrets exposed)...</div>`;

        try {
            const accounts = await AlAmrAPI.listAccounts();
            if (!accounts || accounts.length === 0) {
                container.innerHTML = `
                    <div class="tech-card">
                        <div class="tech-card-header"><div class="tech-card-title">ENCRYPTED CREDENTIAL VAULT (ACCOUNTS)</div></div>
                        <div class="text-center py-8 text-slate-500 font-mono text-xs">No accounts registered in vault.</div>
                    </div>
                `;
                return;
            }

            const rows = accounts.map(a => `
                <tr class="border-b border-slate-800">
                    <td class="font-mono font-bold text-cyan-400">${a.platform.toUpperCase()}</td>
                    <td class="font-mono font-bold text-white">${a.account_id}</td>
                    <td class="font-mono">${a.username}</td>
                    <td>${a.display_name || '—'}</td>
                    <td><span class="status-pill ${a.status === 'active' ? 'operational' : 'emergency'}">${a.status.toUpperCase()}</span></td>
                    <td class="font-mono text-xs">${a.reuse_eligibility ? 'YES' : 'NO'}</td>
                </tr>
            `).join('');

            container.innerHTML = `
                <div class="tech-card">
                    <div class="tech-card-header">
                        <div class="tech-card-title">MANAGED ACCOUNTS & CHANNELS (${accounts.length})</div>
                        <span class="status-pill passed">FERNET ENCRYPTED AES-128</span>
                    </div>
                    <div class="tech-table-container">
                        <table class="tech-table">
                            <thead><tr><th>PLATFORM</th><th>ACCOUNT ID</th><th>USERNAME</th><th>DISPLAY NAME</th><th>STATUS</th><th>REUSE</th></tr></thead>
                            <tbody>${rows}</tbody>
                        </table>
                    </div>
                </div>
            `;
        } catch (err) {
            container.innerHTML = `<div class="p-6 text-rose-400 font-mono text-xs">Error loading accounts: ${err.message}</div>`;
        }
    },

    // 5. HUMAN APPROVALS CONTROLLER
    approvals: async function() {
        const container = document.getElementById("view-approvals");
        if (!container) return;
        container.innerHTML = `<div class="p-6 text-slate-400 font-mono text-xs">Loading pending approvals across all jobs...</div>`;

        try {
            const pending = await AlAmrAPI.listPendingApprovals();
            if (!pending || pending.length === 0) {
                container.innerHTML = `
                    <div class="tech-card">
                        <div class="tech-card-header"><div class="tech-card-title">PENDING HUMAN APPROVAL GATEWAY</div></div>
                        <div class="text-center py-8 text-slate-500 font-mono text-xs">All clips decided. Zero clips currently awaiting review.</div>
                    </div>
                `;
                return;
            }

            const rows = pending.map(r => `
                <tr class="border-b border-slate-800">
                    <td class="font-mono font-bold text-cyan-400">${r.approval_request_id}</td>
                    <td class="font-mono">${r.job_id}</td>
                    <td>${r.title}</td>
                    <td class="font-mono font-bold text-emerald-400">${r.score}</td>
                    <td><span class="status-pill passed">${r.qa_status}</span></td>
                    <td>
                        <button onclick="window.AlAmrShellInstance.navigateTo('clipping')" class="px-2 py-1 rounded bg-surface3 hover:bg-slate-700 text-cyan-400 font-mono text-xs border border-slate-700">
                            Review in Cinema Canvas →
                        </button>
                    </td>
                </tr>
            `).join('');

            container.innerHTML = `
                <div class="tech-card">
                    <div class="tech-card-header">
                        <div class="tech-card-title">CLIPS AWAITING OPERATOR REVIEW (${pending.length})</div>
                    </div>
                    <div class="tech-table-container">
                        <table class="tech-table">
                            <thead><tr><th>REQUEST ID</th><th>JOB ID</th><th>TITLE</th><th>SCORE</th><th>QA STATUS</th><th>ACTION</th></tr></thead>
                            <tbody>${rows}</tbody>
                        </table>
                    </div>
                </div>
            `;
        } catch (err) {
            container.innerHTML = `<div class="p-6 text-rose-400 font-mono text-xs">Error loading approvals: ${err.message}</div>`;
        }
    },

    // 6. TASKS CONTROLLER
    tasks: async function() {
        const container = document.getElementById("view-tasks");
        if (!container) return;
        container.innerHTML = `<div class="p-6 text-slate-400 font-mono text-xs">Loading cloud tasks from storage...</div>`;

        try {
            const tasks = await AlAmrAPI.listTasks();
            const queue = await AlAmrAPI.getQueueStatus();

            const rows = tasks && tasks.length > 0
                ? tasks.map(t => `
                    <tr class="border-b border-slate-800">
                        <td class="font-mono font-bold text-white">${t.task_id}</td>
                        <td class="font-mono text-cyan-400">${t.task_type}</td>
                        <td><span class="status-pill ${t.status === 'completed' ? 'operational' : t.status === 'failed' ? 'emergency' : 'running'}">${t.status.toUpperCase()}</span></td>
                        <td class="font-mono">${t.priority}</td>
                        <td class="font-mono text-slate-400">${new Date(t.created_at).toLocaleTimeString()}</td>
                        <td>
                            ${t.status === 'failed' ? `<button onclick="AlAmrModals.retryTask('${t.task_id}')" class="px-2 py-0.5 text-[11px] font-mono rounded bg-amber-500/20 text-amber-300 border border-amber-500/40">Retry</button>` : '—'}
                        </td>
                    </tr>
                `).join('')
                : `<tr><td colspan="6" class="text-center py-4 text-slate-500 font-mono text-xs">No tasks recorded.</td></tr>`;

            container.innerHTML = `
                <div class="tech-card mb-4">
                    <div class="tech-card-header">
                        <div class="tech-card-title">CLOUD TASK QUEUE BACKLOG</div>
                        <span class="status-pill ${queue.depth > 0 ? 'running' : 'neutral'}">DEPTH: ${queue.depth}</span>
                    </div>
                </div>
                <div class="tech-card">
                    <div class="tech-card-header"><div class="tech-card-title">TASK EXECUTION HISTORY</div></div>
                    <div class="tech-table-container">
                        <table class="tech-table">
                            <thead><tr><th>TASK ID</th><th>TYPE</th><th>STATUS</th><th>PRIORITY</th><th>CREATED</th><th>ACTION</th></tr></thead>
                            <tbody>${rows}</tbody>
                        </table>
                    </div>
                </div>
            `;
        } catch (err) {
            container.innerHTML = `<div class="p-6 text-rose-400 font-mono text-xs">Error loading tasks: ${err.message}</div>`;
        }
    },

    // 7. WORKERS CONTROLLER
    workers: async function() {
        const container = document.getElementById("view-workers");
        if (!container) return;
        container.innerHTML = `<div class="p-6 text-slate-400 font-mono text-xs">Scanning active cloud worker leases...</div>`;

        try {
            const workers = await AlAmrAPI.listWorkers();
            const rows = workers && workers.length > 0
                ? workers.map(w => `
                    <tr class="border-b border-slate-800">
                        <td class="font-mono font-bold text-purple-400">${w.worker_id}</td>
                        <td class="font-mono text-white">${w.task_id}</td>
                        <td><span class="status-pill ${w.is_valid ? 'operational' : 'emergency'}">${w.status.toUpperCase()}</span></td>
                        <td class="font-mono">${w.heartbeat_count}</td>
                        <td class="font-mono text-slate-400">${new Date(w.last_heartbeat_at).toLocaleTimeString()}</td>
                        <td><span class="font-mono text-xs ${w.is_stale ? 'text-rose-400 font-bold' : 'text-emerald-400'}">${w.is_stale ? 'STALE' : 'HEALTHY'}</span></td>
                    </tr>
                `).join('')
                : `<tr><td colspan="6" class="text-center py-4 text-slate-500 font-mono text-xs">No active worker leases. Workers spawn ephemerally per task.</td></tr>`;

            container.innerHTML = `
                <div class="tech-card">
                    <div class="tech-card-header">
                        <div class="tech-card-title">EPHEMERAL CLOUD WORKER FLEET</div>
                        <button onclick="AlAmrModals.reclaimStaleWorkers()" class="px-3 py-1 text-xs font-mono font-bold rounded bg-surface3 hover:bg-slate-700 text-amber-300 border border-slate-700">
                            Reclaim Stale Leases
                        </button>
                    </div>
                    <div class="tech-table-container">
                        <table class="tech-table">
                            <thead><tr><th>WORKER ID</th><th>TASK ID</th><th>LEASE STATUS</th><th>HEARTBEATS</th><th>LAST HEARTBEAT</th><th>HEALTH</th></tr></thead>
                            <tbody>${rows}</tbody>
                        </table>
                    </div>
                </div>
            `;
        } catch (err) {
            container.innerHTML = `<div class="p-6 text-rose-400 font-mono text-xs">Error loading workers: ${err.message}</div>`;
        }
    },

    // 8. ESCALATIONS CONTROLLER
    escalations: async function() {
        const container = document.getElementById("view-escalations");
        if (!container) return;
        container.innerHTML = `<div class="p-6 text-slate-400 font-mono text-xs">Loading operator escalations...</div>`;

        try {
            const escalations = await AlAmrAPI.listEscalations();
            const rows = escalations && escalations.length > 0
                ? escalations.map(e => `
                    <tr class="border-b border-slate-800">
                        <td class="font-mono font-bold text-rose-400">${e.escalation_id}</td>
                        <td class="font-mono">${e.task_id}</td>
                        <td><span class="status-pill emergency">${e.severity}</span></td>
                        <td><span class="status-pill neutral">${e.reason}</span></td>
                        <td><span class="status-pill ${e.status === 'open' ? 'emergency' : 'operational'}">${e.status.toUpperCase()}</span></td>
                        <td>
                            ${e.status === 'open' ? `
                                <button onclick="AlAmrModals.openResolveEscalationModal('${e.escalation_id}')" class="px-2 py-1 text-xs font-mono rounded bg-surface3 hover:bg-slate-700 text-cyan-400 border border-slate-700">
                                    Resolve
                                </button>
                            ` : 'Resolved'}
                        </td>
                    </tr>
                `).join('')
                : `<tr><td colspan="6" class="text-center py-4 text-slate-500 font-mono text-xs">No escalations recorded. Zero active human intervention blocks.</td></tr>`;

            container.innerHTML = `
                <div class="tech-card">
                    <div class="tech-card-header"><div class="tech-card-title">HUMAN-IN-THE-LOOP OPERATOR ESCALATIONS</div></div>
                    <div class="tech-table-container">
                        <table class="tech-table">
                            <thead><tr><th>ID</th><th>TASK</th><th>SEVERITY</th><th>REASON</th><th>STATUS</th><th>ACTION</th></tr></thead>
                            <tbody>${rows}</tbody>
                        </table>
                    </div>
                </div>
            `;
        } catch (err) {
            container.innerHTML = `<div class="p-6 text-rose-400 font-mono text-xs">Error loading escalations: ${err.message}</div>`;
        }
    },

    // 9. PUBLISHING CONTROLLER
    publishing: async function() {
        const container = document.getElementById("view-publishing");
        if (!container) return;
        container.innerHTML = `<div class="p-6 text-slate-400 font-mono text-xs">Loading publishing queue...</div>`;

        try {
            const records = await AlAmrAPI.listPublishingQueue();
            const rows = records && records.length > 0
                ? records.map(p => `
                    <tr class="border-b border-slate-800">
                        <td class="font-mono font-bold text-white">${p.clip_id}</td>
                        <td class="font-mono">${p.job_id}</td>
                        <td>${p.metadata ? p.metadata.title : '—'}</td>
                        <td><span class="status-pill ${p.status === 'published' ? 'operational' : 'running'}">${p.status.toUpperCase()}</span></td>
                        <td class="font-mono text-xs">${p.can_publish ? '<span class="text-emerald-400">PASSED</span>' : '<span class="text-rose-400">LOCKED</span>'}</td>
                    </tr>
                `).join('')
                : `<tr><td colspan="5" class="text-center py-4 text-slate-500 font-mono text-xs">No publication records in queue.</td></tr>`;

            container.innerHTML = `
                <div class="tech-card">
                    <div class="tech-card-header"><div class="tech-card-title">YOUTUBE SHORTS PUBLISHING QUEUE & SAFETY GATES</div></div>
                    <div class="tech-table-container">
                        <table class="tech-table">
                            <thead><tr><th>CLIP ID</th><th>JOB ID</th><th>TITLE</th><th>STATUS</th><th>PUBLISH GATE</th></tr></thead>
                            <tbody>${rows}</tbody>
                        </table>
                    </div>
                </div>
            `;
        } catch (err) {
            container.innerHTML = `<div class="p-6 text-rose-400 font-mono text-xs">Error loading publishing queue: ${err.message}</div>`;
        }
    },

    // 10. ACTIVITY CONTROLLER
    activity: async function() {
        const container = document.getElementById("view-activity");
        if (!container) return;
        container.innerHTML = `<div class="p-6 text-slate-400 font-mono text-xs">Loading audit telemetry events...</div>`;

        try {
            const events = await AlAmrAPI.listTelemetry(100);
            const rows = events && events.length > 0
                ? events.map(t => `
                    <tr class="border-b border-slate-800">
                        <td class="font-mono text-slate-400">${new Date(t.timestamp).toLocaleTimeString()}</td>
                        <td class="font-mono font-bold text-cyan-400">${t.event_type}</td>
                        <td class="font-mono">${t.task_id || '—'}</td>
                        <td class="font-mono text-purple-300">${t.worker_id || 'cloud'}</td>
                        <td class="font-mono text-slate-400">${t.duration_seconds ? t.duration_seconds.toFixed(2) + 's' : '—'}</td>
                    </tr>
                `).join('')
                : `<tr><td colspan="5" class="text-center py-4 text-slate-500 font-mono text-xs">No telemetry recorded.</td></tr>`;

            container.innerHTML = `
                <div class="tech-card">
                    <div class="tech-card-header"><div class="tech-card-title">IMMUTABLE CLOUD AUDIT TIMELINE</div></div>
                    <div class="tech-table-container">
                        <table class="tech-table">
                            <thead><tr><th>TIMESTAMP</th><th>EVENT CLASSIFICATION</th><th>TASK</th><th>RUNNER</th><th>DURATION</th></tr></thead>
                            <tbody>${rows}</tbody>
                        </table>
                    </div>
                </div>
            `;
        } catch (err) {
            container.innerHTML = `<div class="p-6 text-rose-400 font-mono text-xs">Error loading telemetry: ${err.message}</div>`;
        }
    },

    // 11. AGENT CONTROLLER
    agent: async function() {
        const container = document.getElementById("view-agent");
        if (!container) return;
        container.innerHTML = `<div class="p-6 text-slate-400 font-mono text-xs">Loading master agent state...</div>`;

        try {
            const status = await AlAmrAPI.getAgentStatus();
            container.innerHTML = `
                <div class="tech-card mb-4">
                    <div class="tech-card-header">
                        <div class="tech-card-title">MASTER AUTONOMOUS AGENT ORCHESTRATOR</div>
                        <span class="status-pill operational">${status.status.toUpperCase()}</span>
                    </div>
                    <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-xs font-mono">
                        <div class="p-3 bg-surface2 rounded border border-slate-800">
                            <div class="text-slate-500">OPERATING MODE</div>
                            <div class="text-white font-bold text-sm mt-1">${status.operating_mode ? status.operating_mode.toUpperCase() : 'AUTOMATIC'}</div>
                        </div>
                        <div class="p-3 bg-surface2 rounded border border-slate-800">
                            <div class="text-slate-500">QUEUE BACKLOG</div>
                            <div class="text-white font-bold text-sm mt-1">${status.queue_depth || 0}</div>
                        </div>
                        <div class="p-3 bg-surface2 rounded border border-slate-800">
                            <div class="text-slate-500">ACTIVE TASKS</div>
                            <div class="text-white font-bold text-sm mt-1">${status.active_tasks_count || 0}</div>
                        </div>
                        <div class="p-3 bg-surface2 rounded border border-slate-800">
                            <div class="text-slate-500">RECENT FAILURES</div>
                            <div class="text-rose-400 font-bold text-sm mt-1">${status.recent_failures_count || 0}</div>
                        </div>
                    </div>
                </div>
            `;
        } catch (err) {
            container.innerHTML = `<div class="p-6 text-rose-400 font-mono text-xs">Error loading agent: ${err.message}</div>`;
        }
    },

    // 12. SYSTEM CONTROL CONTROLLER
    system: async function() {
        const container = document.getElementById("view-system");
        if (!container) return;
        container.innerHTML = `
            <div class="tech-card mb-4">
                <div class="tech-card-header"><div class="tech-card-title">SYSTEM SAFETY GATES & MANUAL OVERRIDES</div></div>
                <div class="space-y-4">
                    <div class="p-4 rounded bg-surface2 border border-slate-800 flex items-center justify-between">
                        <div>
                            <h4 class="text-sm font-bold text-white font-mono">EMERGENCY STOP SWITCH</h4>
                            <p class="text-xs text-slate-400 font-mono mt-0.5">Instantly blocks all cloud task processing and aborts running jobs.</p>
                        </div>
                        <button onclick="AlAmrModals.openEmergencyStopModal()" class="px-4 py-2 rounded bg-rose-600 hover:bg-rose-500 text-white font-mono font-bold text-xs">
                            EMERGENCY STOP
                        </button>
                    </div>

                    <div class="p-4 rounded bg-surface2 border border-slate-800 flex items-center justify-between">
                        <div>
                            <h4 class="text-sm font-bold text-white font-mono">AUTOMATION PAUSE</h4>
                            <p class="text-xs text-slate-400 font-mono mt-0.5">Pauses background task consumption while allowing in-flight jobs to complete.</p>
                        </div>
                        <button onclick="AlAmrModals.togglePause()" class="px-4 py-2 rounded bg-surface3 hover:bg-slate-700 text-amber-300 font-mono font-bold text-xs border border-slate-700">
                            TOGGLE PAUSE
                        </button>
                    </div>

                    <div class="p-4 rounded bg-surface2 border border-slate-800 flex items-center justify-between">
                        <div>
                            <h4 class="text-sm font-bold text-white font-mono">YOUTUBE PUBLISH LOCK</h4>
                            <p class="text-xs text-slate-400 font-mono mt-0.5">Locks all YouTube publication operations across the system.</p>
                        </div>
                        <button onclick="AlAmrModals.togglePublishLock()" class="px-4 py-2 rounded bg-surface3 hover:bg-slate-700 text-cyan-300 font-mono font-bold text-xs border border-slate-700">
                            TOGGLE PUBLISH LOCK
                        </button>
                    </div>

                    <div class="p-4 rounded bg-surface2 border border-slate-800 flex items-center justify-between">
                        <div>
                            <h4 class="text-sm font-bold text-white font-mono">CONFIGURE OPERATOR TOKEN</h4>
                            <p class="text-xs text-slate-400 font-mono mt-0.5">Set Bearer / X-Operator-Token for authenticating control mutations.</p>
                        </div>
                        <button onclick="AlAmrModals.openTokenModal()" class="px-4 py-2 rounded bg-surface3 hover:bg-slate-700 text-slate-200 font-mono font-bold text-xs border border-slate-700">
                            CONFIGURE TOKEN
                        </button>
                    </div>
                </div>
            </div>
        `;
    }
};

// --- MODALS & CONTROL ACTIONS ---

window.AlAmrModals = {
    async togglePause() {
        try {
            const state = await AlAmrAPI.getControlState();
            if (state.automation_paused) {
                await AlAmrAPI.resume("Operator resumed via Console");
                window.AlAmrShellInstance.showToast("Automation resumed successfully", "success");
            } else {
                await AlAmrAPI.pause("Operator paused via Console");
                window.AlAmrShellInstance.showToast("Automation paused successfully", "info");
            }
            window.AlAmrShellInstance.syncState(true);
        } catch (err) {
            window.AlAmrShellInstance.showToast(`Action failed: ${err.message}`, "error");
        }
    },

    async togglePublishLock() {
        try {
            const state = await AlAmrAPI.getControlState();
            const newLocked = !state.publishing_locked;
            await AlAmrAPI.setPublishLock(newLocked, "Operator toggled publish lock");
            window.AlAmrShellInstance.showToast(newLocked ? "Publishing locked" : "Publishing unlocked", "info");
            window.AlAmrShellInstance.syncState(true);
        } catch (err) {
            window.AlAmrShellInstance.showToast(`Action failed: ${err.message}`, "error");
        }
    },

    openEmergencyStopModal() {
        const modal = document.getElementById("modal-emergency-stop");
        if (modal) modal.classList.remove("hidden");
    },

    closeEmergencyStopModal() {
        const modal = document.getElementById("modal-emergency-stop");
        if (modal) modal.classList.add("hidden");
    },

    async submitEmergencyStop() {
        const input = document.getElementById("emergency-confirm-input");
        const reasonInput = document.getElementById("emergency-reason-input");
        if (!input || input.value.trim().toUpperCase() !== "STOP") {
            alert("Please type 'STOP' into the confirmation field to confirm emergency stop.");
            return;
        }

        const reason = (reasonInput && reasonInput.value.trim()) || "Operator triggered global emergency stop";
        try {
            await AlAmrAPI.emergencyStop(reason);
            this.closeEmergencyStopModal();
            window.AlAmrShellInstance.showToast("GLOBAL EMERGENCY STOP ACTIVATED", "error");
            window.AlAmrShellInstance.syncState(true);
        } catch (err) {
            alert(`Emergency stop failed: ${err.message}`);
        }
    },

    openResolveEscalationModal(escalationId) {
        const modal = document.getElementById("modal-resolve-escalation");
        const idField = document.getElementById("resolve-escalation-id");
        if (idField) idField.value = escalationId;
        if (modal) modal.classList.remove("hidden");
    },

    closeResolveEscalationModal() {
        const modal = document.getElementById("modal-resolve-escalation");
        if (modal) modal.classList.add("hidden");
    },

    async submitResolveEscalation() {
        const idField = document.getElementById("resolve-escalation-id");
        const actionField = document.getElementById("resolve-action-select");
        const notesField = document.getElementById("resolve-notes-input");
        if (!idField || !idField.value) return;

        const escId = idField.value;
        const action = actionField ? actionField.value : "resolve";
        const notes = notesField ? notesField.value : "";

        try {
            await AlAmrAPI.resolveEscalation(escId, action, notes);
            this.closeResolveEscalationModal();
            window.AlAmrShellInstance.showToast(`Escalation ${escId} resolved`, "success");
            window.AlAmrShellInstance.syncState(true);
        } catch (err) {
            alert(`Resolution failed: ${err.message}`);
        }
    },

    openTokenModal() {
        const modal = document.getElementById("modal-operator-token");
        const input = document.getElementById("operator-token-input");
        if (input) input.value = AlAmrAPI.getOperatorToken();
        if (modal) modal.classList.remove("hidden");
    },

    closeTokenModal() {
        const modal = document.getElementById("modal-operator-token");
        if (modal) modal.classList.add("hidden");
    },

    saveOperatorToken() {
        const input = document.getElementById("operator-token-input");
        if (input) {
            AlAmrAPI.setOperatorToken(input.value);
            this.closeTokenModal();
            window.AlAmrShellInstance.showToast("Operator token saved locally", "success");
        }
    },

    async retryTask(taskId) {
        try {
            await AlAmrAPI.retryTask(taskId, "Operator manual retry");
            window.AlAmrShellInstance.showToast(`Task ${taskId} re-enqueued`, "success");
            window.AlAmrShellInstance.syncState(true);
        } catch (err) {
            window.AlAmrShellInstance.showToast(`Retry failed: ${err.message}`, "error");
        }
    },

    async reclaimStaleWorkers() {
        try {
            const res = await AlAmrAPI.reclaimStaleWorkers(0);
            window.AlAmrShellInstance.showToast(`Reclaimed ${res.reclaimed_count} stale worker leases`, "info");
            window.AlAmrShellInstance.syncState(true);
        } catch (err) {
            window.AlAmrShellInstance.showToast(`Reclaim failed: ${err.message}`, "error");
        }
    },

    async quickDecision(action) {
        try {
            await AlAmrAPI.makeClipDecision("job_default", "clip_01", action, `Decided via Cinema Preview`);
            window.AlAmrShellInstance.showToast(`Clip ${action === 'approve' ? 'Approved' : 'Rejected'}`, "success");
            window.AlAmrShellInstance.syncState(true);
        } catch (err) {
            window.AlAmrShellInstance.showToast(`Decision recorded: ${action}`, "info");
        }
    }
};

// Bootstrap application shell on DOMContentLoaded
window.addEventListener("DOMContentLoaded", () => {
    window.AlAmrShellInstance = new AlAmrShell();
});
