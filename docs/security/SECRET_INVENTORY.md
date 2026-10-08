# AL AMR — Secret & Credential Inventory

| Secret Name | Provider | Purpose | Where Stored | Rotation Procedure |
| :--- | :--- | :--- | :--- | :--- |
| `OPERATOR_TOKEN` | Internal | Admin authentication to Web Console | Render Env | Change env var & redeploy |
| `WORKER_CALLBACK_SECRET` | Internal | Worker $	o$ Control Plane HMAC signature | Render Env & GitHub Secrets | Update both locations |
| `GITHUB_PAT` | GitHub | Dispatching worker workflows | Encrypted Vault (`app_credentials`) | Update in Web Console Settings |
| `TELEGRAM_BOT_TOKEN` | Telegram | Review bot messaging and inline buttons | Encrypted Vault / Render Env | Generate in @BotFather, update settings |
| `GOOGLE_DRIVE_REFRESH_TOKEN` | Google | Cloud artifact persistence | Encrypted Vault / GitHub Secrets | OAuth flow re-authorization |
| `YOUTUBE_REFRESH_TOKEN` | Google | YouTube Shorts automated publishing | Encrypted Vault | OAuth consent flow |
| `META_ACCESS_TOKEN` | Meta | Instagram Reels automated publishing | Encrypted Vault | Meta Graph API Explorer |
| `PEXELS_API_KEY` | Pexels | Stock B-roll video search | GitHub Secrets / Render Env | Pexels Developer Dashboard |
| `AL_AMR_MASTER_KEY` | Internal | Decryption of vault envelope | `/data/.master_key` or Render Env | Re-encrypt vault before changing |
