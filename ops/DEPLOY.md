# Deploy Runbook

## Scope

Deploy latest `main` to production and publish static app assets behind Ghost paid-member gating.

## Server

- Host access: `ssh pk`
- Repo path: `/srv/repos/hateful-eight`
- Build output: `/srv/repos/hateful-eight/dist`
- Nginx include dir: `/etc/nginx/sites-available/paulkedrosky.com.d`

## App Build

```bash
ssh pk
cd /srv/repos/hateful-eight
git pull --ff-only origin main
./scripts/build_dist.sh
```

## Ghost Theme

Install template:

```bash
cp /srv/repos/hateful-eight/ops/ghost/hateful-eight.hbs /srv/www/paulkedrosky.com/content/themes/brief/hateful-eight.hbs
```

Add route entries from `ops/ghost/routes-snippet.yaml` to:

```text
/srv/www/paulkedrosky.com/content/settings/routes.yaml
```

## Nginx

Install paywall-gated location block:

```bash
sudo cp /srv/repos/hateful-eight/ops/nginx/44-tools-hateful-eight.conf /etc/nginx/sites-available/paulkedrosky.com.d/44-tools-hateful-eight.conf
sudo nginx -t
sudo systemctl reload nginx
```

Ensure `/_ghost_paid_proxy` is already configured.

## Verification

Anonymous checks:

```bash
curl -I https://paulkedrosky.com/tools/hateful-eight/
curl -I https://paulkedrosky.com/tools/hateful-eight/app/
```

Expected:

- `/tools/hateful-eight/` returns `200`
- `/tools/hateful-eight/app/` returns `302` to signup when not authenticated
