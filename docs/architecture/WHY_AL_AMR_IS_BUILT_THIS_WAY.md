# AL AMR — Architectural Decisions & Technical Rationale ("The Why")

Every engineering choice in AL AMR was shaped by rigorous empirical testing, operational constraints, and hard-learned forensic lessons from production failures.

---

## 1. Why Render as Control Plane and GitHub Actions as Worker?
- **The Problem**: Running video rendering (FFmpeg, Whisper, OpenCV) on free or low-cost cloud application hosts (Render Free, Railway, Heroku) causes immediate Out-Of-Memory (OOM) crashes, CPU starvation, and process termination. Upgrading to a 16 GB RAM dedicated server on Render costs over $85/month.
- **The Solution**: Split the architecture. Render handles lightweight REST APIs, Web Console UI, and webhooks on the free tier. GitHub Actions executes heavy media processing using 4 vCPUs and 16 GB RAM for free.
- **Result**: Enterprise-grade video processing at $0/month compute cost.

---

## 2. Why Google Drive for Persistent Artifacts?
- **The Problem**: GitHub Actions runners are ephemeral; when a workflow completes, all rendered files are wiped. Render free tiers lack persistent media storage (disk space is limited to 1 GB).
- **The Solution**: Google Drive provides 15 GB free durable cloud storage per account. The worker uploads completed MP4s directly to Drive via OAuth, storing permanent file IDs and web view links.
- **Result**: Permanent video availability without self-hosted S3/MinIO infrastructure.

---

## 3. Why Telegram as the Human Review Layer?
- **The Problem**: Asking content creators and business operators to log into a web dashboard to approve video clips creates friction, delays publishing, and results in unreviewed backlogs.
- **The Solution**: Telegram delivers native, playable video previews directly to the operator's mobile phone within seconds of render completion. With native interactive inline buttons (`[ ✅ APPROVE ]`, `[ ❌ REJECT ]`), an operator can review and publish a clip with a single tap from anywhere in the world.
- **Result**: Turnaround time from ingestion to published reel reduced to under 3 minutes.

---

## 4. Why Exactly 5 Clips per Job?
- **The Problem**: Early versions allowed variable clip counts (1 to 5). When speech audio had low confidence or high background noise, the system silently degraded to producing only 1 clip, leaving content pipelines starved.
- **The Solution**: Enforce a strict invariant: every long-form video must yield exactly 5 broadcast-quality clips. If the algorithm cannot find 5 high-confidence candidates, the pipeline rejects the job with `INSUFFICIENT_VALID_CLIPS`, alerting the operator immediately.
- **Result**: Predictable, guaranteed content yield.

---

## 5. Why Semantic Visual Matching Instead of Random Stock B-Roll?
- **The Problem**: Many automated clipping tools insert generic, irrelevant stock footage (e.g. random city skylines or coffee cups) that distracts viewers and destroys retention.
- **The Solution**: AL AMR's `SemanticParser` parses spoken transcripts for concrete nouns, verbs, and business concepts, disallowing literal translations of idioms (e.g. "it's raining cats and dogs" will not search for animals). It queries Pexels strictly for portrait (9:16) stock video that illustrates the exact concept being discussed.
- **Result**: Visual storytelling that measurably increases viewer retention and watch time.

---

## 6. Why Dedicated AES-256 Encrypted Credential Vault?
- **The Problem**: Storing credentials (GitHub PAT, Telegram Bot Token, YouTube OAuth tokens) in local plaintext `.env` files poses massive security risks and fails when containers are redeployed or ephemeral environments reboot.
- **The Solution**: A multi-credential encrypted vault anchored to a single 32-byte master key stored on persistent disk (`/data/.master_key`). The vault encrypts credentials inside SQLite and provides a self-healing fallback envelope.
- **Result**: Complete secret durability with zero risk of credential leakage in source control.
