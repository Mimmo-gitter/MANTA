# Keycloak Local Identity Provider Setup

## Overview
This document describes the local Keycloak identity provider configuration for MANTA, pre-loaded with the `manta` test realm and default client configurations for seamless local development and automated OIDC/OAuth2 testing.

## Local Ports & Endpoints
| Component / Service | Local URL | Description |
|---|---|---|
| **Keycloak Root** | `http://localhost:8080` | Keycloak base HTTP service |
| **Admin Console** | `http://localhost:8080/admin/` | Keycloak Web Administration UI |
| **OIDC Discovery** | `http://localhost:8080/realms/manta/.well-known/openid-configuration` | OpenID Connect discovery document |
| **JWKS Endpoint** | `http://localhost:8080/realms/manta/protocol/openid-connect/certs` | Public RS256 JWKS keys for token verification |
| **Token Endpoint** | `http://localhost:8080/realms/manta/protocol/openid-connect/token` | OAuth2 token exchange endpoint |
| **Health Check** | `http://localhost:8080/health/ready` | Readiness probe endpoint |

## Admin Credentials & Environment Configuration
Keycloak configuration variables are defined in `backend/.env.example`:
```bash
# Keycloak Identity Provider Settings
KEYCLOAK_PORT=8080
KEYCLOAK_ADMIN=admin
KEYCLOAK_ADMIN_PASSWORD=admin
KEYCLOAK_DB_NAME=keycloak
KEYCLOAK_REALM=manta
```

## Pre-Loaded Test Realm (`manta`)
The realm definition is maintained in `infra/keycloak/realm-export.json` and mounted into `/opt/keycloak/data/import/realm-export.json` in the container. When Keycloak boots with `start-dev --import-realm`, it automatically provisions:

### 1. Clients
- **`manta-portal`**: Public client configured for Next.js frontend authentication (PKCE S256, Authorization Code Flow, redirect URIs: `http://localhost:3000/*`, `http://localhost:8000/*`, `http://localhost:8080/*`).
- **`manta-backend`**: Bearer-only client for backend API resource server verification.

### 2. RBAC Roles (Architecture Blueprint Section 6.2)
- `DEVELOPER`: Core engineering workflows, PR checks, refactoring triggers.
- `SCRUM_MASTER`: Sprint tracking, PR reviewer reassignment, blocker mitigation.
- `SOLUTION_ARCHITECT`: Architecture review gate, ADR approvals and publishing.
- `DELIVERY_EXEC`: Platform and DORA metric oversight.
- `TENANT_ADMIN`: Full administrative control across the tenant.

### 3. Seed Test Users
| Username | Password | Roles | Purpose |
|---|---|---|---|
| `admin` | `admin123` | `TENANT_ADMIN`, `SOLUTION_ARCHITECT` | Full admin access & architecture gate |
| `developer` | `dev123` | `DEVELOPER` | Standard developer workflows |
| `architect` | `architect123` | `SOLUTION_ARCHITECT` | Architecture Decision Records & verification |

## Running Keycloak via Docker Compose
To start the entire local stack including Keycloak:
```bash
cd backend
docker compose up -d
```

Verify service status:
```bash
docker compose ps keycloak
```

Check OIDC discovery document:
```bash
curl -s http://localhost:8080/realms/manta/.well-known/openid-configuration
```

## Reproducibility from Scratch
To verify that Keycloak reproduces the test realm without any manual configuration:
```bash
# 1. Tear down the stack and volumes
docker compose down -v

# 2. Re-create the stack
docker compose up -d

# 3. Verify realm is immediately active
curl -s http://localhost:8080/realms/manta/.well-known/openid-configuration | grep "manta"
```
Keycloak automatically imports the realm on boot from the mounted JSON file.
