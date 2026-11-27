# Deploy with Docker

RaiCentral Community Edition is powered by [Ztechnium](https://ztechnium.com). It runs on one machine: API, onboarding worker, console, PostgreSQL, and Redis. It serves a single organization and is not a multi-tenant service.

There are two ways to run it:

| | Local trial | Server |
|---|---|---|
| Compose files | `docker-compose.yml` | `docker-compose.yml` + `docker-compose.prod.yml` |
| Environment template | `.env.example` | `.env.production.example` |
| First operator | bootstrap account | `rai-gateway admin create` |
| Published ports | `127.0.0.1` only | `127.0.0.1` only; none for PostgreSQL and Redis |

## Requirements

- Docker Engine with Docker Compose 2.24 or newer
- A copy of this repository
- For a server: a DNS name, a TLS certificate, and a reverse proxy such as Nginx

## Local trial

From the repository root:

```bash
cp .env.example .env
docker compose up --build
```

| Service | Host address | Notes |
|---|---|---|
| UI | http://localhost:3000 | Next.js production build (`next start`) |
| API | http://localhost:8001 | Container listens on port 8000 |
| PostgreSQL | `localhost:5434` | User `rai_user`, database `rai_gateway`, password from `POSTGRES_PASSWORD` |
| Redis | `localhost:6380` | No authentication |

`.env.example` turns on a development operator, `admin@raigateway.local` / `ChangeMe123!`. Those credentials and the database password are for a private workstation only.

The API container runs `alembic upgrade head` and seeds the platform policy library before Uvicorn starts. It does not load sample prompts or customer records.

### Reach it from another machine

Ports listen on `127.0.0.1`. To expose one on a trusted network, set its bind address in `.env`: `GATEWAY_BIND_ADDRESS`, `UI_BIND_ADDRESS`, `POSTGRES_BIND_ADDRESS`, or `REDIS_BIND_ADDRESS` (for example `0.0.0.0`). On Linux, a container that calls the API through `host.docker.internal` also needs this.

## Production installation

### 1. Create `.env`

```bash
cp .env.production.example .env
chmod 600 .env
```

Never commit `.env`.

### 2. Generate secrets

Generate a different value for each secret:

```bash
openssl rand -hex 32
```

Without OpenSSL: `python3 -c "import secrets; print(secrets.token_hex(32))"`.

### 3. Set the required variables

| Variable | Value |
|---|---|
| `POSTGRES_PASSWORD` | Generated. Any characters work; single-quote a value containing `$`. |
| `JWT_SECRET_KEY` | Generated, at least 32 characters |
| `EVIDENCE_ENCRYPTION_KEY` | Generated. Keep a copy: stored encrypted data needs it. |
| `APP_PUBLIC_URL` | Public console URL, for example `https://rai.example.com` |
| `CORS_ALLOWED_ORIGINS` | The console origin. Never `*`. |
| `NEXT_PUBLIC_API_BASE_URL` | Public API URL, no trailing slash. Compiled into the console. |
| `COMPOSE_FILE` | Preset to `docker-compose.yml:docker-compose.prod.yml` |

Optional: model provider keys, SMTP settings, and `FORWARDED_ALLOW_IPS` (see [Client addresses](#client-addresses)).

The production override fixes these whatever `.env` says: `ENVIRONMENT=production`, empty `ADMIN_TOKEN` and `STEP_UP_TOKEN`, `ADMIN_BOOTSTRAP_ENABLED=false`, `ALLOW_SHARED_SECRET_STEP_UP=false`, `AUTH_COOKIE_SECURE=true`, `MCP_ADAPTER_MODE=PRODUCTION`. The API refuses to start in production with insecure settings; read `docker compose logs rai-gateway` if it exits.

### 4. Start

```bash
docker compose up -d --build
docker compose ps
```

If you removed `COMPOSE_FILE`, add `-f docker-compose.yml -f docker-compose.prod.yml` to every command.

### 5. Create the first administrator

```bash
docker compose exec rai-gateway \
    rai-gateway admin create --role admin
```

The command prompts for email, display name, and password (at least 8 characters). Passing `--password` on the command line leaves it in your shell history.

| Role | Receives |
|---|---|
| `admin`, `platform_admin` | Full platform access, plus the organization admin and RAI officer memberships. The first one also creates the organization profile, onboarding record, and default departments. |
| `platform_support` | Read-only platform access. No organization membership. |

Everyone else is invited from **Team & roles** in the console.

Other commands:

```bash
docker compose exec rai-gateway rai-gateway admin list
docker compose exec rai-gateway rai-gateway admin reset-password --email you@example.com
docker compose exec rai-gateway rai-gateway admin deactivate --email you@example.com
```

Do not enable `ADMIN_TOKEN` or bootstrap to work around a sign-in problem. Use `reset-password`.

## Reverse proxy and HTTPS

The containers speak plain HTTP on `127.0.0.1`. Terminate TLS in a reverse proxy on the same host. [`deploy/nginx/raicentral.conf.example`](../../deploy/nginx/raicentral.conf.example) is a complete Nginx configuration. Obtain a certificate (for example with Certbot), adjust the host name and certificate paths, then:

```bash
sudo cp deploy/nginx/raicentral.conf.example /etc/nginx/conf.d/raicentral.conf
sudo nginx -t && sudo systemctl reload nginx
```

Any proxy works if it strips the API path prefix, preserves `Host`, sets `X-Forwarded-Proto` and `X-Forwarded-For`, does not buffer API responses, and forwards the two `/.well-known/oauth-*` paths to the API.

### Same origin (console and API on one host name)

```dotenv
APP_PUBLIC_URL=https://rai.example.com
CORS_ALLOWED_ORIGINS=https://rai.example.com
NEXT_PUBLIC_API_BASE_URL=https://rai.example.com/api
AUTH_SET_COOKIES=true
AUTH_COOKIE_SECURE=true
AUTH_COOKIE_SAMESITE=lax
```

The proxy strips `/api`:

```nginx
location /api/ {
    proxy_pass http://127.0.0.1:8001/;
}
```

The trailing slash on `proxy_pass` is what removes the prefix.

### Separate console and API domains

```dotenv
APP_PUBLIC_URL=https://rai.example.com
CORS_ALLOWED_ORIGINS=https://rai.example.com
NEXT_PUBLIC_API_BASE_URL=https://api.example.com
```

Give `api.example.com` its own server block that proxies `/` to `127.0.0.1:8001`. The console signs requests with a bearer token, so it works across origins; keep `AUTH_COOKIE_SAMESITE=lax`.

### How the public URL is used

Compose passes `NEXT_PUBLIC_API_BASE_URL` to the API as `API_PUBLIC_BASE_URL`. It sets the refresh cookie path, the API documentation (`<url>/docs`), the SDK setup URL, and [MCP discovery](../public/integration/mcp-server.md). SDKs and agents use the same URL as `RAI_BASE_URL`.

`NEXT_PUBLIC_API_BASE_URL` is compiled into the console, so changing it needs a rebuild:

```bash
docker compose up -d --build
```

Never put a secret in a `NEXT_PUBLIC_*` variable.

### Client addresses

Uvicorn trusts `X-Forwarded-*` headers only from `FORWARDED_ALLOW_IPS`. A proxy on the Docker host reaches the container from the Compose network gateway, so rate limits and session records see that address until you trust it:

```bash
docker network inspect "$(docker compose ps -q rai-gateway | xargs docker inspect \
  -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}')" \
  -f '{{(index .IPAM.Config 0).Gateway}}'
```

Put the result in `FORWARDED_ALLOW_IPS`, then run `docker compose up -d`. Avoid `*`.

## Health and readiness

```bash
curl -fsS http://127.0.0.1:8001/health
curl -fsS http://127.0.0.1:8001/ready
curl -fsS http://127.0.0.1:8001/version
docker compose ps
```

`/health` means the process is up. `/ready` reports dependencies in its body; check that `status` is `ready`, because the response code is 200 either way.

Compose marks the API unhealthy when `/health` does not return a 2xx status, and starts the worker and console only after it is healthy. The worker has its own check (`rai-worker onboarding --healthcheck`). Every service uses `restart: unless-stopped`.

## Backup and restore

Back up three things together: the database, the onboarding source files, and `.env` (store it encrypted and separately). See also [Backup, restore, and disaster recovery](../operations/BACKUP_RESTORE_DR.md). Run these from a Linux or macOS shell.

### Back up

```bash
stamp=$(date -u +%Y%m%dT%H%M%SZ)
docker compose stop rai-onboarding-worker
docker compose exec -T postgres \
    pg_dump -U rai_user -d rai_gateway --format=custom > "rai-db-$stamp.dump"
docker compose exec -T rai-gateway \
    tar -czf - -C /var/lib/rai/onboarding-sources . > "rai-sources-$stamp.tar.gz"
docker compose start rai-onboarding-worker
```

### Restore

This replaces the current data.

```bash
docker compose stop control-plane-ui rai-onboarding-worker rai-gateway
docker compose exec -T postgres \
    pg_restore -U rai_user -d rai_gateway --clean --if-exists --exit-on-error \
    < rai-db-YYYYMMDDTHHMMSSZ.dump
docker compose run --rm --no-deps -T --entrypoint tar rai-gateway \
    -xzf - -C /var/lib/rai/onboarding-sources < rai-sources-YYYYMMDDTHHMMSSZ.tar.gz
docker compose up -d
```

Use the same `EVIDENCE_ENCRYPTION_KEY` as the backed-up instance.

## Upgrade

1. Read the release notes and compare your `.env` with the new `.env.production.example`.
2. [Back up](#back-up).
3. Get the new version and rebuild:

   ```bash
   git pull
   docker compose up -d --build
   ```

4. The API applies database migrations at startup. Check `docker compose ps`, `/ready`, and sign in.

To roll back, check out the previous version, rebuild, and restore the backup: older code may not run against a newer schema.

### Upgrading from an earlier Compose file

| Change | Action |
|---|---|
| `POSTGRES_PASSWORD` is required | For an existing database set the password it was created with. Earlier versions used `rai_password`; change it as shown below. |
| Ports listen on `127.0.0.1` | Point your proxy at `127.0.0.1`, or set the matching `*_BIND_ADDRESS`. |
| Bootstrap is off unless set | Keep `ADMIN_BOOTSTRAP_ENABLED=true` in a development `.env` if you rely on it. Existing accounts are unaffected. |
| No default admin or step-up token | Set `ADMIN_TOKEN` (development scripts only) if a script needs it. |
| Console no longer sends `X-Admin-Token` | Sign in with a user account. |

PostgreSQL reads `POSTGRES_PASSWORD` only when it creates the data volume. To change the password later:

```bash
docker compose exec postgres psql -U rai_user -d rai_gateway -c '\password rai_user'
```

Then put the same value in `.env` and run `docker compose up -d`.

## Develop against the containers

`make dev` uses `docker-compose.dev.yml`. The API reloads Python from `./src`, and the UI runs `next dev` instead of a production image.

```bash
cp .env.example .env
make dev
```

Run only the data stores in containers when you want a debugger:

```bash
docker compose up postgres redis -d
make migrate
uv run uvicorn rai_gateway.main:app --reload --app-dir src --port 8001
pnpm --dir control-plane-ui dev
```

`make migrate` uses the `DATABASE_URL` in `.env`, which points at `localhost:5434`. If you change `POSTGRES_PASSWORD`, change the URL too (percent-encode reserved characters there).

## Optional data

| Command | Effect |
|---|---|
| `make seed-mcp-catalog` | Loads `data/mcp_seed_catalog.json` (sample MCP templates). Idempotent. |
| `ENABLE_REFERENCE_PILOT_SEED=true` | Allows `POST /v1/governance/seed-reference-pilots` to add sample systems on this instance. Default is off, so the route responds 404. It does not create another organization. |

Leave both off for a clean trial.

## Build the images alone

```bash
docker compose build
docker build -t rai-gateway:local .
docker build -t rai-control-plane-ui:local ./control-plane-ui
```

The API image runs as a non-root user and includes Alembic plus the optional MCP catalog script. It does not include the deleted demo seeders.
