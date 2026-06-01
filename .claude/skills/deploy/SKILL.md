---
name: deploy
description: Deploy the gold-price-tracker backend to the live Oracle Cloud VM. Use when the user says "deploy", "deploy gold-price", "ship it", "push to prod", "redeploy", or similar — anything implying they want the latest committed code running in production. Pulls latest origin/main on the VM over SSH, rebuilds the Docker image, restarts the container, and verifies the public health endpoint.
---

# Deploy gold-price-tracker to production

Production runs on a shared Oracle Cloud Always-Free VM alongside `net-tracker`. A deploy is two commands run over SSH, plus a health check.

> The GitHub repo is named `gold-bar-tracker` (legacy name). The deployed service is named `gold-price-tracker` everywhere on the VM (directory, container, Docker image). Don't conflate the two.

## How to invoke

User says any of: "deploy", "deploy gold-price", "ship it", "push to prod", "redeploy", "deploy to oracle".

**Pre-flight assumption:** the code is committed AND pushed to `origin/main`. The skill does a `git pull` on the VM — local-only commits won't be deployed.

## VM layout

```
~/apps/gold-price-tracker/
├── repo/                       # the gold-bar-tracker GitHub repo, cloned via deploy key + github-gold SSH alias
│   └── backend/Dockerfile      # VM-local, not in this repo's git
├── docker-compose.yml          # backend only (VM-local). Joins the shared external `apps_web` Docker network.
└── .env                        # secrets — API_KEY, DATABASE_URL, RESEND_API_KEY, MAGIC_LINK_BASE_URL (VM-local, 0600)
```

**Caddy lives in `~/apps/net-tracker/`** and routes both `yzeir-net.duckdns.org` and `yzeir-gold.duckdns.org` via its Caddyfile. Upstream for gold-price is the container name: `gold-price-backend:8000`. To change routing, edit `~/apps/net-tracker/Caddyfile` and run `sudo docker exec caddy caddy reload --config /etc/caddy/Caddyfile`.

`docker-compose.yml`, `Dockerfile`, and `.env` exist only on the VM. They are intentionally not in this repo. If the VM is ever recreated, they need to be reconstructed.

## Procedure

1. **Read connection details** from `.claude/skills/deploy/deploy.env.local` (gitignored). Required keys: `SSH_HOST`, `SSH_USER`, `SSH_KEY`, `REMOTE_PATH`, `HEALTH_URL`.

   If the file is missing, **refuse to deploy** and tell the user to create it (see Setup below).

2. **Confirm with the user** before deploying. Tell them: "Pulling latest from origin/main on the VM, rebuilding the Docker image, restarting the container." Wait for go-ahead unless they were explicit ("deploy now").

3. **Run the deploy** over SSH:

   ```bash
   ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$SSH_HOST" \
     "cd $REMOTE_PATH/repo && git pull && cd .. && sudo docker compose up -d --build 2>&1 | tail -30"
   ```

4. **Verify** the public health endpoint:

   ```bash
   curl -sS -o /dev/null -w "HTTP %{http_code} in %{time_total}s\n" "$HEALTH_URL"
   ```

   Expect HTTP 200.

5. **On failure**, tail the backend container logs:

   ```bash
   ssh -i "$SSH_KEY" "$SSH_USER@$SSH_HOST" "sudo docker logs gold-price-backend --tail 60"
   ```

## Setup (one-time per machine)

The skill needs `.claude/skills/deploy/deploy.env.local`. Gitignored. Format:

```
SSH_HOST=<vm-public-ip>
SSH_USER=ubuntu
SSH_KEY=<absolute-path-to-private-key>
REMOTE_PATH=~/apps/gold-price-tracker
HEALTH_URL=https://yzeir-gold.duckdns.org/
```

The SSH key is the same key used for the `net-tracker` deploy skill (one key, two repos).

## Operating notes

- **Updating a secret** (Neon password rotation, new Resend key, new API_KEY): SSH in, edit `~/apps/gold-price-tracker/.env`, then `sudo docker compose -f ~/apps/gold-price-tracker/docker-compose.yml up -d --force-recreate backend`. The skill does NOT touch `.env`.
- **Memory pressure**: the VM is shared with `net-tracker` on 954 MB RAM + 2 GB swap. The `/snapshot` cron fan-out spikes memory by ~100–200 MB temporarily every 20 min. Watch `free -h` and `sudo docker stats` if anything misbehaves.
- **DNS**: `yzeir-gold.duckdns.org` → VM public IP. DuckDNS IP is updated manually on the dashboard.
- **Cron destinations**: the three Upstash QStash schedules (`/snapshot`, weekly report, monthly report) must point at `https://yzeir-gold.duckdns.org/...`. If you ever change DuckDNS or move the VM, update the QStash schedule URLs too — otherwise snapshots stop landing silently.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Permission denied (publickey)` | Wrong `SSH_KEY` path in `deploy.env.local` | Verify path |
| `git pull` says `Authentication failed` | GitHub deploy key broken/removed on VM | Re-add VM's `~/.ssh/github_gold_bar.pub` at https://github.com/yzeirbaku/gold-bar-tracker/settings/keys |
| Health check returns 502 | Backend crashed on startup | Tail container logs (step 5) — usually missing/wrong env var or DB connection |
| `/snapshot` cron silently stops | QStash schedule still points at the old URL | Update destination in QStash dashboard |
| `no space left on device` | Docker build cache filled the disk | `ssh ... "sudo docker system prune -af --volumes"` then redeploy |

## Cross-references

- Sibling repo: `net-tracker` (`.claude/skills/deploy/SKILL.md`) — same VM, same Caddy, same `apps_web` Docker network.
