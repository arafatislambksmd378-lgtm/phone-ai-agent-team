# Backend Deployment & Android Connection
No paid hosting required. Run on any always-on machine/VPS/home server/Termux (optional):
`API_AUTH_TOKEN=<long-random> GITHUB_TOKEN=<fine-grained token> python3 main.py --api --api-port 8000`
Put it behind HTTPS (Caddy/nginx reverse proxy or Cloudflare Tunnel — free tiers) and enter that
`https://…` URL + API token in Android Settings. The token is stored Keystore-encrypted on device;
the GitHub token never leaves the backend and is never sent to the Android app or AI providers.
Telegram runs against the same process/DB (`main.py` normal mode), so both interfaces share state.
