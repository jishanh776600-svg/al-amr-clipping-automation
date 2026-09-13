"""Cloudflare WARP Egress & SOCKS5 Proxy Pre-flight Diagnostics.

Verifies connectivity through Cloudflare WARP sidecar or designated egress proxy
before executing remote media extraction, preventing silent datacenter IP blocking.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

DEFAULT_WARP_PROXY = "socks5://127.0.0.1:1080"


def is_port_open(host: str = "127.0.0.1", port: int = 1080, timeout: float = 0.5) -> bool:
    """Check whether a TCP port is open and listening locally."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, port)) == 0
    except Exception:
        return False


def resolve_egress_proxy(configured_proxy: str | None = None) -> str:
    """Resolve active egress proxy from explicit setting, env vars, or local WARP port."""
    if configured_proxy and configured_proxy.strip():
        return configured_proxy.strip()

    env_proxy = (
        os.environ.get("AUTOCLIP_PROXY")
        or os.environ.get("YTDLP_PROXY")
        or os.environ.get("ALL_PROXY")
    )
    if env_proxy and env_proxy.strip():
        return env_proxy.strip()

    # Check if local WARP sidecar is running on 127.0.0.1:1080
    if is_port_open("127.0.0.1", 1080):
        return DEFAULT_WARP_PROXY

    return ""


def check_warp_status(proxy_url: str | None = None, timeout: float = 5.0) -> dict[str, Any]:
    """Test egress proxy connectivity and verify Cloudflare WARP status.

    Returns structured dictionary with connection health, client IP,
    location, WARP status, and round-trip latency.
    """
    effective_proxy = resolve_egress_proxy(proxy_url)

    if not effective_proxy:
        return {
            "active": False,
            "proxy_url": "",
            "client_ip": "",
            "location": "",
            "warp_status": "off",
            "warp_on": False,
            "latency_ms": 0.0,
            "error": "No egress proxy configured and local WARP sidecar (127.0.0.1:1080) is closed.",
        }

    start_time = time.perf_counter()
    text = ""
    err_msg = ""

    # Attempt 1: httpx with SOCKS / HTTP proxy support
    try:
        with httpx.Client(proxy=effective_proxy, timeout=timeout) as client:
            resp = client.get("https://cloudflare.com/cdn-cgi/trace")
            resp.raise_for_status()
            text = resp.text
    except Exception as exc:
        err_msg = str(exc)
        log.debug("httpx probe through %s failed (%s), attempting curl fallback...", effective_proxy, err_msg)

    # Attempt 2: System curl fallback (universal SOCKS5 and HTTP proxy support)
    if not text:
        import shutil
        import subprocess

        curl_bin = shutil.which("curl")
        if curl_bin:
            try:
                proxy_arg = effective_proxy
                if proxy_arg.startswith("socks5://"):
                    proxy_arg = "socks5h://" + proxy_arg[len("socks5://"):]
                cmd = [
                    curl_bin,
                    "-s",
                    "--max-time", str(max(int(timeout), 5)),
                    "--proxy", proxy_arg,
                    "https://cloudflare.com/cdn-cgi/trace",
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 3)
                if proc.returncode == 0 and "ip=" in proc.stdout:
                    text = proc.stdout
                elif proc.stderr:
                    err_msg = f"{err_msg}; curl: {proc.stderr.strip()}"
            except Exception as curl_exc:
                err_msg = f"{err_msg}; curl error: {curl_exc}"

    elapsed_ms = round((time.perf_counter() - start_time) * 1000, 1)

    if text and "ip=" in text:
        trace_data: dict[str, str] = {}
        for line in text.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                trace_data[k.strip()] = v.strip()

        client_ip = trace_data.get("ip", "")
        location = trace_data.get("loc", "")
        warp_status = trace_data.get("warp", "off")
        warp_on = warp_status in ("on", "plus")

        log.info(
            "WARP proxy probe succeeded via %s: IP=%s, loc=%s, warp=%s, latency=%.1fms",
            effective_proxy,
            client_ip,
            location,
            warp_status,
            elapsed_ms,
        )

        return {
            "active": True,
            "proxy_url": effective_proxy,
            "client_ip": client_ip,
            "location": location,
            "warp_status": warp_status,
            "warp_on": warp_on,
            "latency_ms": elapsed_ms,
            "error": None,
        }

    log.warning("WARP proxy probe failed via %s: %s", effective_proxy, err_msg)
    return {
        "active": False,
        "proxy_url": effective_proxy,
        "client_ip": "",
        "location": "",
        "warp_status": "off",
        "warp_on": False,
        "latency_ms": elapsed_ms,
        "error": err_msg,
    }
