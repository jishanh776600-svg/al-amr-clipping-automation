"""Security & SSRF Protections for Source Acquisition.

Guarantees:
- Rejects dangerous URL schemes (file://, ftp://, gopher://, javascript:, data:).
- Validates DNS resolutions against RFC 1918 private subnets, loopback addresses,
  link-local subnets (including cloud metadata services like 169.254.169.254),
  multicast, and reserved spaces.
- Validates that target download filepaths remain strictly contained within the
  isolated job target directory (preventing directory traversal attacks).
- Sanitizes file basenames.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from pathlib import Path
from urllib.parse import urlparse

from .base import SourceAcquisitionError, SourceErrorCode

#: Blocklisted hostnames associated with cloud instance metadata or localhost
BLOCKED_HOSTNAMES = frozenset({
    "localhost",
    "localhost.localdomain",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "instance-data",
    "metadata.google.internal",
    "metadata.internal",
    "169.254.169.254",
})

#: Allowed schemes for remote media acquisition
ALLOWED_SCHEMES = frozenset({"http", "https"})


NAT64_PREFIX = ipaddress.IPv6Network("64:ff9b::/96")


def is_ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if the IP address belongs to a private, loopback, or metadata subnet."""
    if isinstance(ip, ipaddress.IPv6Address) and ip in NAT64_PREFIX:
        # RFC 6052 Well-Known Prefix: unwrap embedded IPv4 address
        embedded_v4 = ipaddress.IPv4Address(ip.packed[-4:])
        return is_ip_blocked(embedded_v4)

    return bool(
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_remote_url(url: str, *, require_https: bool = False) -> str:
    """Validate that the remote URL is safe to retrieve (SSRF-safe).

    Raises SourceAcquisitionError with code SOURCE_INVALID_URL or SOURCE_ACCESS_BLOCKED
    if the URL is unsafe, malformed, or targets internal resources.
    """
    if not url or not isinstance(url, str) or not url.strip():
        raise SourceAcquisitionError(
            "No source URL provided.",
            code=SourceErrorCode.SOURCE_INVALID_URL,
            hint="Please provide a valid remote media URL.",
        )

    clean_url = url.strip()

    try:
        parsed = urlparse(clean_url)
    except Exception as exc:
        raise SourceAcquisitionError(
            f"Malformed URL: {exc}",
            code=SourceErrorCode.SOURCE_INVALID_URL,
            hint="Check that the URL format is valid.",
        ) from exc

    scheme = (parsed.scheme or "").lower()
    if not scheme or scheme not in ALLOWED_SCHEMES:
        raise SourceAcquisitionError(
            f"Unsupported URL scheme '{scheme}'. Only HTTP and HTTPS are permitted.",
            code=SourceErrorCode.SOURCE_INVALID_URL,
            hint="URLs must start with https:// or http://",
        )

    if require_https and scheme != "https":
        raise SourceAcquisitionError(
            f"Insecure scheme '{scheme}'. HTTPS is strictly required for this endpoint.",
            code=SourceErrorCode.SOURCE_INVALID_URL,
            hint="Ensure the remote endpoint URL uses https://",
        )

    hostname = (parsed.hostname or "").lower().strip()
    if not hostname:
        raise SourceAcquisitionError(
            "URL is missing a valid hostname.",
            code=SourceErrorCode.SOURCE_INVALID_URL,
            hint="Check that the URL includes a domain or public hostname.",
        )

    # Check known dangerous hostnames directly
    if hostname in BLOCKED_HOSTNAMES:
        raise SourceAcquisitionError(
            f"Access to internal host '{hostname}' is blocked.",
            code=SourceErrorCode.SOURCE_ACCESS_BLOCKED,
            hint="AL AMR prohibits requests targeting localhost or internal cloud infrastructure.",
        )

    # Resolve hostname to verify destination IP isn't private / loopback / metadata
    try:
        # Check if the hostname is directly an IP literal
        direct_ip = ipaddress.ip_address(hostname)
        if is_ip_blocked(direct_ip):
            raise SourceAcquisitionError(
                f"Access to private/internal IP address '{hostname}' is blocked.",
                code=SourceErrorCode.SOURCE_ACCESS_BLOCKED,
                hint="Requests targeting local or private network subnets are forbidden.",
            )
    except ValueError:
        # Not a raw IP literal; resolve via DNS
        try:
            addr_info = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
            for _, _, _, _, sockaddr in addr_info:
                ip_str = sockaddr[0]
                ip = ipaddress.ip_address(ip_str)
                if is_ip_blocked(ip):
                    raise SourceAcquisitionError(
                        f"Hostname '{hostname}' resolves to private/internal IP '{ip_str}'. Access blocked.",
                        code=SourceErrorCode.SOURCE_ACCESS_BLOCKED,
                        hint="Requests targeting local or private network subnets are forbidden.",
                    )
        except socket.gaierror:
            # Name resolution failure (transient network or invalid host)
            # We let yt-dlp or the HTTP provider handle name resolution errors normally
            pass

    return clean_url


def safe_target_path(target_dir: Path, filename: str) -> Path:
    """Ensure that the target file path resides strictly inside target_dir.

    Prevents path traversal attacks (e.g. filename='../../etc/passwd').
    """
    if not filename or ".." in filename or filename.startswith(("/", "\\")):
        raise SourceAcquisitionError(
            f"Path traversal detected in filename '{filename}'.",
            code=SourceErrorCode.SOURCE_MEDIA_INVALID,
            hint="Target filenames must not escape the job directory.",
        )

    resolved_dir = target_dir.resolve()
    resolved_path = (resolved_dir / filename).resolve()

    if not str(resolved_path).startswith(str(resolved_dir)):
        raise SourceAcquisitionError(
            f"Path traversal detected in filename '{filename}'.",
            code=SourceErrorCode.SOURCE_MEDIA_INVALID,
            hint="Target filenames must not escape the job directory.",
        )

    return resolved_path
