# AL AMR — Credential Recovery Procedures

In the event of credential compromise or accidental revocation:
1. **GitHub PAT**: Revoke token at `github.com/settings/tokens`. Generate new classic token with `repo` scope. Paste into Web Console under **Settings $	o$ GitHub Credentials**.
2. **Telegram Bot**: Message `@BotFather`, run `/revoke`, select bot, copy new token. Update in Web Console under **Settings $	o$ Telegram Configuration**.
3. **Google Drive / YouTube OAuth**: Re-run OAuth authorization script `scripts/generate_drive_token.py` to obtain fresh refresh tokens.
4. **Meta / Instagram Token**: Re-generate System User token in Meta Business Suite and update via API or Console.
