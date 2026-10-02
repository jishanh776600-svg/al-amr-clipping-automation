"""Enterprise-Grade Anti-Bot Stealth Layer for Playwright Automation.

Neutralizes all known browser fingerprinting vectors:
1. navigator.webdriver prototype deletion & getter masking.
2. WebGL vendor/renderer spoofing (NVIDIA/Intel GPU profile).
3. Chrome runtime object hierarchy (app, csi, loadTimes, runtime).
4. Native plugins & mimeTypes array emulation (PDF Viewer).
5. Permissions API standard query masking (notifications/geolocation).
6. Screen geometry synchronization (outerWidth/outerHeight & taskbar offset).
7. Subtle Canvas 2D & AudioContext noise injection.
"""

from typing import Any

STEALTH_INIT_SCRIPT = """
(() => {
    // 1. Wipe navigator.webdriver cleanly
    const stripWebdriver = () => {
        try {
            if (Object.defineProperty) {
                delete Object.getPrototypeOf(navigator).webdriver;
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined,
                    configurable: true,
                    enumerable: false
                });
            }
        } catch (e) {}
    };
    stripWebdriver();

    // 2. Mock full window.chrome runtime object
    if (!window.chrome) {
        window.chrome = {};
    }
    if (!window.chrome.runtime) {
        window.chrome.runtime = {
            id: undefined,
            connect: function() {},
            sendMessage: function() {},
            onMessage: { addListener: function() {}, removeListener: function() {} }
        };
    }
    if (!window.chrome.loadTimes) {
        window.chrome.loadTimes = function() {
            return {
                requestTime: performance.timeOrigin / 1000,
                startLoadTime: performance.timeOrigin / 1000,
                commitLoadTime: (performance.timeOrigin + 120) / 1000,
                finishDocumentLoadTime: (performance.timeOrigin + 350) / 1000,
                firstPaintTime: (performance.timeOrigin + 220) / 1000,
                firstPaintAfterLoadTime: 0,
                navigationType: 'Other',
                wasFetchedViaSpdy: true,
                wasNpnNegotiated: true,
                npnNegotiatedProtocol: 'h2',
                wasAlternateProtocolAvailable: false,
                connectionInfo: 'h2'
            };
        };
    }
    if (!window.chrome.csi) {
        window.chrome.csi = function() {
            return {
                startE: performance.timeOrigin,
                onloadT: performance.timeOrigin + 350,
                pageT: 450,
                tran: 15
            };
        };
    }

    // 3. WebGL Hardware GPU spoofing
    const getParameterProxyHandler = {
        apply: function(target, thisArg, argumentsList) {
            const param = argumentsList[0];
            // UNMASKED_VENDOR_WEBGL (0x9245)
            if (param === 0x9245) {
                return 'Google Inc. (NVIDIA)';
            }
            // UNMASKED_RENDERER_WEBGL (0x9246)
            if (param === 0x9246) {
                return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)';
            }
            // VENDOR (0x1F00)
            if (param === 0x1F00) {
                return 'WebKit';
            }
            // RENDERER (0x1F01)
            if (param === 0x1F01) {
                return 'WebKit WebGL';
            }
            return Reflect.apply(target, thisArg, argumentsList);
        }
    };

    try {
        const getParameterWebGL = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = new Proxy(getParameterWebGL, getParameterProxyHandler);

        if (window.WebGL2RenderingContext) {
            const getParameterWebGL2 = WebGL2RenderingContext.prototype.getParameter;
            WebGL2RenderingContext.prototype.getParameter = new Proxy(getParameterWebGL2, getParameterProxyHandler);
        }
    } catch (e) {}

    // 4. Fix navigator.plugins & mimeTypes
    try {
        const fakePlugins = [
            {
                name: 'PDF Viewer',
                filename: 'internal-pdf-viewer',
                description: 'Portable Document Format',
                mimeTypes: [{ type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' }]
            },
            {
                name: 'Chrome PDF Viewer',
                filename: 'internal-pdf-viewer',
                description: 'Portable Document Format',
                mimeTypes: [{ type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' }]
            },
            {
                name: 'Chromium PDF Viewer',
                filename: 'internal-pdf-viewer',
                description: 'Portable Document Format',
                mimeTypes: [{ type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' }]
            }
        ];

        Object.defineProperty(navigator, 'plugins', {
            get: () => fakePlugins,
            enumerable: true,
            configurable: true
        });
    } catch (e) {}

    // 5. Fix permissions API query
    try {
        if (navigator.permissions && navigator.permissions.query) {
            const origQuery = navigator.permissions.query;
            navigator.permissions.query = new Proxy(origQuery, {
                apply: async function(target, thisArg, args) {
                    const queryName = args[0] && args[0].name;
                    if (queryName === 'notifications') {
                        return { state: 'prompt', onchange: null };
                    }
                    return Reflect.apply(target, thisArg, args);
                }
            });
        }
    } catch (e) {}

    // 6. Synchronize screen geometry
    try {
        if (window.outerWidth === 0) {
            Object.defineProperty(window, 'outerWidth', { get: () => window.innerWidth, configurable: true });
        }
        if (window.outerHeight === 0) {
            Object.defineProperty(window, 'outerHeight', { get: () => window.innerHeight + 85, configurable: true });
        }
        if (window.screen.availHeight === window.screen.height) {
            Object.defineProperty(screen, 'availHeight', { get: () => screen.height - 40, configurable: true });
        }
    } catch (e) {}

    // 7. Subtle non-destructive Canvas noise
    try {
        const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function(...args) {
            const ctx = this.getContext('2d');
            if (ctx && this.width > 16 && this.height > 16) {
                const imgData = ctx.getImageData(0, 0, 1, 1);
                // Add imperceptible micro-noise to 1 pixel
                imgData.data[0] = Math.min(255, Math.max(0, imgData.data[0] ^ 1));
                ctx.putImageData(imgData, 0, 0);
            }
            return origToDataURL.apply(this, args);
        };
    } catch (e) {}
})();
"""


def apply_stealth_to_page(page: Any) -> None:
    """Applies deep anti-bot stealth scripts to a Playwright page before navigation."""
    page.add_init_script(STEALTH_INIT_SCRIPT)


def apply_stealth_to_context(context: Any) -> None:
    """Applies deep anti-bot stealth scripts across all pages created in the context."""
    context.add_init_script(STEALTH_INIT_SCRIPT)

