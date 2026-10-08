# AL AMR — Credential & Security Architecture

## 1. Zero-Leak Architecture

AL AMR strictly enforces zero-leak credential management:
- **No Plaintext on Disk**: Sensitive secrets (PATs, OAuth tokens, bot tokens) are never committed to Git or stored in plaintext in SQLite.
- **AES-256 Envelope**: Secrets are encrypted using symmetric Fernet encryption (`cryptography.fernet.Fernet`), deriving a 128-bit AES-CBC cipher with PKCS7 padding and HMAC-SHA256 authentication.
- **Master Key Resolution Hierarchy**:
  1. `AL_AMR_MASTER_KEY` environment variable.
  2. Persistent master key file at `/data/.master_key`.
  3. Auto-generation and durable persistence on first launch.

---

## 2. Masking & API Security
All REST API endpoints that return credentials or destination configs automatically mask sensitive values (e.g. `ghp_...****`).
