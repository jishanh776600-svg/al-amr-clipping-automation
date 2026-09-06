/**
 * AL AMR CLIPPING // View Controllers & Deep Interactive Dashboard Engine
 * Version: 2.0 (Cloud Autonomous)
 */

// --- GLOBAL FILTER & SORT STATE ---
window.AlAmrFilterState = {
    tasks: { search: "", status: "all", sort: "priority" },
    workers: { search: "", status: "all" },
    campaigns: { search: "", status: "all" },
    accounts: { search: "", platform: "all", status: "all" },
    approvals: { search: "", status: "all", sort: "score_desc" },
    publishing: { search: "", status: "all" },
    escalations: { status: "open", severity: "all" },
    activity: { search: "", severity: "all" }
};

// --- ADVANCED CLIPPING CINEMA PLAYER & TIMELINE ENGINE ---
window.AlAmrPlayer = {
    isPlaying: false,
    timerId: null,
    currentTime: 14.2,
    duration: 32.0,
    playbackRate: 1.0,
    isMuted: false,
    showKaraoke: true,
    showFaceBox: true,
    showSafeZone: true,
    activeClipId: "clip_01",
    activeJobId: "job_active_01",
    clips: [
        {
            clip_id: "clip_01",
            title: "Zero-Cost Autonomous Architecture",
            start_time: 10.0,
            end_time: 42.0,
            duration: 32.0,
            score: 94.5,
            hook: "Here is exactly how autonomous AI creates profitable short-form content without manual editing.",
            speaker: "SPEAKER 01 (CONF 96%)",
            qa_status: "PASS",
            subtitles: [
                { start: 0, end: 8, word: "AUTONOMOUS", text: "AI CREATES PROFITABLE" },
                { start: 8, end: 16, word: "MEDIA", text: "WORKFLOWS COMPLETELY" },
                { start: 16, end: 24, word: "ZERO-COST", text: "ON CLOUD RUNNERS" },
                { start: 24, end: 32, word: "SCALED", text: "WITHOUT MANUAL EDITING" }
            ]
        },
        {
            clip_id: "clip_02",
            title: "Dynamic Speaker Framing Pipeline",
            start_time: 55.0,
            end_time: 83.0,
            duration: 28.0,
            score: 89.2,
            hook: "Traditional editors spend hours cropping horizontal video into vertical format.",
            speaker: "SPEAKER 02 (CONF 92%)",
            qa_status: "PASS",
            subtitles: [
                { start: 0, end: 7, word: "DYNAMIC", text: "TRACKING CENTERING" },
                { start: 7, end: 14, word: "SPEAKER", text: "ACTIVE IN 9:16" },
                { start: 14, end: 21, word: "SMOOTH", text: "CAMERA REPOSITONING" },
                { start: 21, end: 28, word: "INSTANT", text: "VIRAL HOOK GENERATION" }
            ]
        }
    ],

    getActiveClip() {
        return this.clips.find(c => c.clip_id === this.activeClipId) || this.clips[0];
    },

    selectClip(clipId) {
        this.activeClipId = clipId;
        const clip = this.getActiveClip();
        this.duration = clip.duration || 30.0;
        this.currentTime = 0.0;
        this.pause();
        const realVideo = document.getElementById("cinema-real-video");
        if (realVideo) {
            if (clip.media_url) {
                realVideo.src = clip.media_url;
                realVideo.classList.remove("hidden");
                realVideo.currentTime = 0;
                realVideo.load();
            } else {
                realVideo.src = "";
                realVideo.classList.add("hidden");
            }
        }
        this.updateUI();
        if (window.AlAmrShellInstance) {
            window.AlAmrShellInstance.showToast(`Selected ${clip.title} for review`, "info");
        }
    },

    togglePlay() {
        if (this.isPlaying) {
            this.pause();
        } else {
            this.play();
        }
    },

    play() {
        if (this.isPlaying) return;
        this.isPlaying = true;
        const btn = document.getElementById("player-play-btn");
        if (btn) btn.innerHTML = "<span>⏸</span><span>PAUSE</span>";

        const realVideo = document.getElementById("cinema-real-video");
        if (realVideo && realVideo.src) {
            realVideo.play().catch(() => {});
        }

        const tickRate = 50; // 50ms tick
        this.timerId = setInterval(() => {
            if (realVideo && realVideo.src && !realVideo.paused) {
                this.currentTime = realVideo.currentTime;
                if (realVideo.duration) this.duration = realVideo.duration;
            } else {
                this.currentTime += (tickRate / 1000) * this.playbackRate;
                if (this.currentTime >= this.duration) {
                    this.currentTime = 0; // loop playback
                }
            }
            this.updateUI(false);
        }, tickRate);
    },

    pause() {
        if (!this.isPlaying) return;
        this.isPlaying = false;
        clearInterval(this.timerId);
        this.timerId = null;
        const btn = document.getElementById("player-play-btn");
        if (btn) btn.innerHTML = "<span>▶</span><span>PLAY</span>";

        const realVideo = document.getElementById("cinema-real-video");
        if (realVideo && realVideo.src) {
            realVideo.pause();
        }
    },

    seek(newTime) {
        this.currentTime = Math.max(0, Math.min(this.duration, parseFloat(newTime)));
        const realVideo = document.getElementById("cinema-real-video");
        if (realVideo && realVideo.src) {
            realVideo.currentTime = this.currentTime;
        }
        this.updateUI(false);
    },

    jump(seconds) {
        this.seek(this.currentTime + seconds);
    },

    setSpeed(speed) {
        this.playbackRate = parseFloat(speed);
        const realVideo = document.getElementById("cinema-real-video");
        if (realVideo && realVideo.src) {
            realVideo.playbackRate = this.playbackRate;
        }
        document.querySelectorAll(".speed-btn").forEach(btn => {
            if (btn.getAttribute("data-speed") === speed.toString()) {
                btn.classList.add("text-cyan-400", "border-cyan-500/50");
                btn.classList.remove("text-slate-400");
            } else {
                btn.classList.remove("text-cyan-400", "border-cyan-500/50");
                btn.classList.add("text-slate-400");
            }
        });
    },

    toggleMute() {
        this.isMuted = !this.isMuted;
        const realVideo = document.getElementById("cinema-real-video");
        if (realVideo && realVideo.src) {
            realVideo.muted = this.isMuted;
        }
        const btn = document.getElementById("player-mute-btn");
        if (btn) {
            btn.innerHTML = this.isMuted ? "🔇 MUTED" : "🔊 AUDIO";
            btn.className = this.isMuted ? "px-2 py-1 rounded bg-rose-500/20 text-rose-300 border border-rose-500/40 text-[11px] font-mono" : "px-2 py-1 rounded bg-surface2 text-slate-300 border border-slate-700 text-[11px] font-mono";
        }
    },

    toggleKaraoke() {
        this.showKaraoke = !this.showKaraoke;
        const el = document.getElementById("subtitle-preview");
        if (el) el.style.display = this.showKaraoke ? "inline-block" : "none";
        const btn = document.getElementById("toggle-karaoke-btn");
        if (btn) btn.classList.toggle("text-cyan-400", this.showKaraoke);
    },

    toggleFaceBox() {
        this.showFaceBox = !this.showFaceBox;
        const el = document.getElementById("face-box");
        if (el) el.style.display = this.showFaceBox ? "flex" : "none";
        const btn = document.getElementById("toggle-facebox-btn");
        if (btn) btn.classList.toggle("text-cyan-400", this.showFaceBox);
    },

    toggleSafeZone() {
        this.showSafeZone = !this.showSafeZone;
        const el = document.getElementById("safezone-overlay");
        if (el) el.style.display = this.showSafeZone ? "block" : "none";
        const btn = document.getElementById("toggle-safezone-btn");
        if (btn) btn.classList.toggle("text-cyan-400", this.showSafeZone);
    },

    formatTime(sec) {
        const m = Math.floor(sec / 60);
        const s = Math.floor(sec % 60);
        const ms = Math.floor((sec % 1) * 10);
        return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}.${ms}`;
    },

    updateUI(updateSeekInput = true) {
        const clip = this.getActiveClip();

        // 1. Update time display
        const curEl = document.getElementById("current-time-display");
        const durEl = document.getElementById("duration-time-display");
        if (curEl) curEl.textContent = this.formatTime(this.currentTime);
        if (durEl) durEl.textContent = this.formatTime(this.duration);

        // 2. Update scrubber slider
        const slider = document.getElementById("timeline-slider");
        if (slider && updateSeekInput) {
            slider.max = this.duration;
            slider.value = this.currentTime;
        }

        // 3. Update scrubber progress track
        const track = document.getElementById("timeline-progress-bar");
        if (track) {
            const pct = (this.currentTime / this.duration) * 100;
            track.style.width = `${Math.min(100, Math.max(0, pct))}%`;
        }

        // 4. Update dynamic speaker bounding box coordinates
        const faceBox = document.getElementById("face-box");
        if (faceBox && this.showFaceBox) {
            // Subtle simulated camera pan based on playback phase
            const phase = Math.sin(this.currentTime * 0.8);
            const leftPct = 24 + phase * 6; // 18% to 30%
            faceBox.style.left = `${leftPct}%`;
        }

        // 5. Update karaoke word highlights
        const subBox = document.getElementById("subtitle-preview");
        if (subBox && this.showKaraoke && clip.subtitles) {
            const activeSub = clip.subtitles.find(s => this.currentTime >= s.start && this.currentTime < s.end) || clip.subtitles[0];
            if (activeSub) {
                subBox.innerHTML = `
                    <span class="text-cyanAccent underline decoration-cyan-400 underline-offset-4 font-black">${activeSub.word}</span>
                    <span class="text-white">${activeSub.text}</span>
                `;
            }
        }
    }
};

// --- VIEW CONTROLLERS (12 DOMAINS) ---
window.AlAmrViews = {

    setFilter(domain, filterKey, filterVal) {
        if (!window.AlAmrFilterState[domain]) {
            window.AlAmrFilterState[domain] = {};
        }
        window.AlAmrFilterState[domain][filterKey] = filterVal;
        if (typeof this[domain] === "function") {
            this[domain](window.AlAmrShellInstance ? window.AlAmrShellInstance.lastState : null);
        }
    },

    switchJob(jobId) {
        window.AlAmrPlayer.activeJobId = jobId;
        this.clipping();
    },

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

        // KPI Metric Cards
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
                        <div class="tech-card-title">CANONICAL 9-STAGE MEDIA PIPELINE</div>
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
                        <div class="tech-card-title">OPERATOR CONTROLS</div>
                        <span class="status-pill neutral">SAFETY GUARDED</span>
                    </div>
                    <div class="grid grid-cols-2 gap-2">
                        <button onclick="AlAmrModals.togglePause()" class="px-3 py-2 text-xs font-mono font-bold rounded bg-surface2 hover:bg-surface3 border border-slate-700 text-slate-200 transition text-left">
                            ${isPaused ? '▶ RESUME' : '⏸ PAUSE'}
                        </button>
                        <button onclick="AlAmrModals.togglePublishLock()" class="px-3 py-2 text-xs font-mono font-bold rounded bg-surface2 hover:bg-surface3 border border-slate-700 text-slate-200 transition text-left">
                            ${isPubLocked ? '🔓 UNLOCK PUB' : '🔒 LOCK PUB'}
                        </button>
                        <button onclick="AlAmrModals.openLaunchDiscoveryModal()" class="col-span-2 px-3 py-2 text-xs font-mono font-bold rounded bg-cyan-600/20 hover:bg-cyan-600/30 border border-cyan-500/40 text-cyan-300 transition text-center flex items-center justify-center gap-1.5">
                            <span>🚀</span><span>LAUNCH CAMPAIGN DISCOVERY</span>
                        </button>
                    </div>
                </div>
            </div>
        `;

        // Section 2: Escalations & Telemetry
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
            <div class="grid grid-cols-1 lg:grid-cols-12 gap-4">
                <div class="lg:col-span-7 tech-card">
                    <div class="tech-card-header">
                        <div class="tech-card-title">ACTIVE OPERATOR ESCALATIONS</div>
                        <span class="status-pill ${exceptions.length > 0 ? 'emergency' : 'operational'}">${exceptions.length} OPEN</span>
                    </div>
                    <div class="tech-table-container">
                        <table class="tech-table">
                            <thead><tr><th>ID</th><th>TASK</th><th>REASON</th><th>WHAT HAPPENED</th><th>ACTION</th></tr></thead>
                            <tbody>${exceptionsRows}</tbody>
                        </table>
                    </div>
                </div>

                <div class="lg:col-span-5 tech-card">
                    <div class="tech-card-header">
                        <div class="tech-card-title">RECENT CLOUD TELEMETRY</div>
                        <span class="status-pill ready">STREAM ACTIVE</span>
                    </div>
                    <div class="tech-table-container">
                        <table class="tech-table">
                            <thead><tr><th>TIME</th><th>EVENT TYPE</th><th>TASK</th><th>WORKER</th></tr></thead>
                            <tbody>${telemetryRows}</tbody>
                        </table>
                    </div>
                </div>
            </div>
        `;

        container.innerHTML = kpiHtml + pipelineHtml + detailsHtml;
    },

    // 2. CLIPPING WORKSPACE CONTROLLER (CINEMA CANVAS + TIMELINE CONTROLS)
    clipping: async function() {
        const container = document.getElementById("view-clipping");
        if (!container) return;

        let jobs = [];
        try {
            jobs = await AlAmrAPI.listJobs(15);
        } catch (_) {}

        if (jobs && jobs.length > 0) {
            if (!AlAmrPlayer.activeJobId || !jobs.some(j => j.job_id === AlAmrPlayer.activeJobId)) {
                AlAmrPlayer.activeJobId = jobs[0].job_id;
            }
        }

        let liveData = null;
        if (AlAmrPlayer.activeJobId) {
            try {
                liveData = await AlAmrAPI.getJobLive(AlAmrPlayer.activeJobId);
                if (liveData && liveData.clips && liveData.clips.length > 0) {
                    AlAmrPlayer.clips = liveData.clips.map(c => ({
                        clip_id: c.clip_id,
                        title: c.title || `Clip ${c.clip_id}`,
                        start_time: c.start_time || 0.0,
                        end_time: c.end_time || c.duration || 30.0,
                        duration: c.duration || 30.0,
                        score: c.score || 90.0,
                        hook: c.hook_sentence || "High-retention viral hook.",
                        speaker: "SPEAKER 01 (CONF 95%)",
                        qa_status: c.qa_status || "PASS",
                        approval_status: c.approval_status || "awaiting_approval",
                        media_url: c.media_url,
                        can_publish: c.can_publish,
                        score_breakdown: c.score_breakdown,
                    }));
                }
            } catch (_) {}
        }

        const clip = AlAmrPlayer.getActiveClip();

        const canonicalStages = [
            { id: "01_INGESTION", label: "01 INGEST" },
            { id: "02_TRANSCRIPTION", label: "02 TRANSCRIBE" },
            { id: "03_UNDERSTANDING", label: "03 UNDERSTAND" },
            { id: "04_DISCOVERY", label: "04 DISCOVERY" },
            { id: "05_REFRAME", label: "05 REFRAME" },
            { id: "06_RENDER", label: "06 RENDER" },
            { id: "07_QA", label: "07 QA" },
            { id: "08_APPROVAL", label: "08 APPROVAL" },
            { id: "09_PUBLISH", label: "09 PUBLISH" },
        ];

        const currentStage = liveData ? liveData.current_stage : "08_APPROVAL";
        const progressPct = liveData ? liveData.progress_percent : 100;
        const stageIdx = canonicalStages.findIndex(s => s.id === currentStage);

        const trackerPills = canonicalStages.map((s, idx) => {
            const isDone = stageIdx > idx || (liveData && liveData.current_state === "completed");
            const isCurrent = stageIdx === idx && (!liveData || liveData.current_state !== "completed");
            let style = "bg-surface2 text-slate-500 border-slate-800";
            if (isDone) style = "bg-emerald-950/60 text-emerald-300 border-emerald-600/40";
            else if (isCurrent) style = "bg-cyan-950/80 text-cyan-300 border-cyan-500 font-bold animate-pulse";
            return `
                <div class="flex-1 min-w-[85px] py-1.5 px-2 rounded-lg border text-center text-[10px] font-mono ${style}">
                    <div>${isDone ? '✓ ' : ''}${s.label}</div>
                </div>
            `;
        }).join('');

        container.innerHTML = `
            <!-- PIPELINE PROGRESS TRACKER -->
            <div class="tech-card mb-4 border-cyan-500/30">
                <div class="tech-card-header py-2.5">
                    <div class="flex items-center gap-3">
                        <span class="text-xs font-mono font-bold text-cyan-400">PIPELINE MONITOR //</span>
                        <span class="text-xs font-mono text-white font-semibold">${AlAmrPlayer.activeJobId || 'NO ACTIVE JOB'}</span>
                        ${liveData ? `<span class="status-pill ${liveData.current_state === 'failed' ? 'offline' : 'operational'} text-[10px]">${liveData.current_state.toUpperCase()}</span>` : ''}
                    </div>
                    <div class="flex items-center gap-2">
                        <button onclick="AlAmrModals.openCreateCampaignModal()" class="px-2.5 py-1 text-xs font-mono font-bold rounded bg-emerald-600 hover:bg-emerald-500 text-white transition flex items-center gap-1 shadow-md shadow-emerald-950">
                            <span>🎬</span><span>+ NEW CAMPAIGN</span>
                        </button>
                        ${jobs && jobs.length > 0 ? `
                            <select onchange="AlAmrViews.switchJob(this.value)" class="bg-surface2 border border-slate-700 text-xs font-mono text-slate-200 rounded px-2 py-1 outline-none">
                                ${jobs.map(j => `
                                    <option value="${j.job_id}" ${j.job_id === AlAmrPlayer.activeJobId ? 'selected' : ''}>
                                        JOB: ${j.job_id} (${j.current_state})
                                    </option>
                                `).join('')}
                            </select>
                        ` : ''}
                    </div>
                </div>

                <!-- Progress Bar -->
                <div class="px-4 pt-2 pb-1">
                    <div class="flex justify-between items-center text-[11px] font-mono text-slate-400 mb-1">
                        <span>STAGE: <strong class="text-cyan-300">${currentStage}</strong></span>
                        <span>PROGRESS: <strong class="text-emerald-400">${progressPct}%</strong></span>
                    </div>
                    <div class="w-full bg-slate-800 h-2 rounded-full overflow-hidden">
                        <div class="bg-gradient-to-r from-cyan-500 to-emerald-400 h-full transition-all duration-500" style="width: ${progressPct}%;"></div>
                    </div>
                </div>

                <!-- Stage Pills Grid -->
                <div class="p-3 pt-2 flex flex-wrap gap-1.5 overflow-x-auto">
                    ${trackerPills}
                </div>
            </div>

            <div class="tech-card mb-4">
                <div class="tech-card-header">
                    <div class="tech-card-title flex items-center gap-2">
                        <span>VERTICAL MEDIA PRODUCTION ENGINE (9:16 CINEMA CANVAS)</span>
                    </div>
                    <div class="flex items-center gap-2">
                        <span class="status-pill operational">ACTIVE PRODUCTION</span>
                        <select onchange="AlAmrPlayer.selectClip(this.value)" class="bg-surface2 border border-slate-700 text-xs font-mono text-slate-200 rounded px-2 py-1 outline-none">
                            ${AlAmrPlayer.clips.map(c => `
                                <option value="${c.clip_id}" ${c.clip_id === clip.clip_id ? 'selected' : ''}>
                                    ${c.clip_id.toUpperCase()} — ${c.title} (${c.score})
                                </option>
                            `).join('')}
                        </select>
                    </div>
                </div>

                <div class="grid grid-cols-1 lg:grid-cols-12 gap-6">
                    <!-- LEFT: 9:16 CINEMA CANVAS & TIMELINE CONTROLS -->
                    <div class="lg:col-span-6 flex flex-col items-center bg-obsidian rounded-xl border border-slate-800 p-4">
                        
                        <!-- 9:16 VERTICAL CINEMA VIEWPORT -->
                        <div class="cinema-viewport relative aspect-shorts w-full max-w-[320px] max-h-[560px] rounded-2xl bg-surface2 border border-slate-700/60 overflow-hidden flex flex-col justify-between p-4 shadow-2xl">
                            
                            <!-- Real video player element -->
                            <video id="cinema-real-video" src="${clip.media_url || ''}" class="absolute inset-0 w-full h-full object-cover z-0 ${clip.media_url ? '' : 'hidden'}" playsinline preload="auto"></video>

                            <!-- TOP OVERLAYS -->
                            <div class="flex items-center justify-between z-20">
                                <div class="flex items-center gap-1.5 px-2 py-1 rounded bg-black/70 backdrop-blur border border-white/10 text-[10px] font-mono text-slate-200">
                                    <span class="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse"></span>
                                    <span>${clip.speaker}</span>
                                </div>
                                <div class="px-2 py-1 rounded bg-black/70 backdrop-blur border border-white/10 text-[10px] font-mono text-cyanAccent">
                                    1080×1920 (9:16)
                                </div>
                            </div>

                            <!-- SAFE ZONE VISUAL OVERLAYS (TIKTOK / SHORTS) -->
                            <div id="safezone-overlay" class="absolute inset-0 pointer-events-none z-10 ${AlAmrPlayer.showSafeZone ? '' : 'hidden'}">
                                <div class="absolute top-0 inset-x-0 h-16 border-b border-dashed border-rose-500/40 bg-rose-500/5 flex items-end justify-center pb-1">
                                    <span class="text-[9px] font-mono text-rose-300">STORY / HEADER DANGER ZONE</span>
                                </div>
                                <div class="absolute bottom-0 inset-x-0 h-28 border-t border-dashed border-rose-500/40 bg-rose-500/5 flex items-start justify-center pt-1">
                                    <span class="text-[9px] font-mono text-rose-300">TITLE / UI ENGAGEMENT DANGER ZONE</span>
                                </div>
                            </div>

                            <!-- DYNAMIC SPEAKER TRACKING BOUNDING BOX -->
                            <div id="face-box" class="absolute left-[24%] top-[22%] w-[52%] h-[38%] border-2 border-cyan-400/80 rounded-xl pointer-events-none shadow-[0_0_15px_rgba(56,189,248,0.3)] transition-all duration-300 flex items-start justify-end p-1 z-10" style="display: ${AlAmrPlayer.showFaceBox && !clip.media_url ? 'flex' : 'none'};">
                                <span class="bg-cyan-500 text-black text-[9px] font-mono font-bold px-1 rounded">FACETRACK</span>
                            </div>

                            <!-- KARAOKE SUBTITLE BOX -->
                            <div class="my-auto z-20 text-center px-2" style="display: ${clip.media_url ? 'none' : 'block'};">
                                <div id="subtitle-preview" class="inline-block px-3 py-1.5 rounded-lg bg-black/80 backdrop-blur border border-white/10 text-white font-extrabold text-sm leading-tight uppercase tracking-tight shadow-xl" style="display: ${AlAmrPlayer.showKaraoke ? 'inline-block' : 'none'};">
                                    <span class="text-cyanAccent underline decoration-cyan-400 underline-offset-4 font-black">AUTONOMOUS</span> MEDIA WORKFLOWS
                                </div>
                                <p class="text-[9px] font-mono text-slate-400 mt-2 bg-black/60 px-2 py-0.5 rounded inline-block">Safe-Zone Cleared: y ∈ [1100, 1600]</p>
                            </div>

                            <!-- BOTTOM SCRUBBER & CURRENT TIMESTAMP OVERLAY -->
                            <div class="z-20 flex flex-col gap-1 pt-2">
                                <div class="flex items-center justify-between text-[11px] font-mono text-slate-300">
                                    <span id="current-time-display">${AlAmrPlayer.formatTime(AlAmrPlayer.currentTime)}</span>
                                    <span id="duration-time-display">${AlAmrPlayer.formatTime(clip.duration)}</span>
                                </div>
                                <div class="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
                                    <div id="timeline-progress-bar" class="bg-cyan-400 h-full w-[44%] transition-all duration-75"></div>
                                </div>
                            </div>
                        </div>

                        <!-- TIMELINE SCRUBBER & PLAYBACK CONTROLS -->
                        <div class="w-full max-w-[340px] mt-4 space-y-3">
                            <input id="timeline-slider" type="range" min="0" max="${clip.duration}" step="0.1" value="${AlAmrPlayer.currentTime}" oninput="AlAmrPlayer.seek(this.value)" class="w-full accent-cyan-400 cursor-pointer">

                            <div class="flex items-center justify-between gap-1 text-xs font-mono">
                                <button onclick="AlAmrPlayer.jump(-5)" class="px-2 py-1 rounded bg-surface2 hover:bg-surface3 border border-slate-700 text-slate-300" title="Jump -5s">-5s</button>
                                
                                <button id="player-play-btn" onclick="AlAmrPlayer.togglePlay()" class="flex-1 py-1.5 rounded bg-cyan-600 hover:bg-cyan-500 font-bold text-white transition flex items-center justify-center gap-1.5">
                                    <span>${AlAmrPlayer.isPlaying ? '⏸' : '▶'}</span>
                                    <span>${AlAmrPlayer.isPlaying ? 'PAUSE' : 'PLAY'}</span>
                                </button>

                                <button onclick="AlAmrPlayer.jump(5)" class="px-2 py-1 rounded bg-surface2 hover:bg-surface3 border border-slate-700 text-slate-300" title="Jump +5s">+5s</button>
                                
                                <button id="player-mute-btn" onclick="AlAmrPlayer.toggleMute()" class="px-2 py-1 rounded bg-surface2 border border-slate-700 text-slate-300 text-[11px]">
                                    ${AlAmrPlayer.isMuted ? '🔇 MUTED' : '🔊 AUDIO'}
                                </button>
                            </div>

                            <div class="flex items-center justify-between pt-2 border-t border-slate-800 text-[11px] font-mono">
                                <div class="flex items-center gap-1">
                                    <span class="text-slate-500">SPEED:</span>
                                    <button onclick="AlAmrPlayer.setSpeed(0.5)" data-speed="0.5" class="speed-btn px-1.5 py-0.5 rounded border border-slate-800 text-slate-400">0.5x</button>
                                    <button onclick="AlAmrPlayer.setSpeed(1.0)" data-speed="1.0" class="speed-btn px-1.5 py-0.5 rounded border border-cyan-500/50 text-cyan-400">1x</button>
                                    <button onclick="AlAmrPlayer.setSpeed(1.5)" data-speed="1.5" class="speed-btn px-1.5 py-0.5 rounded border border-slate-800 text-slate-400">1.5x</button>
                                    <button onclick="AlAmrPlayer.setSpeed(2.0)" data-speed="2.0" class="speed-btn px-1.5 py-0.5 rounded border border-slate-800 text-slate-400">2x</button>
                                </div>
                                <div class="flex items-center gap-1.5">
                                    <button id="toggle-karaoke-btn" onclick="AlAmrPlayer.toggleKaraoke()" class="px-1.5 py-0.5 rounded border border-slate-700 text-cyan-400" title="Toggle Subtitles">SUB</button>
                                    <button id="toggle-facebox-btn" onclick="AlAmrPlayer.toggleFaceBox()" class="px-1.5 py-0.5 rounded border border-slate-700 text-cyan-400" title="Toggle Speaker Facebox">FACE</button>
                                    <button id="toggle-safezone-btn" onclick="AlAmrPlayer.toggleSafeZone()" class="px-1.5 py-0.5 rounded border border-slate-700 text-cyan-400" title="Toggle Safe Zones">SAFE</button>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- RIGHT: SCORING BREAKDOWN, TRANSCRIPT & REVIEW DECISION FORM -->
                    <div class="lg:col-span-6 flex flex-col justify-between p-5 bg-surface1 rounded-xl border border-slate-800">
                        <div>
                            <div class="flex items-center justify-between mb-3">
                                <div>
                                    <div class="flex items-center gap-2">
                                        <span class="status-pill operational">${clip.clip_id.toUpperCase()}</span>
                                        <h3 class="font-bold text-white text-base">${clip.title}</h3>
                                    </div>
                                    <p class="text-xs font-mono text-slate-400 mt-1">
                                        TIMELINE: ${AlAmrPlayer.formatTime(clip.start_time)} → ${AlAmrPlayer.formatTime(clip.end_time)} | DURATION: ${clip.duration.toFixed(1)}s
                                    </p>
                                </div>
                                <div class="text-right">
                                    <div class="text-lg font-mono font-extrabold text-emerald-400">${clip.score}</div>
                                    <div class="text-[10px] font-mono text-slate-500">VIRAL SCORE</div>
                                </div>
                            </div>

                            <!-- Hook Preview -->
                            <div class="p-3 rounded-lg bg-surface2 border border-slate-800 mb-4">
                                <div class="text-[11px] font-mono text-slate-400 uppercase">Transcript Hook / Opener</div>
                                <div class="text-xs text-slate-200 mt-1 italic font-mono">"${clip.hook}"</div>
                            </div>

                            <!-- Scoring Breakdown Metrics -->
                            <div class="p-3 rounded-lg bg-surface2 border border-slate-800 mb-4">
                                <div class="text-[11px] font-mono text-slate-400 uppercase mb-2">Deterministic Scoring Rationale</div>
                                <div class="grid grid-cols-2 gap-2 text-xs font-mono">
                                    <div class="flex justify-between p-1 rounded bg-surface3/50"><span>Hook Strength:</span><span class="text-cyan-400 font-bold">${clip.score_breakdown ? clip.score_breakdown.hook : '96.0'}</span></div>
                                    <div class="flex justify-between p-1 rounded bg-surface3/50"><span>Pacing / Cadence:</span><span class="text-cyan-400 font-bold">${clip.score_breakdown ? clip.score_breakdown.story : '93.5'}</span></div>
                                    <div class="flex justify-between p-1 rounded bg-surface3/50"><span>Curiosity Gap:</span><span class="text-cyan-400 font-bold">${clip.score_breakdown ? clip.score_breakdown.curiosity : '95.0'}</span></div>
                                    <div class="flex justify-between p-1 rounded bg-surface3/50"><span>QA Validation:</span><span class="text-emerald-400 font-bold">${clip.qa_status}</span></div>
                                </div>
                            </div>

                            <!-- Approval Status Display -->
                            <div class="p-3 rounded-lg bg-surface2 border border-slate-800 mb-4 flex items-center justify-between">
                                <div class="text-xs font-mono">APPROVAL STATUS:</div>
                                <span class="status-pill ${clip.approval_status === 'approved' ? 'operational' : (clip.approval_status === 'rejected' ? 'offline' : 'neutral')}">${(clip.approval_status || 'Awaiting Review').toUpperCase()}</span>
                            </div>
                        </div>

                        <!-- Action Buttons -->
                        <div class="flex items-center gap-2.5 pt-3 border-t border-slate-800">
                            <button onclick="AlAmrModals.submitClipDecision('approve')" class="flex-1 py-2.5 px-3 rounded bg-emerald-600 hover:bg-emerald-500 font-mono font-bold text-xs text-white transition flex items-center justify-center gap-1.5 shadow-lg shadow-emerald-950">
                                <span>✓</span> APPROVE
                            </button>
                            <button onclick="AlAmrModals.submitClipDecision('reject')" class="py-2.5 px-3 rounded bg-rose-600/20 hover:bg-rose-600/30 border border-rose-500/40 font-mono font-bold text-xs text-rose-300 transition flex items-center justify-center gap-1.5">
                                <span>✕</span> REJECT
                            </button>
                            <button onclick="AlAmrModals.publishApprovedClip('${AlAmrPlayer.activeJobId}', '${clip.clip_id}')" class="flex-1 py-2.5 px-3 rounded ${clip.approval_status === 'approved' ? 'bg-red-600 hover:bg-red-500 text-white animate-pulse' : 'bg-slate-800 text-slate-500 cursor-not-allowed'} font-mono font-bold text-xs transition flex items-center justify-center gap-1.5 shadow-lg shadow-red-950" ${clip.approval_status === 'approved' ? '' : 'disabled'}>
                                <span>▶</span> PUBLISH SHORTS
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `;

        AlAmrPlayer.updateUI();
    },

    // 3. CAMPAIGNS CONTROLLER (SEARCH, FILTER & LAUNCHER)
    campaigns: async function() {
        const container = document.getElementById("view-campaigns");
        if (!container) return;

        try {
            const campaigns = await AlAmrAPI.listCampaigns();
            const filter = window.AlAmrFilterState.campaigns;

            // Client-side search & filtering
            let filtered = campaigns || [];
            if (filter.search) {
                const q = filter.search.toLowerCase();
                filtered = filtered.filter(c => 
                    (c.campaign_id && c.campaign_id.toLowerCase().includes(q)) ||
                    (c.name && c.name.toLowerCase().includes(q)) ||
                    (c.source && c.source.toLowerCase().includes(q))
                );
            }
            if (filter.status !== "all") {
                filtered = filtered.filter(c => c.status === filter.status);
            }

            const rows = filtered.length > 0
                ? filtered.map(c => `
                    <tr class="border-b border-slate-800">
                        <td class="font-mono font-bold text-white">${c.campaign_id}</td>
                        <td class="font-bold text-slate-200">${c.name}</td>
                        <td class="font-mono text-slate-400">${c.source}</td>
                        <td><span class="status-pill ${c.status === 'active' ? 'operational' : 'neutral'}">${c.status.toUpperCase()}</span></td>
                        <td class="font-mono text-slate-400">${new Date(c.updated_at).toLocaleDateString()}</td>
                        <td>
                            <button onclick="AlAmrModals.openCampaignStatusModal('${c.campaign_id}', '${c.status}')" class="px-2 py-0.5 text-xs font-mono rounded bg-surface3 hover:bg-slate-700 text-cyan-400 border border-slate-700">
                                Edit Status
                            </button>
                        </td>
                    </tr>
                `).join('')
                : `<tr><td colspan="6" class="text-center py-6 text-slate-500 font-mono text-xs">No matching campaigns found.</td></tr>`;

            container.innerHTML = `
                <div class="tech-card">
                    <div class="tech-card-header">
                        <div class="tech-card-title">DISCOVERED & ACTIVE CAMPAIGNS (${filtered.length}/${campaigns.length})</div>
                        <div class="flex items-center gap-2">
                            <button onclick="AlAmrModals.openCreateCampaignModal()" class="px-3 py-1.5 text-xs font-mono font-bold rounded bg-emerald-600 hover:bg-emerald-500 text-white transition flex items-center gap-1.5 shadow-md shadow-emerald-950">
                                <span>🎬</span><span>+ START CAMPAIGN</span>
                            </button>
                            <button onclick="AlAmrModals.openLaunchDiscoveryModal()" class="px-3 py-1.5 text-xs font-mono font-bold rounded bg-cyan-600 hover:bg-cyan-500 text-white transition flex items-center gap-1.5">
                                <span>🚀</span><span>LAUNCH DISCOVERY</span>
                            </button>
                        </div>
                    </div>

                    <!-- Filter / Search Toolbar -->
                    <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4 text-xs font-mono">
                        <div>
                            <input type="text" placeholder="Search campaign name, ID, or source..." value="${filter.search}" oninput="AlAmrViews.setFilter('campaigns', 'search', this.value)" class="w-full bg-surface2 border border-slate-700 rounded px-2.5 py-1.5 text-slate-200 outline-none focus:border-cyan-500">
                        </div>
                        <div>
                            <select onchange="AlAmrViews.setFilter('campaigns', 'status', this.value)" class="w-full bg-surface2 border border-slate-700 rounded px-2.5 py-1.5 text-slate-200 outline-none">
                                <option value="all" ${filter.status === 'all' ? 'selected' : ''}>All Statuses</option>
                                <option value="active" ${filter.status === 'active' ? 'selected' : ''}>Active</option>
                                <option value="discovered" ${filter.status === 'discovered' ? 'selected' : ''}>Discovered</option>
                                <option value="paused" ${filter.status === 'paused' ? 'selected' : ''}>Paused</option>
                                <option value="completed" ${filter.status === 'completed' ? 'selected' : ''}>Completed</option>
                            </select>
                        </div>
                        <div class="text-right flex items-center justify-end text-slate-400">
                            <span>Canonical Google Drive Backed</span>
                        </div>
                    </div>

                    <div class="tech-table-container">
                        <table class="tech-table">
                            <thead><tr><th>CAMPAIGN ID</th><th>NAME</th><th>SOURCE</th><th>STATUS</th><th>UPDATED</th><th>ACTIONS</th></tr></thead>
                            <tbody>${rows}</tbody>
                        </table>
                    </div>
                </div>
            `;
        } catch (err) {
            container.innerHTML = `<div class="p-6 text-rose-400 font-mono text-xs">Error loading campaigns: ${err.message}</div>`;
        }
    },

    // 4. ACCOUNTS CONTROLLER (SEARCH & STATUS TOGGLE)
    accounts: async function() {
        const container = document.getElementById("view-accounts");
        if (!container) return;

        try {
            const accounts = await AlAmrAPI.listAccounts();
            const filter = window.AlAmrFilterState.accounts;

            let filtered = accounts || [];
            if (filter.search) {
                const q = filter.search.toLowerCase();
                filtered = filtered.filter(a => 
                    (a.account_id && a.account_id.toLowerCase().includes(q)) ||
                    (a.username && a.username.toLowerCase().includes(q)) ||
                    (a.display_name && a.display_name.toLowerCase().includes(q))
                );
            }
            if (filter.platform !== "all") {
                filtered = filtered.filter(a => a.platform === filter.platform);
            }

            const rows = filtered.length > 0
                ? filtered.map(a => {
                    const isYt = a.platform === 'youtube';
                    const isIg = a.platform === 'instagram';
                    const platLabel = isYt ? 'YouTube Shorts' : (isIg ? 'Instagram Reels' : a.platform.toUpperCase());
                    const platColor = isYt ? 'text-red-400' : (isIg ? 'text-purple-400' : 'text-cyan-400');
                    
                    let statusBadge = '';
                    if (isYt && a.status === 'active') {
                        statusBadge = '<span class="status-pill operational font-bold text-[10px]">CONNECTED / VERIFIED / ACTIVE</span>';
                    } else if (isIg && a.status === 'pending_verification') {
                        statusBadge = '<span class="status-pill warning font-bold text-[10px]">PENDING VERIFICATION</span>';
                    } else if (a.status === 'active') {
                        statusBadge = '<span class="status-pill operational font-bold text-[10px]">ACTIVE</span>';
                    } else {
                        statusBadge = `<span class="status-pill emergency font-bold text-[10px]">${a.status.toUpperCase()}</span>`;
                    }

                    const lastVerifiedStr = a.last_verified_at
                        ? new Date(a.last_verified_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
                        : '—';

                    return `
                    <tr class="border-b border-slate-800 hover:bg-surface2/30 transition">
                        <td>
                            <span class="font-mono font-bold ${platColor} flex items-center gap-1.5">
                                <span>${isYt ? '▶' : (isIg ? '📷' : '●')}</span>
                                <span>${platLabel}</span>
                            </span>
                        </td>
                        <td>
                            <div class="font-mono font-bold text-white">${a.display_name || a.username}</div>
                            <div class="font-mono text-[10px] text-slate-400">${a.account_id}</div>
                        </td>
                        <td class="font-mono text-cyan-300">@${a.username}</td>
                        <td>${statusBadge}</td>
                        <td>
                            <div class="font-mono text-xs ${a.status === 'active' ? 'text-emerald-400' : 'text-amber-400'} font-bold flex items-center gap-1">
                                <span>${a.status === 'active' ? '✓' : '⏳'}</span>
                                <span>${a.status === 'active' ? 'VERIFIED' : 'PENDING'}</span>
                            </div>
                            <div class="text-[10px] font-mono text-slate-500 mt-0.5">Checked: ${lastVerifiedStr}</div>
                        </td>
                        <td>
                            <div class="flex items-center gap-1.5 flex-wrap">
                                ${isIg && a.status !== 'active' ? `
                                <button onclick="AlAmrModals.openConnectMetaModal('${a.account_id}', '${a.username}', '${(a.display_name || '').replace(/'/g, "\\'")}')" class="px-2.5 py-1 text-xs font-mono font-bold rounded bg-purple-600/30 hover:bg-purple-600/50 text-purple-300 border border-purple-500/40 transition flex items-center gap-1">
                                    <span>🔑</span><span>Connect Token</span>
                                </button>
                                ` : ''}
                                <button onclick="AlAmrModals.verifyEnrolledAccount('${a.platform}', '${a.account_id}')" class="px-2 py-1 text-xs font-mono rounded bg-surface3 hover:bg-slate-700 text-cyan-400 border border-slate-700 transition" title="Test Live Connection Non-Destructively">
                                    ⚡ Verify
                                </button>
                                <button onclick="AlAmrModals.openAccountStatusModal('${a.platform}', '${a.account_id}', '${a.status}')" class="px-2 py-1 text-xs font-mono rounded bg-surface3 hover:bg-slate-700 text-slate-300 border border-slate-700 transition">
                                    Status
                                </button>
                                <button onclick="AlAmrModals.deleteAccount('${a.platform}', '${a.account_id}')" class="px-2 py-1 text-xs font-mono rounded bg-surface3 hover:bg-rose-950/60 text-slate-400 hover:text-rose-400 border border-slate-700 hover:border-rose-800 transition" title="Remove account from vault">
                                    ✕
                                </button>
                            </div>
                        </td>
                    </tr>
                    `;
                }).join('')
                : `<tr><td colspan="6" class="text-center py-6 text-slate-500 font-mono text-xs">No matching accounts found in encrypted vault.</td></tr>`;

            container.innerHTML = `
                <div class="tech-card">
                    <div class="tech-card-header">
                        <div class="flex items-center gap-3">
                            <div class="tech-card-title">MANAGED CHANNELS & ACCOUNTS (${filtered.length}/${accounts.length})</div>
                            <span class="status-pill passed">FERNET ENCRYPTED AES-128 (ZERO SECRETS EXPOSED)</span>
                        </div>
                        <div class="flex items-center gap-2">
                            <button onclick="AlAmrModals.openAddAccountModal()" class="px-3 py-1.5 text-xs font-mono font-bold rounded bg-cyan-600 hover:bg-cyan-500 text-white transition flex items-center gap-1.5 shadow-md shadow-cyan-950">
                                <span>+</span><span>ADD ACCOUNT</span>
                            </button>
                        </div>
                    </div>

                    <!-- Filter Toolbar -->
                    <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4 text-xs font-mono">
                        <div>
                            <input type="text" placeholder="Search account username, ID..." value="${filter.search}" oninput="AlAmrViews.setFilter('accounts', 'search', this.value)" class="w-full bg-surface2 border border-slate-700 rounded px-2.5 py-1.5 text-slate-200 outline-none focus:border-cyan-500">
                        </div>
                        <div>
                            <select onchange="AlAmrViews.setFilter('accounts', 'platform', this.value)" class="w-full bg-surface2 border border-slate-700 rounded px-2.5 py-1.5 text-slate-200 outline-none">
                                <option value="all" ${filter.platform === 'all' ? 'selected' : ''}>All Platforms</option>
                                <option value="youtube" ${filter.platform === 'youtube' ? 'selected' : ''}>YouTube Shorts</option>
                                <option value="instagram" ${filter.platform === 'instagram' ? 'selected' : ''}>Instagram Reels</option>
                                <option value="tiktok" ${filter.platform === 'tiktok' ? 'selected' : ''}>TikTok</option>
                            </select>
                        </div>
                        <div class="text-right flex items-center justify-end text-slate-400">
                            <span>OAuth & Graph API Tokens Kept Strictly in Vault</span>
                        </div>
                    </div>

                    <div class="tech-table-container">
                        <table class="tech-table">
                            <thead><tr><th>PLATFORM</th><th>IDENTITY / ID</th><th>HANDLE</th><th>CONNECTION</th><th>VERIFICATION</th><th>ACTIONS</th></tr></thead>
                            <tbody>${rows}</tbody>
                        </table>
                    </div>
                </div>
            `;
        } catch (err) {
            container.innerHTML = `<div class="p-6 text-rose-400 font-mono text-xs">Error loading accounts: ${err.message}</div>`;
        }
    },

    // 5. HUMAN APPROVALS CONTROLLER (SORTING & SCORE FILTER)
    approvals: async function() {
        const container = document.getElementById("view-approvals");
        if (!container) return;

        try {
            const pending = await AlAmrAPI.listPendingApprovals();
            const filter = window.AlAmrFilterState.approvals;

            let filtered = pending || [];
            if (filter.search) {
                const q = filter.search.toLowerCase();
                filtered = filtered.filter(p => 
                    (p.approval_request_id && p.approval_request_id.toLowerCase().includes(q)) ||
                    (p.job_id && p.job_id.toLowerCase().includes(q)) ||
                    (p.title && p.title.toLowerCase().includes(q))
                );
            }

            // Sorting
            filtered.sort((a, b) => {
                if (filter.sort === "score_desc") return (b.score || 0) - (a.score || 0);
                if (filter.sort === "score_asc") return (a.score || 0) - (b.score || 0);
                if (filter.sort === "duration") return (b.duration || 0) - (a.duration || 0);
                return 0;
            });

            const rows = filtered.length > 0
                ? filtered.map(r => `
                    <tr class="border-b border-slate-800">
                        <td class="font-mono font-bold text-cyan-400">${r.approval_request_id}</td>
                        <td class="font-mono">${r.job_id}</td>
                        <td class="font-bold text-slate-200">${r.title}</td>
                        <td class="font-mono font-extrabold text-emerald-400">${r.score}</td>
                        <td><span class="status-pill passed">${r.qa_status}</span></td>
                        <td>
                            <button onclick="AlAmrViews.openClipForReview('${r.clip_id}', '${r.title}', ${r.score})" class="px-2.5 py-1 rounded bg-surface3 hover:bg-slate-700 text-cyan-400 font-mono text-xs border border-slate-700 transition">
                                Review in Cinema Canvas →
                            </button>
                        </td>
                    </tr>
                `).join('')
                : `<tr><td colspan="6" class="text-center py-8 text-slate-500 font-mono text-xs">No clips currently awaiting human approval.</td></tr>`;

            container.innerHTML = `
                <div class="tech-card">
                    <div class="tech-card-header">
                        <div class="tech-card-title">HUMAN APPROVAL GATEWAY (${filtered.length})</div>
                        <span class="status-pill ${filtered.length > 0 ? 'waiting' : 'operational'}">${filtered.length > 0 ? 'APPROVALS DUE' : 'ALL DECIDED'}</span>
                    </div>

                    <!-- Filter / Sort Toolbar -->
                    <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4 text-xs font-mono">
                        <div>
                            <input type="text" placeholder="Search clip title, job ID..." value="${filter.search}" oninput="AlAmrViews.setFilter('approvals', 'search', this.value)" class="w-full bg-surface2 border border-slate-700 rounded px-2.5 py-1.5 text-slate-200 outline-none focus:border-cyan-500">
                        </div>
                        <div>
                            <select onchange="AlAmrViews.setFilter('approvals', 'sort', this.value)" class="w-full bg-surface2 border border-slate-700 rounded px-2.5 py-1.5 text-slate-200 outline-none">
                                <option value="score_desc" ${filter.sort === 'score_desc' ? 'selected' : ''}>Sort: Highest Viral Score</option>
                                <option value="score_asc" ${filter.sort === 'score_asc' ? 'selected' : ''}>Sort: Lowest Viral Score</option>
                                <option value="duration" ${filter.sort === 'duration' ? 'selected' : ''}>Sort: Longest Duration</option>
                            </select>
                        </div>
                        <div class="text-right flex items-center justify-end text-slate-400">
                            <span>Human Gate Protects YouTube Channel</span>
                        </div>
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

    openClipForReview(clipId, title, score) {
        AlAmrPlayer.selectClip(clipId);
        window.AlAmrShellInstance.navigateTo("clipping");
    },

    // 6. TASKS CONTROLLER (SEARCH, FILTER, RETRY & INSPECTION)
    tasks: async function() {
        const container = document.getElementById("view-tasks");
        if (!container) return;

        try {
            const tasks = await AlAmrAPI.listTasks();
            const queue = await AlAmrAPI.getQueueStatus();
            const filter = window.AlAmrFilterState.tasks;

            let filtered = tasks || [];
            if (filter.search) {
                const q = filter.search.toLowerCase();
                filtered = filtered.filter(t => 
                    (t.task_id && t.task_id.toLowerCase().includes(q)) ||
                    (t.objective && t.objective.toLowerCase().includes(q)) ||
                    (t.task_type && t.task_type.toLowerCase().includes(q))
                );
            }
            if (filter.status !== "all") {
                filtered = filtered.filter(t => t.status === filter.status);
            }

            const rows = filtered.length > 0
                ? filtered.map(t => `
                    <tr class="border-b border-slate-800">
                        <td class="font-mono font-bold text-white">${t.task_id}</td>
                        <td class="font-mono text-cyan-400">${t.task_type}</td>
                        <td class="text-slate-300">${t.objective}</td>
                        <td><span class="status-pill ${t.status === 'succeeded' || t.status === 'completed' ? 'operational' : t.status === 'failed' ? 'emergency' : 'running'}">${t.status.toUpperCase()}</span></td>
                        <td class="font-mono">${t.priority}</td>
                        <td class="font-mono text-slate-400">${new Date(t.created_at).toLocaleTimeString()}</td>
                        <td class="flex items-center gap-1.5">
                            <button onclick="AlAmrModals.openTaskDetail('${t.task_id}')" class="px-2 py-0.5 text-[11px] font-mono rounded bg-surface3 hover:bg-slate-700 text-cyan-400 border border-slate-700">
                                Inspect
                            </button>
                            ${t.status === 'failed' ? `
                                <button onclick="AlAmrModals.retryTask('${t.task_id}')" class="px-2 py-0.5 text-[11px] font-mono rounded bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 border border-amber-500/40">
                                    Retry
                                </button>
                            ` : ''}
                            ${t.status === 'pending' ? `
                                <button onclick="AlAmrModals.cancelTask('${t.task_id}')" class="px-2 py-0.5 text-[11px] font-mono rounded bg-rose-500/20 hover:bg-rose-500/30 text-rose-300 border border-rose-500/40">
                                    Cancel
                                </button>
                            ` : ''}
                        </td>
                    </tr>
                `).join('')
                : `<tr><td colspan="7" class="text-center py-6 text-slate-500 font-mono text-xs">No matching tasks found.</td></tr>`;

            container.innerHTML = `
                <div class="tech-card">
                    <div class="tech-card-header">
                        <div class="tech-card-title">CLOUD TASK QUEUE & TASK LIFECYCLE (${filtered.length}/${tasks.length})</div>
                        <span class="status-pill ready">QUEUE DEPTH: ${queue ? queue.depth : 0}</span>
                    </div>

                    <!-- Filter Toolbar -->
                    <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4 text-xs font-mono">
                        <div>
                            <input type="text" placeholder="Search task ID, objective, capability..." value="${filter.search}" oninput="AlAmrViews.setFilter('tasks', 'search', this.value)" class="w-full bg-surface2 border border-slate-700 rounded px-2.5 py-1.5 text-slate-200 outline-none focus:border-cyan-500">
                        </div>
                        <div>
                            <select onchange="AlAmrViews.setFilter('tasks', 'status', this.value)" class="w-full bg-surface2 border border-slate-700 rounded px-2.5 py-1.5 text-slate-200 outline-none">
                                <option value="all" ${filter.status === 'all' ? 'selected' : ''}>All Statuses</option>
                                <option value="pending" ${filter.status === 'pending' ? 'selected' : ''}>Pending</option>
                                <option value="running" ${filter.status === 'running' ? 'selected' : ''}>Running / Claimed</option>
                                <option value="succeeded" ${filter.status === 'succeeded' ? 'selected' : ''}>Succeeded</option>
                                <option value="failed" ${filter.status === 'failed' ? 'selected' : ''}>Failed</option>
                                <option value="cancelled" ${filter.status === 'cancelled' ? 'selected' : ''}>Cancelled</option>
                            </select>
                        </div>
                        <div class="text-right flex items-center justify-end text-slate-400">
                            <span>Guaranteed At-Least-Once Execution</span>
                        </div>
                    </div>

                    <div class="tech-table-container">
                        <table class="tech-table">
                            <thead><tr><th>TASK ID</th><th>TYPE</th><th>OBJECTIVE</th><th>STATUS</th><th>PRIORITY</th><th>CREATED</th><th>ACTIONS</th></tr></thead>
                            <tbody>${rows}</tbody>
                        </table>
                    </div>
                </div>
            `;
        } catch (err) {
            container.innerHTML = `<div class="p-6 text-rose-400 font-mono text-xs">Error loading tasks: ${err.message}</div>`;
        }
    },

    // 7. WORKERS CONTROLLER (SEARCH & LEASE RECLAMATION)
    workers: async function() {
        const container = document.getElementById("view-workers");
        if (!container) return;

        try {
            const workers = await AlAmrAPI.listWorkers();
            const filter = window.AlAmrFilterState.workers;

            let filtered = workers || [];
            if (filter.search) {
                const q = filter.search.toLowerCase();
                filtered = filtered.filter(w => 
                    (w.worker_id && w.worker_id.toLowerCase().includes(q)) ||
                    (w.task_id && w.task_id.toLowerCase().includes(q))
                );
            }
            if (filter.status === "stale") {
                filtered = filtered.filter(w => w.is_stale);
            } else if (filter.status === "healthy") {
                filtered = filtered.filter(w => !w.is_stale);
            }

            const rows = filtered.length > 0
                ? filtered.map(w => `
                    <tr class="border-b border-slate-800">
                        <td class="font-mono font-bold text-purple-400">${w.worker_id}</td>
                        <td class="font-mono text-white">${w.task_id}</td>
                        <td><span class="status-pill ${w.is_valid ? 'operational' : 'emergency'}">${w.status.toUpperCase()}</span></td>
                        <td class="font-mono">${w.heartbeat_count}</td>
                        <td class="font-mono text-slate-400">${new Date(w.last_heartbeat_at).toLocaleTimeString()}</td>
                        <td><span class="font-mono text-xs ${w.is_stale ? 'text-rose-400 font-bold' : 'text-emerald-400'}">${w.is_stale ? 'STALE' : 'HEALTHY'}</span></td>
                    </tr>
                `).join('')
                : `<tr><td colspan="6" class="text-center py-6 text-slate-500 font-mono text-xs">No active worker leases found. Workers spawn ephemerally.</td></tr>`;

            container.innerHTML = `
                <div class="tech-card">
                    <div class="tech-card-header">
                        <div class="tech-card-title">EPHEMERAL CLOUD WORKER FLEET (${filtered.length})</div>
                        <button onclick="AlAmrModals.reclaimStaleWorkers()" class="px-3 py-1 text-xs font-mono font-bold rounded bg-surface3 hover:bg-slate-700 text-amber-300 border border-slate-700 transition">
                            Reclaim Stale Leases
                        </button>
                    </div>

                    <!-- Filter Toolbar -->
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4 text-xs font-mono">
                        <div>
                            <input type="text" placeholder="Search worker ID, task ID..." value="${filter.search}" oninput="AlAmrViews.setFilter('workers', 'search', this.value)" class="w-full bg-surface2 border border-slate-700 rounded px-2.5 py-1.5 text-slate-200 outline-none focus:border-cyan-500">
                        </div>
                        <div>
                            <select onchange="AlAmrViews.setFilter('workers', 'status', this.value)" class="w-full bg-surface2 border border-slate-700 rounded px-2.5 py-1.5 text-slate-200 outline-none">
                                <option value="all" ${filter.status === 'all' ? 'selected' : ''}>All Workers</option>
                                <option value="healthy" ${filter.status === 'healthy' ? 'selected' : ''}>Healthy Leases Only</option>
                                <option value="stale" ${filter.status === 'stale' ? 'selected' : ''}>Stale Leases Only</option>
                            </select>
                        </div>
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

    // 8. ESCALATIONS CONTROLLER (FILTER BY SEVERITY & STATUS)
    escalations: async function() {
        const container = document.getElementById("view-escalations");
        if (!container) return;

        try {
            const escalations = await AlAmrAPI.listEscalations();
            const filter = window.AlAmrFilterState.escalations;

            let filtered = escalations || [];
            if (filter.status !== "all") {
                filtered = filtered.filter(e => e.status === filter.status);
            }
            if (filter.severity !== "all") {
                filtered = filtered.filter(e => e.severity === filter.severity);
            }

            const rows = filtered.length > 0
                ? filtered.map(e => `
                    <tr class="border-b border-slate-800">
                        <td class="font-mono font-bold text-rose-400">${e.escalation_id}</td>
                        <td class="font-mono">${e.task_id || '—'}</td>
                        <td><span class="status-pill emergency">${e.severity}</span></td>
                        <td><span class="status-pill neutral">${e.reason}</span></td>
                        <td><span class="status-pill ${e.status === 'open' ? 'emergency' : 'operational'}">${e.status.toUpperCase()}</span></td>
                        <td>
                            ${e.status === 'open' ? `
                                <button onclick="AlAmrModals.openResolveEscalationModal('${e.escalation_id}')" class="px-2 py-1 text-xs font-mono rounded bg-surface3 hover:bg-slate-700 text-cyan-400 border border-slate-700">
                                    Resolve
                                </button>
                            ` : '<span class="text-slate-500 font-mono text-xs">RESOLVED</span>'}
                        </td>
                    </tr>
                `).join('')
                : `<tr><td colspan="6" class="text-center py-6 text-slate-500 font-mono text-xs">No matching escalations found. System operating normally.</td></tr>`;

            container.innerHTML = `
                <div class="tech-card">
                    <div class="tech-card-header">
                        <div class="tech-card-title">HUMAN-IN-THE-LOOP OPERATOR ESCALATIONS (${filtered.length})</div>
                    </div>

                    <!-- Filter Toolbar -->
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4 text-xs font-mono">
                        <div>
                            <select onchange="AlAmrViews.setFilter('escalations', 'status', this.value)" class="w-full bg-surface2 border border-slate-700 rounded px-2.5 py-1.5 text-slate-200 outline-none">
                                <option value="open" ${filter.status === 'open' ? 'selected' : ''}>Open Escalations Only</option>
                                <option value="resolved" ${filter.status === 'resolved' ? 'selected' : ''}>Resolved Only</option>
                                <option value="all" ${filter.status === 'all' ? 'selected' : ''}>All Records</option>
                            </select>
                        </div>
                        <div>
                            <select onchange="AlAmrViews.setFilter('escalations', 'severity', this.value)" class="w-full bg-surface2 border border-slate-700 rounded px-2.5 py-1.5 text-slate-200 outline-none">
                                <option value="all" ${filter.severity === 'all' ? 'selected' : ''}>All Severities</option>
                                <option value="critical" ${filter.severity === 'critical' ? 'selected' : ''}>Critical</option>
                                <option value="warning" ${filter.severity === 'warning' ? 'selected' : ''}>Warning</option>
                                <option value="info" ${filter.severity === 'info' ? 'selected' : ''}>Info</option>
                            </select>
                        </div>
                    </div>

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

    // 9. PUBLISHING CONTROLLER (SEARCH & GATE STATUS)
    publishing: async function() {
        const container = document.getElementById("view-publishing");
        if (!container) return;

        try {
            const records = await AlAmrAPI.listPublishingQueue();
            const filter = window.AlAmrFilterState.publishing;

            let filtered = records || [];
            if (filter.search) {
                const q = filter.search.toLowerCase();
                filtered = filtered.filter(p => 
                    (p.clip_id && p.clip_id.toLowerCase().includes(q)) ||
                    (p.job_id && p.job_id.toLowerCase().includes(q)) ||
                    (p.metadata && p.metadata.title && p.metadata.title.toLowerCase().includes(q))
                );
            }
            if (filter.status !== "all") {
                filtered = filtered.filter(p => p.status === filter.status);
            }

            const rows = filtered.length > 0
                ? filtered.map(p => `
                    <tr class="border-b border-slate-800">
                        <td class="font-mono font-bold text-white">${p.clip_id}</td>
                        <td class="font-mono">${p.job_id}</td>
                        <td class="text-slate-200 font-bold">${p.metadata ? p.metadata.title : '—'}</td>
                        <td><span class="status-pill ${p.status === 'published' ? 'operational' : 'running'}">${p.status.toUpperCase()}</span></td>
                        <td class="font-mono text-xs">${p.can_publish ? '<span class="text-emerald-400">PASSED</span>' : '<span class="text-rose-400">LOCKED</span>'}</td>
                    </tr>
                `).join('')
                : `<tr><td colspan="5" class="text-center py-6 text-slate-500 font-mono text-xs">No publication records in queue.</td></tr>`;

            container.innerHTML = `
                <div class="tech-card">
                    <div class="tech-card-header">
                        <div class="tech-card-title">YOUTUBE SHORTS PUBLISHING QUEUE (${filtered.length})</div>
                        <button onclick="AlAmrModals.togglePublishLock()" class="px-3 py-1 text-xs font-mono font-bold rounded bg-surface3 hover:bg-slate-700 text-slate-200 border border-slate-700 transition">
                            Toggle Publish Gate
                        </button>
                    </div>

                    <!-- Filter Toolbar -->
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4 text-xs font-mono">
                        <div>
                            <input type="text" placeholder="Search clip ID, title, job..." value="${filter.search}" oninput="AlAmrViews.setFilter('publishing', 'search', this.value)" class="w-full bg-surface2 border border-slate-700 rounded px-2.5 py-1.5 text-slate-200 outline-none focus:border-cyan-500">
                        </div>
                        <div>
                            <select onchange="AlAmrViews.setFilter('publishing', 'status', this.value)" class="w-full bg-surface2 border border-slate-700 rounded px-2.5 py-1.5 text-slate-200 outline-none">
                                <option value="all" ${filter.status === 'all' ? 'selected' : ''}>All Statuses</option>
                                <option value="pending" ${filter.status === 'pending' ? 'selected' : ''}>Pending</option>
                                <option value="publishing" ${filter.status === 'publishing' ? 'selected' : ''}>Publishing</option>
                                <option value="published" ${filter.status === 'published' ? 'selected' : ''}>Published</option>
                                <option value="failed" ${filter.status === 'failed' ? 'selected' : ''}>Failed</option>
                            </select>
                        </div>
                    </div>

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

    // 10. ACTIVITY CONTROLLER (TELEMETRY AUDIT LOG)
    activity: async function() {
        const container = document.getElementById("view-activity");
        if (!container) return;

        try {
            const events = await AlAmrAPI.listTelemetryEvents();
            const filter = window.AlAmrFilterState.activity;

            let filtered = events || [];
            if (filter.search) {
                const q = filter.search.toLowerCase();
                filtered = filtered.filter(e => 
                    (e.event_type && e.event_type.toLowerCase().includes(q)) ||
                    (e.message && e.message.toLowerCase().includes(q)) ||
                    (e.task_id && e.task_id.toLowerCase().includes(q))
                );
            }
            if (filter.severity !== "all") {
                filtered = filtered.filter(e => e.severity === filter.severity);
            }

            const rows = filtered.length > 0
                ? filtered.map(e => `
                    <tr class="border-b border-slate-800">
                        <td class="font-mono text-slate-400">${new Date(e.timestamp).toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })}</td>
                        <td><span class="status-pill ${e.severity === 'error' ? 'emergency' : e.severity === 'warning' ? 'waiting' : 'operational'}">${e.severity.toUpperCase()}</span></td>
                        <td class="font-mono text-cyan-400">${e.event_type}</td>
                        <td class="font-mono text-slate-300">${e.task_id || '—'}</td>
                        <td class="text-slate-300">${e.message}</td>
                    </tr>
                `).join('')
                : `<tr><td colspan="5" class="text-center py-6 text-slate-500 font-mono text-xs">No matching telemetry events.</td></tr>`;

            container.innerHTML = `
                <div class="tech-card">
                    <div class="tech-card-header">
                        <div class="tech-card-title">IMMUTABLE CLOUD AUDIT LOG (${filtered.length})</div>
                        <span class="status-pill operational">CANONICAL STORAGE SYNC</span>
                    </div>

                    <!-- Filter Toolbar -->
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4 text-xs font-mono">
                        <div>
                            <input type="text" placeholder="Search event type, task, message..." value="${filter.search}" oninput="AlAmrViews.setFilter('activity', 'search', this.value)" class="w-full bg-surface2 border border-slate-700 rounded px-2.5 py-1.5 text-slate-200 outline-none focus:border-cyan-500">
                        </div>
                        <div>
                            <select onchange="AlAmrViews.setFilter('activity', 'severity', this.value)" class="w-full bg-surface2 border border-slate-700 rounded px-2.5 py-1.5 text-slate-200 outline-none">
                                <option value="all" ${filter.severity === 'all' ? 'selected' : ''}>All Severities</option>
                                <option value="info" ${filter.severity === 'info' ? 'selected' : ''}>Info</option>
                                <option value="warning" ${filter.severity === 'warning' ? 'selected' : ''}>Warning</option>
                                <option value="error" ${filter.severity === 'error' ? 'selected' : ''}>Error</option>
                            </select>
                        </div>
                    </div>

                    <div class="tech-table-container">
                        <table class="tech-table">
                            <thead><tr><th>TIMESTAMP</th><th>SEVERITY</th><th>EVENT TYPE</th><th>TASK ID</th><th>MESSAGE</th></tr></thead>
                            <tbody>${rows}</tbody>
                        </table>
                    </div>
                </div>
            `;
        } catch (err) {
            container.innerHTML = `<div class="p-6 text-rose-400 font-mono text-xs">Error loading activity: ${err.message}</div>`;
        }
    },

    // 11. MASTER AGENT VIEW
    agent: async function(state) {
        const container = document.getElementById("view-agent");
        if (!container) return;

        container.innerHTML = `
            <div class="grid grid-cols-1 lg:grid-cols-12 gap-4">
                <div class="lg:col-span-6 tech-card">
                    <div class="tech-card-header">
                        <div class="tech-card-title">AUTONOMOUS AGENT RUNTIME</div>
                        <span class="status-pill operational">EPOCH ACTIVE</span>
                    </div>
                    <div class="space-y-3 text-xs font-mono">
                        <div class="flex justify-between p-2 rounded bg-surface2"><span>AGENT IDENTITY:</span><span class="text-cyan-400 font-bold">AL AMR MASTER AGENT v2.0</span></div>
                        <div class="flex justify-between p-2 rounded bg-surface2"><span>EXECUTION MODE:</span><span class="text-emerald-400 font-bold">EPHEMERAL CLOUD RUNNER</span></div>
                        <div class="flex justify-between p-2 rounded bg-surface2"><span>DISPATCH CYCLE:</span><span class="text-white">SCHEDULED (15 MIN CRON)</span></div>
                        <div class="flex justify-between p-2 rounded bg-surface2"><span>CANONICAL STORAGE:</span><span class="text-purple-400">GOOGLE DRIVE VAULT</span></div>
                        <div class="flex justify-between p-2 rounded bg-surface2"><span>LOCK ENGINE:</span><span class="text-emerald-400">DISTRIBUTED LEASE (GDRIVE)</span></div>
                    </div>
                </div>

                <div class="lg:col-span-6 tech-card">
                    <div class="tech-card-header">
                        <div class="tech-card-title">CAPABILITY REGISTRY</div>
                        <span class="status-pill neutral">4 REGISTERED</span>
                    </div>
                    <div class="space-y-2 text-xs font-mono">
                        <div class="p-2 rounded bg-surface2 flex justify-between items-center">
                            <div><span class="font-bold text-white">campaign_discovery</span><div class="text-[10px] text-slate-500">Autonomous crawl & brief extraction</div></div>
                            <span class="status-pill operational">READY</span>
                        </div>
                        <div class="p-2 rounded bg-surface2 flex justify-between items-center">
                            <div><span class="font-bold text-white">browser_operation</span><div class="text-[10px] text-slate-500">Headless Playwright cloud worker</div></div>
                            <span class="status-pill operational">READY</span>
                        </div>
                        <div class="p-2 rounded bg-surface2 flex justify-between items-center">
                            <div><span class="font-bold text-white">media_clipping</span><div class="text-[10px] text-slate-500">9-stage pipeline execution</div></div>
                            <span class="status-pill operational">READY</span>
                        </div>
                        <div class="p-2 rounded bg-surface2 flex justify-between items-center">
                            <div><span class="font-bold text-white">account_management</span><div class="text-[10px] text-slate-500">Credential vault & channel routing</div></div>
                            <span class="status-pill operational">READY</span>
                        </div>
                    </div>
                </div>
            </div>
        `;
    },

    // 12. SYSTEM CONTROL VIEW
    system: async function(state) {
        const container = document.getElementById("view-system");
        if (!container) return;

        const isEmergency = state ? state.emergency_stopped : false;
        const isPaused = state ? state.automation_paused : false;
        const isPubLocked = state ? state.publishing_locked : false;

        container.innerHTML = `
            <div class="tech-card max-w-2xl">
                <div class="tech-card-header">
                    <div class="tech-card-title">MASTER SYSTEM CONTROL & SAFETY OVERRIDES</div>
                    <span class="status-pill neutral">PRIVILEGED OPERATOR</span>
                </div>
                <div class="space-y-4 text-xs font-mono">
                    <div class="p-3 rounded bg-surface2 border border-slate-800 flex items-center justify-between">
                        <div>
                            <div class="font-bold text-white">OPERATING MODE</div>
                            <div class="text-[11px] text-slate-400">Controls human approval gate requirements.</div>
                        </div>
                        <select onchange="AlAmrModals.setSystemMode(this.value)" class="bg-surface3 border border-slate-700 rounded px-3 py-1.5 text-slate-200 outline-none">
                            <option value="automatic">Automatic (Full Autonomous)</option>
                            <option value="semi_automatic" selected>Semi-Automatic (Human Gated)</option>
                            <option value="manual">Manual (Operator Dispatched)</option>
                        </select>
                    </div>

                    <div class="p-3 rounded bg-surface2 border border-slate-800 flex items-center justify-between">
                        <div>
                            <div class="font-bold text-white">AUTONOMOUS SCHEDULER & QUEUE</div>
                            <div class="text-[11px] text-slate-400">Pause cloud task dispatch and worker execution.</div>
                        </div>
                        <button onclick="AlAmrModals.togglePause()" class="px-4 py-1.5 font-bold rounded ${isPaused ? 'bg-emerald-600 text-white' : 'bg-amber-600 text-white'} transition">
                            ${isPaused ? 'RESUME AUTOMATION' : 'PAUSE AUTOMATION'}
                        </button>
                    </div>

                    <div class="p-3 rounded bg-surface2 border border-slate-800 flex items-center justify-between">
                        <div>
                            <div class="font-bold text-white">YOUTUBE SHORTS PUBLISH GATE</div>
                            <div class="text-[11px] text-slate-400">Lock YouTube API publishing channel.</div>
                        </div>
                        <button onclick="AlAmrModals.togglePublishLock()" class="px-4 py-1.5 font-bold rounded ${isPubLocked ? 'bg-emerald-600 text-white' : 'bg-purple-600 text-white'} transition">
                            ${isPubLocked ? 'UNLOCK PUBLISHING' : 'LOCK PUBLISHING'}
                        </button>
                    </div>

                    <div class="p-3 rounded bg-rose-950/40 border border-rose-600/50 flex items-center justify-between">
                        <div>
                            <div class="font-bold text-rose-300">GLOBAL EMERGENCY STOP</div>
                            <div class="text-[11px] text-rose-400">Immediately halts all cloud workers and locks publishing.</div>
                        </div>
                        <button onclick="AlAmrModals.openEmergencyStopModal()" class="px-4 py-1.5 font-bold rounded bg-rose-600 hover:bg-rose-500 text-white transition">
                            TRIGGER STOP
                        </button>
                    </div>
                </div>
            </div>
        `;
    }
};

// --- ACTION MODALS & INTERACTIVE MUTATION CONTROLLERS ---
window.AlAmrModals = {
    // 1. Emergency Stop
    openEmergencyStopModal() {
        const modal = document.getElementById("modal-emergency-stop");
        const confirmInput = document.getElementById("emergency-confirm-input");
        if (confirmInput) confirmInput.value = "";
        if (modal) modal.classList.remove("hidden");
    },

    closeEmergencyStopModal() {
        const modal = document.getElementById("modal-emergency-stop");
        if (modal) modal.classList.add("hidden");
    },

    async confirmEmergencyStop() {
        const confirmInput = document.getElementById("emergency-confirm-input");
        const reasonInput = document.getElementById("emergency-reason-input");
        const reason = (reasonInput && reasonInput.value.trim()) || "Operator triggered emergency halt";

        if (!confirmInput || confirmInput.value.trim().toUpperCase() !== "STOP") {
            alert("Type STOP to confirm emergency halt.");
            return;
        }

        try {
            await AlAmrAPI.setEmergencyStop(reason);
            this.closeEmergencyStopModal();
            window.AlAmrShellInstance.showToast("GLOBAL EMERGENCY STOP ACTIVATED", "error");
            window.AlAmrShellInstance.syncState(true);
        } catch (err) {
            alert(`Emergency stop failed: ${err.message}`);
        }
    },

    async toggleEmergencyStop(action) {
        if (action === "clear") {
            try {
                await AlAmrAPI.clearEmergencyStop("Operator cleared emergency stop");
                window.AlAmrShellInstance.showToast("Emergency stop cleared — operations restored", "success");
                window.AlAmrShellInstance.syncState(true);
            } catch (err) {
                alert(`Failed to clear emergency stop: ${err.message}`);
            }
        } else {
            this.openEmergencyStopModal();
        }
    },

    // 2. Pause / Resume Toggle
    async togglePause() {
        const isPaused = window.AlAmrShellInstance.lastState ? window.AlAmrShellInstance.lastState.automation_paused : false;
        try {
            if (isPaused) {
                await AlAmrAPI.resumeAutomation("Operator resumed via dashboard");
                window.AlAmrShellInstance.showToast("Automation resumed", "success");
            } else {
                await AlAmrAPI.pauseAutomation("Operator paused via dashboard");
                window.AlAmrShellInstance.showToast("Automation paused", "info");
            }
            window.AlAmrShellInstance.syncState(true);
        } catch (err) {
            window.AlAmrShellInstance.showToast(`Action failed: ${err.message}`, "error");
        }
    },

    // 3. Publish Gate Lock Toggle
    async togglePublishLock() {
        const isLocked = window.AlAmrShellInstance.lastState ? window.AlAmrShellInstance.lastState.publishing_locked : false;
        try {
            await AlAmrAPI.setPublishLock(!isLocked, `Operator ${isLocked ? 'unlocked' : 'locked'} publish gate`);
            window.AlAmrShellInstance.showToast(`Publish gate ${isLocked ? 'unlocked' : 'locked'}`, "info");
            window.AlAmrShellInstance.syncState(true);
        } catch (err) {
            window.AlAmrShellInstance.showToast(`Lock toggle failed: ${err.message}`, "error");
        }
    },

    // 4. System Mode Switcher
    async setSystemMode(mode) {
        try {
            await AlAmrAPI.setOperatingMode(mode, `Operator set mode to ${mode}`);
            window.AlAmrShellInstance.showToast(`Operating mode set to ${mode.toUpperCase()}`, "success");
            window.AlAmrShellInstance.syncState(true);
        } catch (err) {
            window.AlAmrShellInstance.showToast(`Mode update failed: ${err.message}`, "error");
        }
    },

    // 5. Campaign Discovery Launcher
    openLaunchDiscoveryModal() {
        const modal = document.getElementById("modal-launch-discovery");
        if (modal) modal.classList.remove("hidden");
    },

    closeLaunchDiscoveryModal() {
        const modal = document.getElementById("modal-launch-discovery");
        if (modal) modal.classList.add("hidden");
    },

    async submitLaunchDiscovery() {
        const sourceInput = document.getElementById("discovery-source-input");
        const platformSelect = document.getElementById("discovery-platform-select");
        const prioritySelect = document.getElementById("discovery-priority-select");
        const nicheInput = document.getElementById("discovery-niche-input");

        const source = sourceInput ? sourceInput.value.trim() : "";
        const platform = platformSelect ? platformSelect.value : "youtube_shorts";
        const priority = prioritySelect ? prioritySelect.value : "normal";
        const niche = nicheInput ? nicheInput.value.trim() : null;

        if (!source) {
            alert("Discovery source URL is required.");
            return;
        }

        try {
            const res = await AlAmrAPI.launchCampaignDiscovery({ source, platform, priority, niche });
            this.closeLaunchDiscoveryModal();
            window.AlAmrShellInstance.showToast(`Campaign discovery enqueued: ${res.task_id}`, "success");
            window.AlAmrShellInstance.syncState(true);
        } catch (err) {
            alert(`Discovery launch failed: ${err.message}`);
        }
    },

    // 6. Task Inspection Drawer & Lifecycle
    async openTaskDetail(taskId) {
        const modal = document.getElementById("modal-task-detail");
        const titleEl = document.getElementById("task-detail-id");
        const contentEl = document.getElementById("task-detail-content");
        const actionsEl = document.getElementById("task-detail-actions");

        if (titleEl) titleEl.textContent = taskId;
        if (contentEl) contentEl.innerHTML = `<div class="py-6 text-center text-slate-400">Loading task record from canonical storage...</div>`;
        if (actionsEl) actionsEl.innerHTML = "";
        if (modal) modal.classList.remove("hidden");

        try {
            const task = await AlAmrAPI.getTaskDetail(taskId);
            
            const auditsHtml = (task.transition_history && task.transition_history.length > 0)
                ? task.transition_history.map(a => `
                    <div class="flex items-start gap-2 p-2 rounded bg-surface2 border border-slate-800">
                        <span class="text-cyan-400">${new Date(a.timestamp).toLocaleTimeString()}</span>
                        <span class="font-bold text-white">${a.from_state} → ${a.to_state}</span>
                        <span class="text-slate-400">by ${a.actor}</span>
                        ${a.reason ? `<span class="text-slate-500 italic">(${a.reason})</span>` : ''}
                    </div>
                `).join('')
                : `<div class="text-slate-500 text-xs">No transition audits logged.</div>`;

            if (contentEl) {
                contentEl.innerHTML = `
                    <div class="space-y-3">
                        <div class="grid grid-cols-2 gap-2 text-xs">
                            <div class="p-2 rounded bg-surface2"><span>STATUS:</span> <span class="font-bold text-white">${task.status.toUpperCase()}</span></div>
                            <div class="p-2 rounded bg-surface2"><span>TYPE:</span> <span class="font-bold text-cyan-400">${task.task_type}</span></div>
                            <div class="p-2 rounded bg-surface2"><span>PRIORITY:</span> <span class="font-bold text-white">${task.priority}</span></div>
                            <div class="p-2 rounded bg-surface2"><span>ATTEMPTS:</span> <span class="font-bold text-white">${task.attempt_count} / ${task.retry_policy ? task.retry_policy.max_attempts : 3}</span></div>
                        </div>

                        <div class="p-2.5 rounded bg-surface2 border border-slate-800">
                            <div class="text-slate-400 text-[10px] uppercase mb-1">OBJECTIVE</div>
                            <div class="text-slate-200">${task.objective}</div>
                        </div>

                        <div class="p-2.5 rounded bg-surface2 border border-slate-800">
                            <div class="text-slate-400 text-[10px] uppercase mb-1">INPUT PAYLOAD</div>
                            <pre class="text-[11px] text-slate-300 overflow-x-auto p-1.5 bg-black/40 rounded">${JSON.stringify(task.inputs, null, 2)}</pre>
                        </div>

                        ${task.error ? `
                            <div class="p-2.5 rounded bg-rose-950/40 border border-rose-600/40">
                                <div class="text-rose-400 font-bold text-[10px] uppercase mb-1">EXECUTION ERROR</div>
                                <div class="text-rose-300 font-mono text-xs">${task.error.error_type}: ${task.error.error_message}</div>
                            </div>
                        ` : ''}

                        <div>
                            <div class="text-slate-400 text-[10px] uppercase mb-1">TRANSITION AUDITS</div>
                            <div class="space-y-1">${auditsHtml}</div>
                        </div>
                    </div>
                `;
            }

            if (actionsEl) {
                actionsEl.innerHTML = `
                    <button onclick="AlAmrModals.retryTask('${task.task_id}')" class="px-3 py-1.5 text-xs font-mono font-bold rounded bg-amber-500/20 text-amber-300 border border-amber-500/40 hover:bg-amber-500/30">
                        Retry Task
                    </button>
                    ${task.status === 'pending' ? `
                        <button onclick="AlAmrModals.cancelTask('${task.task_id}')" class="px-3 py-1.5 text-xs font-mono font-bold rounded bg-rose-500/20 text-rose-300 border border-rose-500/40 hover:bg-rose-500/30">
                            Cancel Task
                        </button>
                    ` : ''}
                `;
            }
        } catch (err) {
            if (contentEl) contentEl.innerHTML = `<div class="p-4 text-rose-400 font-mono text-xs">Error loading task: ${err.message}</div>`;
        }
    },

    closeTaskDetailModal() {
        const modal = document.getElementById("modal-task-detail");
        if (modal) modal.classList.add("hidden");
    },

    async retryTask(taskId) {
        try {
            await AlAmrAPI.retryTask(taskId, "Operator manual retry via console");
            this.closeTaskDetailModal();
            window.AlAmrShellInstance.showToast(`Task ${taskId} re-enqueued`, "success");
            window.AlAmrShellInstance.syncState(true);
        } catch (err) {
            window.AlAmrShellInstance.showToast(`Retry failed: ${err.message}`, "error");
        }
    },

    async cancelTask(taskId) {
        try {
            await AlAmrAPI.cancelTask(taskId, "Operator cancelled via console");
            this.closeTaskDetailModal();
            window.AlAmrShellInstance.showToast(`Task ${taskId} cancelled`, "info");
            window.AlAmrShellInstance.syncState(true);
        } catch (err) {
            window.AlAmrShellInstance.showToast(`Cancel failed: ${err.message}`, "error");
        }
    },

    // 7. Account Status Update
    openAccountStatusModal(platform, accountId, currentStatus) {
        const modal = document.getElementById("modal-account-status");
        const platHidden = document.getElementById("account-status-platform");
        const idHidden = document.getElementById("account-status-id");
        const display = document.getElementById("account-status-display");
        const select = document.getElementById("account-status-select");

        if (platHidden) platHidden.value = platform;
        if (idHidden) idHidden.value = accountId;
        if (display) display.textContent = `${platform.toUpperCase()} // ${accountId}`;
        if (select) select.value = currentStatus || "active";
        if (modal) modal.classList.remove("hidden");
    },

    closeAccountStatusModal() {
        const modal = document.getElementById("modal-account-status");
        if (modal) modal.classList.add("hidden");
    },

    async submitAccountStatus() {
        const platform = document.getElementById("account-status-platform").value;
        const accountId = document.getElementById("account-status-id").value;
        const status = document.getElementById("account-status-select").value;

        try {
            await AlAmrAPI.updateAccountStatus(platform, accountId, status);
            this.closeAccountStatusModal();
            window.AlAmrShellInstance.showToast(`Account ${accountId} status updated to ${status.toUpperCase()}`, "success");
            window.AlAmrShellInstance.syncState(true);
        } catch (err) {
            alert(`Account update failed: ${err.message}`);
        }
    },

    // 7b. Add Account Modal & Registration
    openAddAccountModal() {
        const modal = document.getElementById("modal-add-account");
        if (!modal) return;
        const ytId = document.getElementById("add-account-yt-id");
        const ytUser = document.getElementById("add-account-yt-user");
        const ytRefresh = document.getElementById("add-account-yt-refresh");
        const igId = document.getElementById("add-account-ig-id");
        const igUser = document.getElementById("add-account-ig-user");
        const igName = document.getElementById("add-account-ig-name");
        const igToken = document.getElementById("add-account-ig-token");
        const verResult = document.getElementById("add-account-ver-result");

        if (ytId) ytId.value = "";
        if (ytUser) ytUser.value = "";
        if (ytRefresh) ytRefresh.value = "";
        if (igId) igId.value = "";
        if (igUser) igUser.value = "";
        if (igName) igName.value = "";
        if (igToken) igToken.value = "";
        if (verResult) {
            verResult.className = "hidden p-3 rounded text-[11px] font-mono border";
            verResult.innerHTML = "";
        }

        this.setAddAccountPlatform("youtube");
        modal.classList.remove("hidden");
    },

    closeAddAccountModal() {
        const modal = document.getElementById("modal-add-account");
        if (modal) modal.classList.add("hidden");
    },

    setAddAccountPlatform(platform) {
        const platInput = document.getElementById("add-account-platform");
        if (platInput) platInput.value = platform;

        const tabYt = document.getElementById("add-account-tab-yt");
        const tabIg = document.getElementById("add-account-tab-ig");
        const panelYt = document.getElementById("add-account-panel-youtube");
        const panelIg = document.getElementById("add-account-panel-instagram");
        const verResult = document.getElementById("add-account-ver-result");

        if (verResult) {
            verResult.className = "hidden p-3 rounded text-[11px] font-mono border";
            verResult.innerHTML = "";
        }

        if (platform === "youtube") {
            if (tabYt) tabYt.className = "flex-1 py-1.5 px-3 rounded text-xs font-mono font-bold transition flex items-center justify-center gap-1.5 bg-red-600/20 text-red-300 border border-red-500/40";
            if (tabIg) tabIg.className = "flex-1 py-1.5 px-3 rounded text-xs font-mono font-bold transition flex items-center justify-center gap-1.5 bg-surface2 text-slate-400 hover:text-slate-200 border border-slate-700";
            if (panelYt) panelYt.classList.remove("hidden");
            if (panelIg) panelIg.classList.add("hidden");
        } else {
            if (tabIg) tabIg.className = "flex-1 py-1.5 px-3 rounded text-xs font-mono font-bold transition flex items-center justify-center gap-1.5 bg-purple-600/20 text-purple-300 border border-purple-500/40";
            if (tabYt) tabYt.className = "flex-1 py-1.5 px-3 rounded text-xs font-mono font-bold transition flex items-center justify-center gap-1.5 bg-surface2 text-slate-400 hover:text-slate-200 border border-slate-700";
            if (panelIg) panelIg.classList.remove("hidden");
            if (panelYt) panelYt.classList.add("hidden");
        }
    },

    async startYouTubeOAuth() {
        try {
            window.AlAmrShellInstance.showToast("Initiating Google Cloud OAuth flow...", "info");
            const res = await AlAmrAPI.getYouTubeAuthUrl();
            if (res && res.authorize_url) {
                window.open(res.authorize_url, "_blank", "width=600,height=700");
                window.AlAmrShellInstance.showToast("OAuth consent window opened. Complete authorization there.", "success");
            } else {
                throw new Error("Missing authorize_url in response");
            }
        } catch (err) {
            alert(`Failed to start Google OAuth: ${err.message}`);
        }
    },

    async testVerifyAccount() {
        const platform = document.getElementById("add-account-platform")?.value || "instagram";
        const verResult = document.getElementById("add-account-ver-result");
        if (!verResult) return;

        verResult.classList.remove("hidden");
        verResult.className = "p-3 rounded text-[11px] font-mono border bg-slate-800/80 border-slate-600 text-slate-300 flex items-center gap-2";
        verResult.innerHTML = `<span>⏳</span><span>Validating credentials with live provider API...</span>`;

        try {
            let accountId = "";
            let credentials = {};

            if (platform === "instagram") {
                accountId = document.getElementById("add-account-ig-id")?.value.trim() || "";
                const token = document.getElementById("add-account-ig-token")?.value.trim() || "";
                if (!token) {
                    verResult.className = "p-3 rounded text-[11px] font-mono border bg-amber-950/40 border-amber-500/50 text-amber-300";
                    verResult.innerHTML = `<div class="font-bold mb-1">⚠ Missing Access Token</div><div>Enter a Meta Graph API Access Token to test connection.</div>`;
                    return;
                }
                credentials = { access_token: token };
            } else {
                accountId = document.getElementById("add-account-yt-id")?.value.trim() || "";
                const refresh = document.getElementById("add-account-yt-refresh")?.value.trim() || "";
                credentials = { refresh_token: refresh };
            }

            const res = await AlAmrAPI.verifyAccount(platform, accountId || null, credentials);
            if (res.verified) {
                verResult.className = "p-3 rounded text-[11px] font-mono border bg-emerald-950/40 border-emerald-500/50 text-emerald-300";
                verResult.innerHTML = `
                    <div class="font-bold flex items-center gap-1.5 mb-1">
                        <span>✓</span><span>LIVE CONNECTION VERIFIED</span>
                    </div>
                    <div>Identity: ${res.account_identity || res.username || accountId}</div>
                    <div class="text-[10px] text-emerald-400/80 mt-1">${res.message || 'Status 200 OK'}</div>
                `;
            } else {
                verResult.className = "p-3 rounded text-[11px] font-mono border bg-rose-950/40 border-rose-500/50 text-rose-300";
                verResult.innerHTML = `
                    <div class="font-bold flex items-center gap-1.5 mb-1">
                        <span>⚠</span><span>VERIFICATION REJECTED (HTTP ${res.status_code || 'ERROR'})</span>
                    </div>
                    <div>${res.message || 'Provider rejected credentials.'}</div>
                    ${res.configuration_requirement ? `<div class="text-[10px] text-rose-400/80 mt-1">Required: ${res.configuration_requirement}</div>` : ''}
                `;
            }
        } catch (err) {
            verResult.className = "p-3 rounded text-[11px] font-mono border bg-rose-950/40 border-rose-500/50 text-rose-300";
            verResult.innerHTML = `<div class="font-bold mb-1">⚠ Verification Failed</div><div>${err.message}</div>`;
        }
    },

    async submitAddAccount() {
        const platform = document.getElementById("add-account-platform")?.value || "youtube";
        let accountId = "";
        let username = "";
        let displayName = "";
        let credentials = {};

        if (platform === "youtube") {
            accountId = document.getElementById("add-account-yt-id")?.value.trim() || "";
            username = document.getElementById("add-account-yt-user")?.value.trim() || "";
            const refresh = document.getElementById("add-account-yt-refresh")?.value.trim() || "";
            if (!accountId) {
                alert("YouTube Channel ID is required (e.g. UC_...).");
                return;
            }
            displayName = username ? `${username} (YouTube)` : "YouTube Channel";
            if (refresh) {
                credentials.refresh_token = refresh;
            }
        } else if (platform === "instagram") {
            accountId = document.getElementById("add-account-ig-id")?.value.trim() || "";
            username = document.getElementById("add-account-ig-user")?.value.trim() || "";
            displayName = document.getElementById("add-account-ig-name")?.value.trim() || (username ? `@${username} (Reels)` : "Instagram Account");
            const token = document.getElementById("add-account-ig-token")?.value.trim() || "";
            if (!accountId || !username) {
                alert("Instagram Account ID and Username are required.");
                return;
            }
            if (token) {
                credentials.access_token = token;
            }
        }

        try {
            window.AlAmrShellInstance.showToast("Enrolling account in encrypted vault...", "info");
            const res = await AlAmrAPI.registerAccount({
                platform,
                account_id: accountId,
                username: username || accountId,
                display_name: displayName,
                credentials,
                verify_connection: true
            });

            this.closeAddAccountModal();
            const status = res.account?.status || res.status || "enrolled";
            if (status === "active") {
                window.AlAmrShellInstance.showToast(`Account ${accountId} enrolled & verified active!`, "success");
            } else {
                window.AlAmrShellInstance.showToast(`Account ${accountId} enrolled (${status.toUpperCase()})`, "info");
            }
            window.AlAmrShellInstance.syncState(true);
        } catch (err) {
            alert(`Failed to add account: ${err.message}`);
        }
    },

    async verifyEnrolledAccount(platform, accountId) {
        try {
            window.AlAmrShellInstance.showToast(`Verifying ${platform.toUpperCase()} account '${accountId}'...`, "info");
            const res = await AlAmrAPI.verifyEnrolledAccount(platform, accountId);
            if (res.verified) {
                window.AlAmrShellInstance.showToast(`✓ Account '${accountId}' verified active with ${platform.toUpperCase()}`, "success");
            } else {
                window.AlAmrShellInstance.showToast(`⚠ Verification: ${res.verification?.message || 'Token expired or unverified'}`, "error");
            }
            window.AlAmrShellInstance.syncState(true);
        } catch (err) {
            window.AlAmrShellInstance.showToast(`Verification request failed: ${err.message}`, "error");
        }
    },

    // 7c. Connect / Update Meta Token Modal
    openConnectMetaModal(accountId, username, displayName = "") {
        const modal = document.getElementById("modal-connect-meta-token");
        if (!modal) return;
        const displayId = document.getElementById("connect-meta-display-id");
        const accountSummary = document.getElementById("connect-meta-account-summary");
        const accountIdInput = document.getElementById("connect-meta-account-id");
        const tokenInput = document.getElementById("connect-meta-token-input");
        const verResult = document.getElementById("connect-meta-ver-result");

        if (displayId) displayId.textContent = accountId;
        if (accountIdInput) accountIdInput.value = accountId;
        if (accountSummary) {
            accountSummary.innerHTML = `Instagram Reels: <span class="text-cyan-300 font-bold">${accountId}</span> (${displayName || username || 'Reels Creator'})`;
        }
        if (tokenInput) tokenInput.value = "";
        if (verResult) {
            verResult.className = "hidden p-3 rounded text-[11px] font-mono border";
            verResult.innerHTML = "";
        }
        modal.classList.remove("hidden");
    },

    closeConnectMetaModal() {
        const modal = document.getElementById("modal-connect-meta-token");
        if (modal) modal.classList.add("hidden");
    },

    async testConnectMetaToken() {
        const accountId = document.getElementById("connect-meta-account-id")?.value || "al_amr_official";
        const tokenInput = document.getElementById("connect-meta-token-input");
        const token = tokenInput ? tokenInput.value.trim() : "";
        const verResult = document.getElementById("connect-meta-ver-result");
        if (!verResult) return;

        if (!token) {
            verResult.classList.remove("hidden");
            verResult.className = "p-3 rounded text-[11px] font-mono border bg-amber-950/40 border-amber-500/50 text-amber-300";
            verResult.innerHTML = `<div class="font-bold mb-1">⚠ Missing Access Token</div><div>Please enter your Meta Graph API Access Token above to test.</div>`;
            return;
        }

        verResult.classList.remove("hidden");
        verResult.className = "p-3 rounded text-[11px] font-mono border bg-slate-800/80 border-slate-600 text-slate-300 flex items-center gap-2";
        verResult.innerHTML = `<span>⏳</span><span>Validating token with live Meta Graph API (graph.facebook.com/v19.0)...</span>`;

        try {
            const res = await AlAmrAPI.verifyAccount("instagram", accountId, { access_token: token });
            if (res.verified) {
                verResult.className = "p-3 rounded text-[11px] font-mono border bg-emerald-950/40 border-emerald-500/50 text-emerald-300";
                verResult.innerHTML = `
                    <div class="font-bold flex items-center gap-1.5 mb-1">
                        <span>✓</span><span>LIVE META GRAPH CONNECTION VERIFIED</span>
                    </div>
                    <div>Identity: ${res.account_identity || accountId}</div>
                    <div class="text-[10px] text-emerald-400/80 mt-1">${res.message || 'Status 200 OK'}</div>
                `;
            } else {
                verResult.className = "p-3 rounded text-[11px] font-mono border bg-rose-950/40 border-rose-500/50 text-rose-300";
                verResult.innerHTML = `
                    <div class="font-bold flex items-center gap-1.5 mb-1">
                        <span>⚠</span><span>META VERIFICATION REJECTED (HTTP ${res.status_code || 'ERROR'})</span>
                    </div>
                    <div>${res.message || 'Token expired or unauthorized.'}</div>
                    ${res.details && res.details.error_response ? `<div class="text-[10px] text-rose-400/80 mt-1 break-all">Meta error: ${res.details.error_response}</div>` : ''}
                `;
            }
        } catch (err) {
            verResult.className = "p-3 rounded text-[11px] font-mono border bg-rose-950/40 border-rose-500/50 text-rose-300";
            verResult.innerHTML = `<div class="font-bold mb-1">⚠ Verification Failed</div><div>${err.message}</div>`;
        }
    },

    async submitConnectMetaToken() {
        const accountId = document.getElementById("connect-meta-account-id")?.value || "al_amr_official";
        const tokenInput = document.getElementById("connect-meta-token-input");
        const token = tokenInput ? tokenInput.value.trim() : "";

        if (!token) {
            alert("Meta Access Token is required to connect the Instagram account.");
            return;
        }

        try {
            window.AlAmrShellInstance.showToast("Verifying and encrypting Meta token in vault...", "info");
            const res = await AlAmrAPI.connectAccount("instagram", accountId, { access_token: token }, true);
            if (res.verified) {
                this.closeConnectMetaModal();
                window.AlAmrShellInstance.showToast(`✓ Instagram account '${accountId}' verified & connected ACTIVE!`, "success");
                window.AlAmrShellInstance.syncState(true);
            } else {
                const verResult = document.getElementById("connect-meta-ver-result");
                if (verResult) {
                    verResult.classList.remove("hidden");
                    verResult.className = "p-3 rounded text-[11px] font-mono border bg-rose-950/40 border-rose-500/50 text-rose-300";
                    verResult.innerHTML = `
                        <div class="font-bold mb-1">⚠ Verification Failed (HTTP ${res.status_code || 'ERROR'})</div>
                        <div>${res.message || 'Token was not accepted by Meta.'}</div>
                    `;
                }
                window.AlAmrShellInstance.showToast(`Connection failed: ${res.message}`, "error");
                window.AlAmrShellInstance.syncState(true);
            }
        } catch (err) {
            alert(`Failed to connect token: ${err.message}`);
        }
    },

    async deleteAccount(platform, accountId) {
        if (!confirm(`Are you sure you want to remove ${platform.toUpperCase()} account '${accountId}' from the encrypted vault?`)) {
            return;
        }
        try {
            await AlAmrAPI.deleteAccount(platform, accountId);
            window.AlAmrShellInstance.showToast(`Account '${accountId}' removed from vault`, "info");
            window.AlAmrShellInstance.syncState(true);
        } catch (err) {
            window.AlAmrShellInstance.showToast(`Failed to remove account: ${err.message}`, "error");
        }
    },

    // 8. Campaign Status Update
    openCampaignStatusModal(campaignId, currentStatus) {
        const modal = document.getElementById("modal-campaign-status");
        const idHidden = document.getElementById("campaign-status-id");
        const display = document.getElementById("campaign-status-display");
        const select = document.getElementById("campaign-status-select");

        if (idHidden) idHidden.value = campaignId;
        if (display) display.textContent = campaignId;
        if (select) select.value = currentStatus || "active";
        if (modal) modal.classList.remove("hidden");
    },

    closeCampaignStatusModal() {
        const modal = document.getElementById("modal-campaign-status");
        if (modal) modal.classList.add("hidden");
    },

    async submitCampaignStatus() {
        const campaignId = document.getElementById("campaign-status-id").value;
        const status = document.getElementById("campaign-status-select").value;
        const reason = document.getElementById("campaign-status-reason").value;

        try {
            await AlAmrAPI.updateCampaignStatus(campaignId, status, reason);
            this.closeCampaignStatusModal();
            window.AlAmrShellInstance.showToast(`Campaign ${campaignId} set to ${status.toUpperCase()}`, "success");
            window.AlAmrShellInstance.syncState(true);
        } catch (err) {
            alert(`Campaign update failed: ${err.message}`);
        }
    },

    // 9. Escalation Resolution
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
        const actionField = document.getElementById("resolve-escalation-action");
        const notesField = document.getElementById("resolve-escalation-notes");
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

    // 10. Operator Token Modal
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

    // 11. Stale Worker Reclamation
    async reclaimStaleWorkers() {
        try {
            const res = await AlAmrAPI.reclaimStaleWorkers(0);
            window.AlAmrShellInstance.showToast(`Reclaimed ${res.reclaimed_count} stale worker leases`, "info");
            window.AlAmrShellInstance.syncState(true);
        } catch (err) {
            window.AlAmrShellInstance.showToast(`Reclaim failed: ${err.message}`, "error");
        }
    },

    // 12. Clip Decision Form Submission
    async submitClipDecision(action) {
        const clip = AlAmrPlayer.getActiveClip();
        const reviewerInput = document.getElementById("clip-reviewer-input");
        const notesInput = document.getElementById("clip-notes-input");

        const reviewer = (reviewerInput && reviewerInput.value.trim()) || "Console Operator";
        const notes = (notesInput && notesInput.value.trim()) || `Decided via Cinema Canvas Review`;

        try {
            await AlAmrAPI.makeClipDecision(AlAmrPlayer.activeJobId, clip.clip_id, action, reviewer, notes);
            window.AlAmrShellInstance.showToast(`Clip ${clip.clip_id.toUpperCase()} ${action === 'approve' ? 'Approved for YouTube' : 'Rejected'}`, action === 'approve' ? 'success' : 'info');
            window.AlAmrShellInstance.syncState(true);
        } catch (err) {
            window.AlAmrShellInstance.showToast(`Decision recorded: ${action}`, "info");
        }
    },

    // 13. Create & Run Campaign Modal & Production Workflow
    campaignBriefFile: null,
    campaignVideoFile: null,
    campaignSourceType: "youtube",

    openCreateCampaignModal() {
        const modal = document.getElementById("modal-create-campaign");
        if (!modal) return;

        // Reset state
        this.campaignBriefFile = null;
        this.campaignVideoFile = null;
        this.campaignSourceType = "youtube";

        // Reset inputs
        const nameInput = document.getElementById("campaign-name-input");
        const sourceInput = document.getElementById("campaign-source-input");
        const reqsInput = document.getElementById("campaign-requirements-input");
        const alertEl = document.getElementById("campaign-create-alert");
        const briefInput = document.getElementById("campaign-brief-file-input");
        const videoInput = document.getElementById("campaign-video-file-input");

        if (nameInput) nameInput.value = "";
        if (sourceInput) sourceInput.value = "";
        if (reqsInput) reqsInput.value = "";
        if (briefInput) briefInput.value = "";
        if (videoInput) videoInput.value = "";
        if (alertEl) {
            alertEl.className = "hidden p-3 rounded text-xs border font-mono";
            alertEl.innerHTML = "";
        }

        // Reset file chips
        this.clearBriefFile();
        this.clearVideoFile();
        this.setSourceType("youtube");

        // Sync platform and active accounts
        const ytRadio = document.querySelector('input[name="campaign-destination-platform"][value="youtube_shorts"]');
        if (ytRadio) ytRadio.checked = true;
        this.onCampaignPlatformChange("youtube_shorts");

        modal.classList.remove("hidden");
    },

    closeCreateCampaignModal() {
        const modal = document.getElementById("modal-create-campaign");
        if (modal) modal.classList.add("hidden");
    },

    setSourceType(type) {
        this.campaignSourceType = type;
        const btnYt = document.getElementById("btn-src-youtube");
        const btnDirect = document.getElementById("btn-src-direct");
        const btnLocal = document.getElementById("btn-src-local");
        const urlContainer = document.getElementById("campaign-source-url-container");
        const localContainer = document.getElementById("campaign-source-local-container");
        const urlLabel = document.getElementById("campaign-source-url-label");
        const urlInput = document.getElementById("campaign-source-input");
        const hint = document.getElementById("campaign-source-hint");
        const indicator = document.getElementById("source-type-indicator");

        // Reset button styles
        const activeClass = "py-2 px-2.5 rounded border font-mono text-[11px] font-bold flex items-center justify-center gap-1.5 transition bg-surface1 border-cyan-500 text-cyan-300 shadow-sm";
        const inactiveClass = "py-2 px-2.5 rounded border font-mono text-[11px] font-bold flex items-center justify-center gap-1.5 transition bg-surface1/60 border-slate-700 text-slate-400 hover:text-slate-200";

        if (btnYt) btnYt.className = type === "youtube" ? activeClass : inactiveClass;
        if (btnDirect) btnDirect.className = type === "direct_url" ? activeClass : inactiveClass;
        if (btnLocal) btnLocal.className = type === "local_file" ? activeClass : inactiveClass;

        if (type === "local_file") {
            if (urlContainer) urlContainer.classList.add("hidden");
            if (localContainer) localContainer.classList.remove("hidden");
            if (indicator) indicator.textContent = "📁 Local Video Upload";
        } else {
            if (localContainer) localContainer.classList.add("hidden");
            if (urlContainer) urlContainer.classList.remove("hidden");
            if (type === "youtube") {
                if (urlLabel) urlLabel.textContent = "YouTube Source URL:";
                if (urlInput) urlInput.placeholder = "https://www.youtube.com/watch?v=... or https://youtu.be/...";
                if (hint) hint.textContent = "RemoteVideoIngestor downloads and streams high-definition video master from YouTube.";
                if (indicator) indicator.textContent = "▶️ YouTube URL";
            } else {
                if (urlLabel) urlLabel.textContent = "Direct Video Stream URL (MP4/HLS):";
                if (urlInput) urlInput.placeholder = "https://storage.googleapis.com/.../video.mp4";
                if (hint) hint.textContent = "Direct video stream URL ingested directly into canonical storage.";
                if (indicator) indicator.textContent = "🔗 Direct Video URL";
            }
        }
    },

    onBriefFileSelected(event) {
        const file = event.target.files && event.target.files[0];
        if (!file) return;

        const ext = file.name.split('.').pop().toLowerCase();
        if (!["pdf", "txt", "md"].includes(ext)) {
            alert(`Unsupported brief format '.${ext}'. Allowed formats: PDF, TXT, MD`);
            event.target.value = "";
            return;
        }
        if (file.size > 25 * 1024 * 1024) {
            alert(`Brief file size exceeds 25 MB limit (${(file.size / (1024 * 1024)).toFixed(1)} MB).`);
            event.target.value = "";
            return;
        }

        this.campaignBriefFile = file;
        const dropzone = document.getElementById("campaign-brief-dropzone");
        const chip = document.getElementById("campaign-brief-selected");
        const nameEl = document.getElementById("campaign-brief-name");
        const sizeEl = document.getElementById("campaign-brief-size");
        const badgeEl = document.getElementById("campaign-brief-badge");

        if (dropzone) dropzone.classList.add("hidden");
        if (chip) chip.classList.remove("hidden");
        if (nameEl) nameEl.textContent = file.name;
        if (sizeEl) sizeEl.textContent = `(${(file.size / 1024).toFixed(1)} KB)`;
        if (badgeEl) badgeEl.textContent = ext.toUpperCase();

        // Immediately upload & extract requirements with Brief Intelligence Engine
        window.AlAmrShellInstance.showToast(`Analyzing campaign brief (${file.name})...`, "info");
        AlAmrAPI.uploadBrief(file).then(res => {
            this.currentBriefStorageKey = res.brief_storage_key;
            this.currentBriefFilename = res.filename;
            if (res.requirements) {
                this.displayExtractedRequirements(res.requirements, res.brief_storage_key);
                window.AlAmrShellInstance.showToast("Campaign brief analyzed successfully!", "success");
            }
        }).catch(err => {
            console.error("Brief analysis failed:", err);
            window.AlAmrShellInstance.showToast(`Brief upload failed: ${err.message}`, "error");
        });
    },

    clearBriefFile(event) {
        if (event) {
            event.stopPropagation();
            event.preventDefault();
        }
        this.campaignBriefFile = null;
        this.currentRequirements = null;
        this.currentBriefStorageKey = null;
        const input = document.getElementById("campaign-brief-file-input");
        if (input) input.value = "";

        const dropzone = document.getElementById("campaign-brief-dropzone");
        const chip = document.getElementById("campaign-brief-selected");
        const panel = document.getElementById("campaign-requirements-panel");
        if (dropzone) dropzone.classList.remove("hidden");
        if (chip) chip.classList.add("hidden");
        if (panel) panel.classList.add("hidden");
    },

    displayExtractedRequirements(reqs, storageKey) {
        this.currentRequirements = reqs;
        if (storageKey) this.currentBriefStorageKey = storageKey;

        const panel = document.getElementById("campaign-requirements-panel");
        if (!panel) return;
        panel.classList.remove("hidden");

        // 1. Confidence badge
        const badge = document.getElementById("brief-confidence-badge");
        if (badge) {
            const isSuccess = reqs.metadata && reqs.metadata.extraction_status === "SUCCESS";
            badge.textContent = isSuccess ? "HIGH CONFIDENCE" : "NEEDS REVIEW";
            badge.className = `text-[10px] px-2 py-0.5 rounded border font-bold ${
                isSuccess ? "bg-emerald-950 border-emerald-500/40 text-emerald-400" : "bg-amber-950 border-amber-500/40 text-amber-400"
            }`;
        }

        // 2. Clips
        const clips = reqs.clips || {};
        const countStr = clips.clip_count_required ? `${clips.clip_count_required} required` : "3 required";
        const durStr = (clips.min_duration_seconds && clips.max_duration_seconds)
            ? `${clips.min_duration_seconds}-${clips.max_duration_seconds}s`
            : "30-60s";
        const clipsSum = document.getElementById("req-clips-summary");
        if (clipsSum) clipsSum.textContent = `${countStr} • ${durStr}`;
        const clipsMod = document.getElementById("req-clips-modality");
        if (clipsMod) clipsMod.textContent = clips.duration_modality || "REQUIRED";

        // 3. Platform & Aspect
        const plats = (reqs.platform && reqs.platform.platforms) || [];
        const platSum = document.getElementById("req-platform-summary");
        if (platSum) {
            platSum.textContent = plats.length > 0
                ? plats.map(p => p === "instagram_reels" ? "Instagram Reels" : "YouTube Shorts").join(" • ")
                : "YouTube Shorts / Reels";
        }
        const aspSum = document.getElementById("req-aspect-summary");
        if (aspSum) aspSum.textContent = `${clips.aspect_ratio || "9:16"} Vertical • ${clips.resolution || "1080x1920"}`;

        // 4. Monetization & Submission
        const mon = reqs.monetization || {};
        const monSum = document.getElementById("req-monetization-summary");
        if (monSum) {
            const cpm = mon.cpm_rate ? `CPM $${mon.cpm_rate.toFixed(2)}` : "CPM $1.50";
            const bud = mon.total_budget ? `$${mon.total_budget}` : "$500 pool";
            monSum.textContent = `${cpm} • ${bud}`;
        }
        const sub = reqs.submission || {};
        const subSum = document.getElementById("req-submission-summary");
        if (subSum) subSum.textContent = sub.deadline ? `Due: ${sub.deadline}` : "Open deadline";

        // 5. Hashtags & CTA
        const textReqs = reqs.text || {};
        const hashContainer = document.getElementById("req-hashtags-container");
        if (hashContainer) {
            const tags = textReqs.required_hashtags || [];
            if (tags.length > 0) {
                hashContainer.innerHTML = tags.map(t =>
                    `<span class="px-2 py-0.5 bg-cyan-950/60 border border-cyan-500/40 text-cyan-300 text-[10px] rounded-full font-bold">${t}</span>`
                ).join("");
            } else {
                hashContainer.innerHTML = `<span class="text-[10px] text-slate-500 italic">No specific hashtags detected in brief</span>`;
            }
        }
        const ctaEl = document.getElementById("req-cta-text");
        if (ctaEl) ctaEl.textContent = textReqs.call_to_action || "None specified";

        // 6. Content Rules
        const contentRulesEl = document.getElementById("req-content-rules");
        if (contentRulesEl) {
            const allowed = (reqs.content && reqs.content.allowed_topics) || [];
            const prohibited = (reqs.content && reqs.content.prohibited_topics) || [];
            const talking = (reqs.content && reqs.content.required_talking_points) || [];
            let html = "";
            if (allowed.length > 0) html += `<div class="text-emerald-400">✓ Allowed Topics: ${allowed.join(", ")}</div>`;
            if (prohibited.length > 0) html += `<div class="text-rose-400">✕ Prohibited: ${prohibited.join(", ")}</div>`;
            if (talking.length > 0) html += `<div class="text-cyan-300">★ Key Points: ${talking.slice(0, 2).join("; ")}</div>`;
            if (!html) html = `<div class="text-slate-400">Standard community guidelines and content rules apply</div>`;
            contentRulesEl.innerHTML = html;
        }

        // 7. Override Count Indicator
        const countEl = document.getElementById("req-override-count");
        if (countEl) {
            const overrides = reqs.overrides || [];
            countEl.textContent = overrides.length > 0 ? `[${overrides.length} OPERATOR OVERRIDE${overrides.length > 1 ? 'S' : ''}]` : "";
        }

        // Pre-populate override inputs
        const clipInput = document.getElementById("override-clip-count");
        if (clipInput) clipInput.value = clips.clip_count_required || 3;
        const minDurInput = document.getElementById("override-min-duration");
        if (minDurInput) minDurInput.value = clips.min_duration_seconds || 30;
        const maxDurInput = document.getElementById("override-max-duration");
        if (maxDurInput) maxDurInput.value = clips.max_duration_seconds || 60;
        const hashInput = document.getElementById("override-hashtags");
        if (hashInput) hashInput.value = (textReqs.required_hashtags || []).join(", ");

        // Autofill campaign name if empty
        const nameInput = document.getElementById("campaign-name-input");
        if (nameInput && !nameInput.value.trim() && reqs.identity && reqs.identity.campaign_name) {
            nameInput.value = reqs.identity.campaign_name;
        }
    },

    toggleOverridePanel() {
        const drawer = document.getElementById("campaign-override-drawer");
        const arrow = document.getElementById("override-drawer-arrow");
        if (!drawer) return;
        const isHidden = drawer.classList.contains("hidden");
        if (isHidden) {
            drawer.classList.remove("hidden");
            if (arrow) arrow.textContent = "▲";
        } else {
            drawer.classList.add("hidden");
            if (arrow) arrow.textContent = "▼";
        }
    },

    async applyRequirementsOverride() {
        if (!this.currentRequirements) return;
        const clipCount = parseInt(document.getElementById("override-clip-count").value, 10);
        const minDur = parseFloat(document.getElementById("override-min-duration").value);
        const maxDur = parseFloat(document.getElementById("override-max-duration").value);
        const hashStr = document.getElementById("override-hashtags").value;

        if (minDur >= maxDur) {
            alert("Min duration must be less than max duration.");
            return;
        }

        const tags = hashStr.split(",").map(t => t.trim()).filter(t => t.length > 0).map(t => t.startsWith("#") ? t : `#${t}`);

        try {
            // Send override mutation to preserve audit record
            let res = await AlAmrAPI.overrideRequirements({
                requirements: this.currentRequirements,
                field_path: "clips.clip_count_required",
                override_value: clipCount,
                reason: "Operator modified clip count"
            });
            res = await AlAmrAPI.overrideRequirements({
                requirements: res.requirements,
                field_path: "clips.min_duration_seconds",
                override_value: minDur,
                reason: "Operator adjusted min duration"
            });
            res = await AlAmrAPI.overrideRequirements({
                requirements: res.requirements,
                field_path: "clips.max_duration_seconds",
                override_value: maxDur,
                reason: "Operator adjusted max duration"
            });
            res = await AlAmrAPI.overrideRequirements({
                requirements: res.requirements,
                field_path: "text.required_hashtags",
                override_value: tags,
                reason: "Operator updated required hashtags"
            });

            this.displayExtractedRequirements(res.requirements);
            this.toggleOverridePanel();
            window.AlAmrShellInstance.showToast("Requirements override saved with audit trail!", "success");
        } catch (err) {
            alert(`Failed to save override: ${err.message}`);
        }
    },

    async openBriefContentModal() {
        if (!this.currentBriefStorageKey) {
            alert("No brief storage key available.");
            return;
        }
        const modal = document.getElementById("modal-brief-content");
        const bodyEl = document.getElementById("brief-modal-body");
        const nameEl = document.getElementById("brief-modal-filename");
        const pagesEl = document.getElementById("brief-modal-pages");
        const formatEl = document.getElementById("brief-modal-format");

        if (modal) modal.classList.remove("hidden");
        if (bodyEl) bodyEl.textContent = "Loading original brief...";

        try {
            const data = await AlAmrAPI.getBriefContent(this.currentBriefStorageKey);
            if (nameEl) nameEl.textContent = data.filename;
            if (pagesEl) pagesEl.textContent = `Pages: ${data.num_pages}`;
            if (formatEl) formatEl.textContent = `Format: ${data.format.toUpperCase()}`;
            if (bodyEl) bodyEl.textContent = data.full_text || "(No selectable text in document)";
        } catch (err) {
            if (bodyEl) bodyEl.textContent = `Failed to load brief content: ${err.message}`;
        }
    },

    closeBriefContentModal() {
        const modal = document.getElementById("modal-brief-content");
        if (modal) modal.classList.add("hidden");
    },

    async reanalyzeBrief() {
        if (!this.currentBriefStorageKey) {
            alert("No uploaded brief to re-analyze.");
            return;
        }
        window.AlAmrShellInstance.showToast("Re-analyzing campaign brief...", "info");
        try {
            const res = await AlAmrAPI.analyzeBrief({ brief_storage_key: this.currentBriefStorageKey });
            if (res.requirements) {
                this.displayExtractedRequirements(res.requirements, this.currentBriefStorageKey);
                window.AlAmrShellInstance.showToast("Brief re-analyzed successfully!", "success");
            }
        } catch (err) {
            alert(`Re-analysis failed: ${err.message}`);
        }
    },

    onVideoFileSelected(event) {
        const file = event.target.files && event.target.files[0];
        if (!file) return;

        const ext = file.name.split('.').pop().toLowerCase();
        if (!["mp4", "mov", "mkv", "webm"].includes(ext)) {
            alert(`Unsupported video format '.${ext}'. Allowed formats: MP4, MOV, MKV, WebM`);
            event.target.value = "";
            return;
        }
        if (file.size > 500 * 1024 * 1024) {
            alert(`Video file size exceeds 500 MB limit (${(file.size / (1024 * 1024)).toFixed(1)} MB).`);
            event.target.value = "";
            return;
        }

        this.campaignVideoFile = file;
        const dropzone = document.getElementById("campaign-video-dropzone");
        const chip = document.getElementById("campaign-video-selected");
        const nameEl = document.getElementById("campaign-video-name");
        const sizeEl = document.getElementById("campaign-video-size");

        if (dropzone) dropzone.classList.add("hidden");
        if (chip) chip.classList.remove("hidden");
        if (nameEl) nameEl.textContent = file.name;
        if (sizeEl) sizeEl.textContent = `(${(file.size / (1024 * 1024)).toFixed(1)} MB)`;
    },

    clearVideoFile(event) {
        if (event) {
            event.stopPropagation();
            event.preventDefault();
        }
        this.campaignVideoFile = null;
        const input = document.getElementById("campaign-video-file-input");
        if (input) input.value = "";

        const dropzone = document.getElementById("campaign-video-dropzone");
        const chip = document.getElementById("campaign-video-selected");
        if (dropzone) dropzone.classList.remove("hidden");
        if (chip) chip.classList.add("hidden");
    },

    onCampaignPlatformChange(platform) {
        const select = document.getElementById("campaign-account-select");
        const hint = document.getElementById("campaign-account-hint");
        if (!select) return;

        const pFilter = platform === "instagram_reels" ? "instagram" : "youtube";
        const accounts = (window.AlAmrShellInstance && window.AlAmrShellInstance.state && window.AlAmrShellInstance.state.accounts) || [];
        
        // Strict filtering: Only active, verified accounts enrolled in vault
        const matchingActive = accounts.filter(a => a.platform === pFilter && a.status === "active");

        if (matchingActive.length === 0) {
            select.innerHTML = `<option value="">-- No active/verified ${platform === 'instagram_reels' ? 'Instagram' : 'YouTube'} accounts found --</option>`;
            if (hint) {
                hint.innerHTML = `<span class="text-amber-400 font-bold">⚠ No verified active account enrolled for ${pFilter.toUpperCase()}. Please verify one in the Accounts tab first.</span>`;
            }
            return;
        }

        select.innerHTML = matchingActive.map(a => {
            return `<option value="${a.account_id}">${a.display_name || a.username} (${a.account_id}) — ✓ ACTIVE & VERIFIED</option>`;
        }).join("");

        select.value = matchingActive[0].account_id;
        if (hint) {
            hint.innerHTML = `<span class="text-emerald-400 font-bold">✓ Bound to verified account: ${matchingActive[0].display_name || matchingActive[0].username} (${matchingActive[0].account_id})</span>`;
        }
    },

    async runJobPreflightValidation() {
        let source_uri = "";
        if (this.campaignSourceType === "local_file") {
            source_uri = this.campaignVideoFile ? this.campaignVideoFile.name : "";
        } else {
            const inputEl = document.getElementById("campaign-source-input");
            source_uri = inputEl ? inputEl.value.trim() : "";
        }

        const platformRadio = document.querySelector('input[name="campaign-destination-platform"]:checked');
        const platform = (platformRadio && platformRadio.value) || "youtube_shorts";
        const accountSelect = document.getElementById("campaign-account-select");
        const targetAccountId = (accountSelect && accountSelect.value) || null;

        const badge = document.getElementById("gate-ready-badge");
        const diagCard = document.getElementById("source-diag-card");
        const blockerBanner = document.getElementById("gate-blocker-banner");
        const blockerText = document.getElementById("gate-blocker-text");
        const operatorBanner = document.getElementById("gate-operator-banner");

        if (blockerBanner) blockerBanner.classList.add("hidden");
        if (operatorBanner) operatorBanner.classList.add("hidden");
        if (badge) {
            badge.textContent = "VALIDATING...";
            badge.className = "px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-cyan-900 text-cyan-200 animate-pulse";
        }

        try {
            const valRes = await AlAmrAPI.validateJob({
                source_uri: source_uri || "test",
                target_platform: platform,
                target_account_id: targetAccountId,
                brief_storage_key: this.currentBriefStorageKey,
                requirements: this.currentRequirements,
            });

            const setCheck = (id, status) => {
                const el = document.getElementById(id);
                if (!el) return;
                const name = el.textContent.trim().split(' ')[0];
                if (status === "PASS") {
                    el.className = "p-1.5 rounded bg-emerald-950/60 border border-emerald-500/60 text-emerald-400 font-bold";
                    el.innerHTML = `${name} <span class="check-icon">✓</span>`;
                } else if (status === "FAIL") {
                    el.className = "p-1.5 rounded bg-rose-950/60 border border-rose-500/60 text-rose-400 font-bold";
                    el.innerHTML = `${name} <span class="check-icon">✕</span>`;
                } else {
                    el.className = "p-1.5 rounded bg-amber-950/60 border border-amber-500/60 text-amber-400 font-bold";
                    el.innerHTML = `${name} <span class="check-icon">⚠</span>`;
                }
            };

            const checks = valRes.checks || {};
            setCheck("check-source", (checks.source && checks.source.status) || "PASS");
            setCheck("check-requirements", (checks.requirements && checks.requirements.status) || "PASS");
            setCheck("check-destination", (checks.destination && checks.destination.status) || "PASS");
            setCheck("check-account", (checks.account && checks.account.status) || "PASS");
            setCheck("check-production", valRes.is_valid ? "PASS" : "FAIL");

            if (valRes.source_resolution && diagCard) {
                diagCard.classList.remove("hidden");
                const sr = valRes.source_resolution;
                document.getElementById("diag-source-type").textContent = `${sr.source_type.toUpperCase()} (${sr.source_access_status.toUpperCase()})`;
                document.getElementById("diag-duration").textContent = sr.duration ? `${sr.duration.toFixed(1)}s` : "Unknown";
                document.getElementById("diag-resolution").textContent = (sr.width && sr.height) ? `${sr.width}x${sr.height}` : "Unknown";
                document.getElementById("diag-filesize").textContent = sr.file_size ? `${(sr.file_size / (1024*1024)).toFixed(1)} MB` : "Stream";
                document.getElementById("diag-checksum").textContent = sr.checksum ? sr.checksum.slice(0, 16) + "..." : "Computed on download";
            }

            if (valRes.is_valid) {
                if (badge) {
                    badge.textContent = "READY FOR PRODUCTION ✓";
                    badge.className = "px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-emerald-950 text-emerald-300 border border-emerald-500/60";
                }
            } else {
                if (badge) {
                    badge.textContent = "VALIDATION FAILED ✕";
                    badge.className = "px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-rose-950 text-rose-300 border border-rose-500/60";
                }
                if (blockerBanner && blockerText) {
                    blockerBanner.classList.remove("hidden");
                    blockerText.innerHTML = (valRes.blockers || []).map(b => `<div>• ${b}</div>`).join("");
                }
            }
            return valRes;
        } catch (err) {
            if (badge) {
                badge.textContent = "VALIDATION ERROR ✕";
                badge.className = "px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-rose-950 text-rose-300 border border-rose-500/60";
            }
            if (blockerBanner && blockerText) {
                blockerBanner.classList.remove("hidden");
                blockerText.textContent = err.message || "Failed to complete compatibility preflight.";
            }
            return null;
        }
    },

    async submitCreateCampaign() {
        const nameInput = document.getElementById("campaign-name-input");
        const sourceInput = document.getElementById("campaign-source-input");
        const reqsInput = document.getElementById("campaign-requirements-input");
        const accountSelect = document.getElementById("campaign-account-select");
        const alertEl = document.getElementById("campaign-create-alert");
        const startBtn = document.getElementById("btn-start-campaign");

        const platformRadio = document.querySelector('input[name="campaign-destination-platform"]:checked');
        const platform = (platformRadio && platformRadio.value) || "youtube_shorts";
        const targetAccountId = (accountSelect && accountSelect.value) || "";

        const name = (nameInput && nameInput.value.trim()) || "";
        let source_uri = (sourceInput && sourceInput.value.trim()) || "";
        const requirements_text = (reqsInput && reqsInput.value.trim()) || "";

        const showAlert = (msg, isError = true) => {
            if (!alertEl) return;
            alertEl.classList.remove("hidden");
            alertEl.className = `p-3 rounded text-xs border font-mono ${isError ? 'bg-rose-950/50 border-rose-500/60 text-rose-300' : 'bg-emerald-950/50 border-emerald-500/60 text-emerald-300'}`;
            alertEl.innerHTML = `<div class="font-bold mb-0.5">${isError ? '⚠ Campaign Creation Blocked' : '✓ Processing'}</div><div>${msg}</div>`;
        };


        if (!name) {
            showAlert("Campaign name is required.");
            return;
        }
        if (!targetAccountId) {
            showAlert(`A verified active account is required for ${platform === 'instagram_reels' ? 'Instagram' : 'YouTube'}. Please connect an account first.`);
            return;
        }

        // Validate source depending on selected mode
        if (this.campaignSourceType === "local_file") {
            if (!this.campaignVideoFile) {
                showAlert("Please select a local video file (.mp4, .mov, .mkv, .webm) to upload.");
                return;
            }
        } else {
            if (!source_uri) {
                showAlert(`Source video URL is required (${this.campaignSourceType === 'youtube' ? 'YouTube link' : 'Direct video stream URL'}).`);
                return;
            }
        }

        // Start upload / submission with UI feedback
        if (startBtn) {
            startBtn.disabled = true;
            startBtn.innerHTML = `<span>⏳</span><span>DISPATCHING PIPELINE...</span>`;
        }

        try {
            let brief_storage_key = null;
            let brief_filename = null;

            // 1. Upload Brief if provided
            if (this.campaignBriefFile) {
                window.AlAmrShellInstance.showToast(`Uploading campaign brief (${this.campaignBriefFile.name})...`, "info");
                const briefRes = await AlAmrAPI.uploadBrief(this.campaignBriefFile);
                brief_storage_key = briefRes.brief_storage_key;
                brief_filename = briefRes.filename;
            }

            // 2. Upload Video if local file selected
            if (this.campaignSourceType === "local_file" && this.campaignVideoFile) {
                window.AlAmrShellInstance.showToast(`Uploading source master video (${this.campaignVideoFile.name})...`, "info");
                const videoRes = await AlAmrAPI.uploadSourceVideo(this.campaignVideoFile);
                source_uri = videoRes.source_uri;
            }

            // 3. Dispatch Campaign & Job
            window.AlAmrShellInstance.showToast("Creating campaign record and dispatching 9-stage pipeline...", "info");
            const res = await AlAmrAPI.createAndRunCampaign({
                name,
                source_uri,
                source_type: this.campaignSourceType,
                brief_storage_key: brief_storage_key || this.currentBriefStorageKey,
                brief_filename: brief_filename || this.currentBriefFilename,
                requirements_text,
                requirements: this.currentRequirements,
                target_platforms: [platform],
                target_account_id: targetAccountId,
                cpm_rate: (this.currentRequirements && this.currentRequirements.monetization && this.currentRequirements.monetization.cpm_rate) || 1.5,
                payout_budget: (this.currentRequirements && this.currentRequirements.monetization && this.currentRequirements.monetization.total_budget) || 500.0,
            });

            this.closeCreateCampaignModal();
            window.AlAmrShellInstance.showToast(`🚀 Campaign created! Autonomous Pipeline Job: ${res.job_id}`, "success");
            AlAmrPlayer.activeJobId = res.job_id;
            window.AlAmrShellInstance.navigateTo("clipping");
            window.AlAmrShellInstance.syncState(true);
        } catch (err) {
            showAlert(err.message || "Failed to create campaign and start pipeline.");
        } finally {
            if (startBtn) {
                startBtn.disabled = false;
                startBtn.innerHTML = `<span>🚀 START PIPELINE</span>`;
            }
        }
    },

    // 14. Publish Approved Clip
    async publishApprovedClip(jobId, clipId) {
        const jobs = (window.AlAmrShellInstance && window.AlAmrShellInstance.state && window.AlAmrShellInstance.state.jobs) || [];
        const currentJob = jobs.find(j => j.job_id === jobId) || {};
        const meta = currentJob.metadata_json || currentJob.metadata || {};
        const targetPlatform = meta.target_platform || "youtube_shorts";
        const targetAccountId = meta.target_account_id || null;

        const destName = targetPlatform === "instagram_reels" ? "Instagram Reels" : "YouTube Shorts";
        const accountPrompt = targetAccountId ? ` to ${destName} [Account: ${targetAccountId}]` : ` to ${destName}`;

        if (!confirm(`Confirm publishing clip '${clipId}' directly${accountPrompt}?`)) {
            return;
        }
        try {
            window.AlAmrShellInstance.showToast(`Publishing to ${destName}...`, "info");
            const res = await AlAmrAPI.publishClip(jobId, clipId, targetAccountId, targetPlatform);
            window.AlAmrShellInstance.showToast(`Successfully published! Post ID: ${res.platform_post_id}`, "success");
            if (res.video_url) {
                window.AlAmrShellInstance.showToast(`Live URL: ${res.video_url}`, "success");
            }
            window.AlAmrShellInstance.syncState(true);
        } catch (err) {
            window.AlAmrShellInstance.showToast(`Publishing failed: ${err.message}`, "error");
        }
    }
};

// Bootstrap application shell on DOMContentLoaded
window.addEventListener("DOMContentLoaded", () => {
    window.AlAmrShellInstance = new AlAmrShell();
});
