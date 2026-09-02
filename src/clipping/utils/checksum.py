"""Checksum calculation utilities."""

import hashlib


def compute_sha256_bytes(data: bytes) -> str:
    """Computes SHA-256 hex digest for in-memory bytes."""
    return hashlib.sha256(data).hexdigest()


def compute_sha256_file(file_path: str, chunk_size: int = 65536) -> str:
    """Computes SHA-256 hex digest for a file on disk."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()
