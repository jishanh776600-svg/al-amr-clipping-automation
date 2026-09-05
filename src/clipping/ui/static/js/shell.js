/**
 * AL AMR CLIPPING // Global Shell, Navigation Router & Control Action Controller
 */

class AlAmrShell {
    constructor() {
        this.currentView = "overview";
        this.pollingInterval = null;
        this.pollSeconds = 5;
        this.isPollingActive = true;
        this.lastState = null;
        this.views = {
            overview: null,
            agent: null,
            campaigns: null,
            accounts: null,
            clipping: null,
            approvals: null,
            publishing: null,
            tasks: null,
            workers: null,
            escalations: null,
            activity: null,
            system: null,
        };

        this.init();
    }

    init() {
        this.bindEvents();
        this.initRouter();
        this.startStateSync();
    }

    bindEvents() {
        // Mobile sidebar toggle
        const toggleBtn = document.getElementById("mobile-menu-btn");
        const sidebar = document.getElementById("app-sidebar");
        const overlay = document.getElementById("sidebar-overlay");

        if (toggleBtn && sidebar && overlay) {
            toggleBtn.addEventListener("click", () => {
                sidebar.classList.toggle("open");
                overlay.classList.toggle("active");
            });
            overlay.addEventListener("click", () => {
                sidebar.classList.remove("open");
                overlay.classList.remove("active");
            });
        }

        // Window resize
        window.addEventListener("resize", () => {
            if (window.innerWidth > 1024 && sidebar && overlay) {
                sidebar.classList.remove("open");
                overlay.classList.remove("active");
            }
        });

        // Visibility change: Pause polling when tab is hidden
        document.addEventListener("visibilitychange", () => {
            if (document.visibilityState === "hidden") {
                this.isPollingActive = false;
            } else {
                this.isPollingActive = true;
                this.syncState(true);
            }
        });

        // Manual refresh button
        const refreshBtn = document.getElementById("btn-global-refresh");
        if (refreshBtn) {
            refreshBtn.addEventListener("click", () => {
                this.syncState(true);
                this.showToast("Telemetry synced with cloud canonical storage", "info");
            });
        }
    }

    initRouter() {
        // Handle popstate
        window.addEventListener("popstate", () => this.handleRoute());
        window.addEventListener("hashchange", () => this.handleRoute());

        // Intercept nav clicks
        document.querySelectorAll("[data-nav-target]").forEach((el) => {
            el.addEventListener("click", (e) => {
                e.preventDefault();
                const target = el.getAttribute("data-nav-target");
                this.navigateTo(target);
            });
        });

        this.handleRoute();
    }

    handleRoute() {
        let path = window.location.pathname.replace(/^\//, "");
        const hash = window.location.hash.replace(/^#/, "");

        let target = hash || path || "overview";
        if (target === "dashboard") target = "overview";

        const validViews = [
            "overview", "agent", "campaigns", "accounts", "clipping",
            "approvals", "publishing", "tasks", "workers", "escalations",
            "activity", "system"
        ];

        if (!validViews.includes(target)) {
            target = "overview";
        }

        this.switchView(target, false);
    }

    navigateTo(viewName) {
        if (this.currentView === viewName) return;
        window.location.hash = viewName;
        this.switchView(viewName, true);

        // Auto-close mobile sidebar if open
        const sidebar = document.getElementById("app-sidebar");
        const overlay = document.getElementById("sidebar-overlay");
        if (sidebar && overlay) {
            sidebar.classList.remove("open");
            overlay.classList.remove("active");
        }
    }

    switchView(viewName, pushHistory = true) {
        this.currentView = viewName;

        // Update active nav item in sidebar
        document.querySelectorAll("[data-nav-target]").forEach((el) => {
            const target = el.getAttribute("data-nav-target");
            if (target === viewName || (viewName === "overview" && target === "dashboard")) {
                el.classList.add("active");
            } else {
                el.classList.remove("active");
            }
        });

        // Update page title / breadcrumb
        const titleEl = document.getElementById("view-header-title");
        if (titleEl) {
            const formatted = viewName.toUpperCase();
            titleEl.textContent = formatted === "OVERVIEW" ? "MISSION OVERVIEW" : formatted;
        }

        // Hide all view panels and show the target view
        document.querySelectorAll(".view-panel").forEach((panel) => {
            if (panel.id === `view-${viewName}`) {
                panel.classList.remove("hidden");
            } else {
                panel.classList.add("hidden");
            }
        });

        // Trigger view-specific render
        if (window.AlAmrViews && typeof window.AlAmrViews[viewName] === "function") {
            window.AlAmrViews[viewName](this.lastState);
        }
    }

    startStateSync() {
        this.syncState(true);
        if (this.pollingInterval) clearInterval(this.pollingInterval);
        this.pollingInterval = setInterval(() => {
            if (this.isPollingActive) {
                this.syncState(false);
            }
        }, this.pollSeconds * 1000);
    }

    async syncState(forceViewRender = false) {
        try {
            const overview = await AlAmrAPI.getDashboardOverview();
            this.lastState = overview;
            this.updateStatusBar(overview);
            this.updateBanners(overview);
            this.updateNavBadges(overview);

            // Re-render current active view if controller exists
            if (window.AlAmrViews && typeof window.AlAmrViews[this.currentView] === "function") {
                window.AlAmrViews[this.currentView](overview, forceViewRender);
            }

            const timestampEl = document.getElementById("last-sync-timestamp");
            if (timestampEl) {
                const d = new Date(overview.timestamp);
                timestampEl.textContent = d.toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
            }
        } catch (err) {
            console.warn("[AlAmrShell] State sync notice:", err.message);
            const statusDot = document.getElementById("status-dot-system");
            const statusText = document.getElementById("status-text-system");
            if (statusDot) statusDot.className = "w-2 h-2 rounded-full bg-amber-400 animate-pulse";
            if (statusText) statusText.textContent = "DEGRADED SYNC";
        }
    }

    updateStatusBar(state) {
        const statusDot = document.getElementById("status-dot-system");
        const statusText = document.getElementById("status-text-system");
        const modeText = document.getElementById("status-text-mode");
        const queuePill = document.getElementById("status-text-queue");
        const workersPill = document.getElementById("status-text-workers");
        const pubGatePill = document.getElementById("status-text-pubgate");

        if (statusDot && statusText) {
            if (state.emergency_stopped) {
                statusDot.className = "w-2 h-2 rounded-full bg-rose-500 animate-ping";
                statusText.textContent = "EMERGENCY STOP";
                statusText.className = "text-rose-400 font-bold";
            } else if (state.automation_paused) {
                statusDot.className = "w-2 h-2 rounded-full bg-amber-400";
                statusText.textContent = "PAUSED";
                statusText.className = "text-amber-300";
            } else {
                statusDot.className = "w-2 h-2 rounded-full bg-emerald-400";
                statusText.textContent = "OPERATIONAL";
                statusText.className = "text-emerald-300";
            }
        }

        if (modeText) {
            modeText.textContent = `MODE: ${state.operating_mode ? state.operating_mode.toUpperCase() : "AUTOMATIC"}`;
        }
        if (queuePill && state.counts) {
            queuePill.textContent = `QUEUE: ${state.counts.queue_depth || 0}`;
        }
        if (workersPill && state.counts) {
            workersPill.textContent = `WORKERS: ${state.counts.active_workers || 0}`;
        }
        if (pubGatePill) {
            if (state.publishing_locked || state.emergency_stopped) {
                pubGatePill.textContent = "PUB GATE: LOCKED";
                pubGatePill.className = "flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-rose-500/10 border border-rose-500/30 text-rose-300";
            } else {
                pubGatePill.textContent = "PUB GATE: OPEN";
                pubGatePill.className = "flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/30 text-emerald-400";
            }
        }
    }

    updateBanners(state) {
        const emBanner = document.getElementById("banner-emergency");
        const pauseBanner = document.getElementById("banner-paused");
        const pubLockBanner = document.getElementById("banner-publock");

        if (emBanner) {
            if (state.emergency_stopped) emBanner.classList.remove("hidden");
            else emBanner.classList.add("hidden");
        }
        if (pauseBanner) {
            if (state.automation_paused && !state.emergency_stopped) pauseBanner.classList.remove("hidden");
            else pauseBanner.classList.add("hidden");
        }
        if (pubLockBanner) {
            if (state.publishing_locked && !state.emergency_stopped) pubLockBanner.classList.remove("hidden");
            else pubLockBanner.classList.add("hidden");
        }
    }

    updateNavBadges(state) {
        if (!state || !state.counts) return;

        const badgeApprovals = document.getElementById("nav-badge-approvals");
        if (badgeApprovals) {
            const cnt = state.counts.pending_approvals || 0;
            badgeApprovals.textContent = cnt;
            badgeApprovals.className = cnt > 0 ? "nav-badge danger" : "nav-badge";
        }

        const badgeEscalations = document.getElementById("nav-badge-escalations");
        if (badgeEscalations) {
            const cnt = state.counts.open_escalations || 0;
            badgeEscalations.textContent = cnt;
            badgeEscalations.className = cnt > 0 ? "nav-badge danger" : "nav-badge";
        }

        const badgeQueue = document.getElementById("nav-badge-tasks");
        if (badgeQueue) {
            badgeQueue.textContent = state.counts.queue_depth || 0;
        }

        const badgeWorkers = document.getElementById("nav-badge-workers");
        if (badgeWorkers) {
            badgeWorkers.textContent = state.counts.active_workers || 0;
        }
    }

    showToast(message, type = "info", duration = 4000) {
        const container = document.getElementById("toast-container");
        if (!container) return;

        const toast = document.createElement("div");
        toast.className = `toast ${type}`;

        const icon = type === "success" ? "✓" : type === "error" ? "🛑" : "ℹ";
        toast.innerHTML = `
            <span class="font-bold">${icon}</span>
            <div class="flex-1">${message}</div>
        `;

        container.appendChild(toast);
        setTimeout(() => {
            toast.style.opacity = "0";
            toast.style.transform = "translateY(8px)";
            setTimeout(() => toast.remove(), 200);
        }, duration);
    }
}

window.AlAmrShell = AlAmrShell;
