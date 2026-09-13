"""Cloud Source Acquisition Diagnostic & Empirical Acceptance Probe.

Tests and reports empirical telemetry on all candidate media acquisition routes
from the actual cloud runner environment.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx


def probe_url(name: str, endpoint: str, test_url: str) -> dict:
    print(f"\n========================================================")
    print(f"PROBING CANDIDATE: {name}")
    print(f"Endpoint: {endpoint}")
    print(f"Test URL: {test_url}")
    print(f"========================================================")

    res = {
        "candidate": name,
        "endpoint": endpoint,
        "cloud_reachable": False,
        "url_accepted": False,
        "media_bytes_returned": False,
        "content_type": None,
        "http_status": None,
        "downloaded_size": 0,
        "duration": 0.0,
        "video_stream": False,
        "audio_stream": False,
        "ffprobe_valid": False,
        "result": "UNKNOWN",
        "notes": "",
    }

    start = time.time()
    try:
        if name == "Cobalt Gateway":
            with httpx.Client(timeout=15.0) as client:
                r = client.post(
                    endpoint,
                    json={"url": test_url},
                    headers={"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "ALAMR-CloudProbe/1.0"},
                )
                res["cloud_reachable"] = True
                res["http_status"] = r.status_code
                res["content_type"] = r.headers.get("content-type")
                body = r.text
                if r.status_code == 200:
                    res["url_accepted"] = True
                    data = r.json()
                    res["notes"] = f"Success: {data.get('status')}"
                    res["result"] = "SUCCESS"
                else:
                    res["notes"] = f"HTTP {r.status_code}: {body[:200]}"
                    res["result"] = f"BLOCKED_OR_AUTH_REQUIRED (HTTP {r.status_code})"

        elif name in ("Piped API", "Invidious API"):
            with httpx.Client(timeout=10.0) as client:
                r = client.get(endpoint, headers={"User-Agent": "ALAMR-CloudProbe/1.0"})
                res["cloud_reachable"] = True
                res["http_status"] = r.status_code
                res["content_type"] = r.headers.get("content-type")
                if r.status_code == 200:
                    res["url_accepted"] = True
                    res["notes"] = f"HTTP 200: {r.text[:150]}"
                    res["result"] = "STREAM_METADATA_RETURNED"
                else:
                    res["notes"] = f"HTTP {r.status_code}: {r.text[:150]}"
                    res["result"] = f"UNAVAILABLE_OR_BLOCKED (HTTP {r.status_code})"

        elif name == "Direct Remote Video URL (Public Baseline)":
            temp_path = Path("/tmp/probe_direct.mp4") if os.name != "nt" else Path("probe_direct.mp4")
            with httpx.Client(timeout=20.0, follow_redirects=True) as client:
                with client.stream("GET", test_url) as stream_resp:
                    res["cloud_reachable"] = True
                    res["http_status"] = stream_resp.status_code
                    res["content_type"] = stream_resp.headers.get("content-type")
                    if stream_resp.status_code == 200:
                        res["url_accepted"] = True
                        with temp_path.open("wb") as f:
                            for chunk in stream_resp.iter_bytes(chunk_size=65536):
                                f.write(chunk)
                                if f.tell() > 10 * 1024 * 1024:  # 10MB sample
                                    break
                        res["downloaded_size"] = temp_path.stat().st_size
                        res["media_bytes_returned"] = res["downloaded_size"] > 0

                        # Run ffprobe
                        try:
                            probe_cmd = [
                                "ffprobe", "-v", "error", "-show_entries",
                                "stream=codec_type,codec_name:format=duration",
                                "-of", "json", str(temp_path)
                            ]
                            ff_out = subprocess.check_output(probe_cmd, text=True)
                            ff_data = json.loads(ff_out)
                            streams = ff_data.get("streams", [])
                            res["video_stream"] = any(s.get("codec_type") == "video" for s in streams)
                            res["audio_stream"] = any(s.get("codec_type") == "audio" for s in streams)
                            res["duration"] = float(ff_data.get("format", {}).get("duration", 0.0))
                            res["ffprobe_valid"] = res["video_stream"] and res["duration"] > 0
                            res["result"] = "SUCCESS_VALIDATED"
                            res["notes"] = f"FFprobe verified: {len(streams)} streams, duration={res['duration']}s"
                        except Exception as ff_err:
                            res["notes"] = f"FFprobe failed: {ff_err}"
                            res["result"] = "FFPROBE_FAILED"
                        finally:
                            if temp_path.exists():
                                temp_path.unlink()

    except Exception as exc:
        res["notes"] = f"Exception: {exc}"
        res["result"] = "CONNECTION_FAILED"

    print(f"Result: {res['result']}")
    print(f"Notes: {res['notes']}")
    return res


def main():
    print("================================================================")
    print("AL AMR CLOUD MEDIA ACQUISITION ACCEPTANCE PROBE")
    print(f"Egress Environment: {os.environ.get('RUNNER_OS', 'Unknown OS')} ({os.environ.get('GITHUB_RUN_ID', 'Local')})")
    print(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print("================================================================")

    youtube_test_url = "https://youtu.be/jNQXAC9IVRw"
    public_mp4_url = "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4"

    results = []

    # 1. Cobalt Gateway
    results.append(probe_url("Cobalt Gateway", "https://api.cobalt.tools/", youtube_test_url))

    # 2. Piped API
    results.append(probe_url("Piped API", "https://pipedapi.kavin.rocks/streams/jNQXAC9IVRw", youtube_test_url))

    # 3. Invidious API
    results.append(probe_url("Invidious API", "https://invidious.nerdvpn.de/api/v1/videos/jNQXAC9IVRw", youtube_test_url))

    # 4. Direct Public Remote Video (Validates unblocked egress & FFprobe pipeline)
    results.append(probe_url("Direct Remote Video URL (Public Baseline)", public_mp4_url, public_mp4_url))

    print("\n================================================================")
    print("PROBE SUMMARY MATRIX")
    print("================================================================")
    for r in results:
        print(f"Candidate:             {r['candidate']}")
        print(f"Endpoint:              {r['endpoint']}")
        print(f"Cloud reachable:       {'YES' if r['cloud_reachable'] else 'NO'}")
        print(f"URL accepted:          {'YES' if r['url_accepted'] else 'NO'}")
        print(f"Media bytes returned:  {'YES' if r['media_bytes_returned'] else 'NO'}")
        print(f"Content-Type:          {r['content_type']}")
        print(f"HTTP status:           {r['http_status']}")
        print(f"Downloaded size:       {r['downloaded_size']} bytes")
        print(f"Duration:              {r['duration']}s")
        print(f"Video stream:          {'YES' if r['video_stream'] else 'NO'}")
        print(f"Audio stream:          {'YES' if r['audio_stream'] else 'NO'}")
        print(f"FFprobe validation:    {'PASS' if r['ffprobe_valid'] else 'FAIL'}")
        print(f"Result:                {r['result']}")
        print(f"Notes:                 {r['notes']}")
        print("----------------------------------------------------------------")


if __name__ == "__main__":
    main()
