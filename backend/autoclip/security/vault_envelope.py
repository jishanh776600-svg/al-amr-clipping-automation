"""Encrypted vault envelope for zero-config persistence across container recreation.

This module contains ciphertext encrypted with the production master key (AES-256 Fernet).
It is safely importable across all packaging formats and ensures that if a container is
recreated without a persistent disk, the vault can restore credentials automatically on boot.
"""

CIPHERTEXT = "gAAAAABqrqSvnR057vInInqcPmGSb-eGGe8xdF2Z0Etc4_mUdn0muYkHnGD5XfAKvezuUNPOGV9ALXBohbZN1Ua4w6VpLcTTTKAbzjyN4HzC2Xz9CZKAHIPhavso-yBOcpOax5DUb1Kb"
