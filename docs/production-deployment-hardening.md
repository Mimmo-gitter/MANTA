# Production Hardening & Deployment Configuration (`TASK-301-5` / `SCRUM-500`)

This guide documents MANTA's production hardening setup for Keycloak 24, FastAPI (`uvicorn`), PostgreSQL/PgBouncer migrations, CORS, and runtime secrets behind a TLS-terminating Caddy reverse proxy.

## 1. Architecture Summary

| Component | Dev Baseline (`backend/docker-compose.yml`) | Production Hardening (`backend/docker-compose.prod.yml`) |
| :--- | :--- | :--- |
| **Keycloak Mode** | `start-dev --import-realm` | `start --import-realm`, `KC_HOSTNAME`, `KC_PROXY_HEADERS=xforwarded`, `KC_HTTP_ENABLED=true`, `KC_HOSTNAME_STRICT_HTTPS=true`, `KC_HOSTNAME_STRICT=false` |
| **Keycloak Realm** | `infra/keycloak/realm-export.json` (`sslRequired: none`, `60s`/`120s` lifespans, 13 test users, `localhost` URIs) | Rendered via `infra/keycloak/render_prod_realm.py` from `realm-export.json` + `infra/keycloak/realm-prod-overlay.json` (`sslRequired: external`, `900s`/`1800s`/`36000s` lifespans, `https://manta.<domain>/*`, `users: []`) |
| **FastAPI Proxy Headers** | `uvicorn --proxy-headers --forwarded-allow-ips=*` | Same (`backend/Dockerfile` honors `X-Forwarded-Proto/For/Host` from Caddy) |
| **FastAPI CORS** | Explicit `CORS_ORIGINS` + localhost regex (`APP_ENV=development`) | Explicit HTTPS `CORS_ORIGINS` only (`APP_ENV=production` disables localhost regex) |
| **DB Migrations** | `alembic upgrade head && python -m scripts.seed_dev_db --respect-env` | `python -m scripts.run_prod_migrations` (`SEED_DEV_DATA=false`, runs `alembic upgrade head` only + rotates `eng_app_user` SCRAM-SHA-256 password) |
| **Host Port Exposure** | Ports `5432`, `6432`, `6379`, `8000`, `8080`, `1025`, `8025` exposed | Stripped (`ports: !reset []`); Caddy on `manta_net` is the sole public ingress |

---

## 2. Secret Generation & Rotation Commands

Run on a trusted operator workstation (never commit generated values to Git):

```bash
# 1. 256-bit AES-GCM Encryption Master Key (64 hex chars)
openssl rand -hex 32

# 2. PostgreSQL Admin Password (POSTGRES_PASSWORD)
openssl rand -base64 32 | tr -d '/+=' | head -c 32

# 3. Least-Privilege App DB Role Password (ENG_APP_DB_PASSWORD for eng_app_user)
openssl rand -base64 32 | tr -d '/+=' | head -c 32

# 4. Keycloak Admin Console Password (KEYCLOAK_ADMIN_PASSWORD)
openssl rand -base64 32 | tr -d '/+=' | head -c 32
```

> [!TIP]
> Using alphanumeric-only strings (as shown with `tr -d '/+='`) avoids URL-encoding mismatches between `POSTGRES_PASSWORD`/`ENG_APP_DB_PASSWORD` and `DATABASE_URL`/`MIGRATION_DATABASE_URL`.

---

## 3. Required GitHub Repository Secrets

Configure the following secrets under **GitHub Repository $\rightarrow$ Settings $\rightarrow$ Secrets and variables $\rightarrow$ Actions** (or the `production` GitHub Environment):

| Secret Name | Example / Format | Notes |
| :--- | :--- | :--- |
| `MANTA_DOMAIN` | `example.com` | Base domain; renders `https://manta.<domain>/*` in Keycloak realm |
| `KC_HOSTNAME` | `auth.manta.example.com` | Public Keycloak hostname behind Caddy |
| `CORS_ORIGINS` | `https://manta.example.com` | Explicit HTTPS portal origin(s), comma-separated |
| `OIDC_ISSUER` | `https://auth.manta.example.com/realms/manta` | Public JWT `iss` claim validated by FastAPI |
| `OIDC_JWKS_URL` | `http://keycloak:8080/realms/manta/protocol/openid-connect/certs` | Internal Docker JWKS URL (or public HTTPS URL) |
| `WEB_BASE_URL` | `https://manta.example.com` | Used in invitation & portal links |
| `WEBHOOK_BASE_URL` | `https://api.manta.example.com` | Used when generating provider webhook callback URLs |
| `ENCRYPTION_MASTER_KEY` | 64-char hex (`openssl rand -hex 32`) | Rotated AES-256-GCM key shared by `app` and `worker` |
| `POSTGRES_PASSWORD` | 32-char random secret | Used by PostgreSQL, PgBouncer (`auth_user=manta_user`), Keycloak DB, and `MIGRATION_DATABASE_URL` |
| `ENG_APP_DB_PASSWORD` | 32-char random secret | Synced to PostgreSQL role `eng_app_user` by `run_prod_migrations.py` and used in `DATABASE_URL` |
| `DATABASE_URL` | `postgresql+asyncpg://eng_app_user:<ENG_APP_DB_PASSWORD>@pgbouncer:5432/manta_db` | Runtime app/worker DB connection via PgBouncer |
| `MIGRATION_DATABASE_URL` | `postgresql+asyncpg://manta_user:<POSTGRES_PASSWORD>@postgres:5432/manta_db` | Direct admin connection for Alembic migrations |
| `KEYCLOAK_ADMIN_PASSWORD` | 32-char random secret | Keycloak `master` realm admin password |
| `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | Provider SMTP credentials | Production email delivery for member invitations |

---

## 4. Caddy Reverse Proxy Header Contract

Caddy automatically sets `X-Forwarded-For`, `X-Forwarded-Proto`, and `X-Forwarded-Host` on `reverse_proxy`. Reference routing on `manta_net`:

```caddyfile
manta.{$MANTA_DOMAIN} {
    reverse_proxy web:3000
}

api.manta.{$MANTA_DOMAIN} {
    reverse_proxy app:8000
}

auth.manta.{$MANTA_DOMAIN} {
    reverse_proxy keycloak:8080
}
```

---

## 5. Production Launch & Verification Sequence

Requires **Docker Compose v2.24+**:

```bash
# 1. Validate merged production configuration & fail-fast secret checks
docker compose --env-file backend/.env.prod \
  -f backend/docker-compose.yml \
  -f backend/docker-compose.prod.yml config > /dev/null

# 2. Deploy production stack (renders realm -> runs alembic only -> boots services)
docker compose --env-file backend/.env.prod \
  -f backend/docker-compose.yml \
  -f backend/docker-compose.prod.yml up -d --build

# 3. Verify Keycloak OIDC discovery reports HTTPS issuer & endpoints
curl -sf "https://${KC_HOSTNAME}/realms/manta/.well-known/openid-configuration" | jq '{issuer, authorization_endpoint, token_endpoint, end_session_endpoint}'

# 4. Verify zero dev seed rows on a fresh database
docker compose --env-file backend/.env.prod \
  -f backend/docker-compose.yml \
  -f backend/docker-compose.prod.yml exec -T postgres \
  psql -U manta_user -d manta_db -c "SELECT count(*) FROM tenants; SELECT count(*) FROM users;"
```

> [!NOTE]
> **PgBouncer `auth_user` Coupling:** `infra/pgbouncer/pgbouncer.ini` sets `auth_user = manta_user`. Keep `POSTGRES_USER=manta_user` with a rotated strong `POSTGRES_PASSWORD`, and rotate `ENG_APP_DB_PASSWORD` separately for `eng_app_user`. `scripts/run_prod_migrations.py` automatically synchronizes `eng_app_user`'s SCRAM-SHA-256 password in PostgreSQL after `alembic upgrade head`.
