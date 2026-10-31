# Keycloak Local Identity Provider Setup

## Overview
This document describes the local Keycloak identity provider configuration for MANTA, pre-loaded with the `manta` test realm, claim mappers, and multi-tenant test users for seamless local development and automated OIDC/OAuth2 testing.

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

### 1. Clients & Protocol Mappers
- **`manta-portal`**: Public client configured for Next.js frontend authentication (PKCE S256, Authorization Code Flow, redirect URIs: `http://localhost:3000/*`, `http://localhost:8000/*`, `http://localhost:8080/*`).
  - **`tenant_id` claim mapper**: (`oidc-usermodel-attribute-mapper`) maps user attribute `tenant_id` into the JWT ID and Access Tokens as a string claim, enabling database Row-Level Security (RLS) enforcement.
  - **`audience` mapper**: (`oidc-audience-mapper`) injects `manta-backend` into the `aud` claim so FastAPI token verification accepts the token.
- **`manta-backend`**: Bearer-only client for backend API resource server verification.

### 2. RBAC Roles (SRS §2.3 & Architecture Blueprint Section 6.2)
- `DEVELOPER`: Core engineering workflows, PR checks, AST/SCIP call trees, refactoring triggers.
- `SCRUM_MASTER`: Sprint velocity, burndown charts, PR reviewer reassignment, blocker mitigation.
- `PROJECT_MANAGER`: Sprint planning, delivery tracking, two-stage Acceptance Criteria scope creep alerts.
- `SOLUTION_ARCHITECT`: Architecture review gate, ADR approvals and publishing, technical debt analysis.
- `DELIVERY_DIRECTOR`: Portfolio delivery health oversight, blocker intervention, reviewer reassignment. *(Supersedes legacy `DELIVERY_EXEC` alias)*.
- `TOP_MANAGEMENT`: High-level strategic read-only portfolio governance and deadline risk metrics.
- `TENANT_ADMIN`: Full administrative control across the tenant, user provisioning, webhook credentials, and GDPR Article 17 erasure.

### 3. Multi-Tenant Test Users
The test realm pre-seeds users across two distinct tenants to validate multi-tenant isolation:

#### Tenant Alpha (`11111111-1111-1111-1111-111111111111`)
| Username | Password | Roles | `tenant_id` | Purpose |
|---|---|---|---|---|
| `alpha_admin` | `admin123` | `TENANT_ADMIN`, `SOLUTION_ARCHITECT` | `11111111-1111-1111-1111-111111111111` | Tenant A administrator & ADR gate |
| `alpha_dev` | `dev123` | `DEVELOPER` | `11111111-1111-1111-1111-111111111111` | Tenant A developer |
| `alpha_scrum` | `scrum123` | `SCRUM_MASTER` | `11111111-1111-1111-1111-111111111111` | Tenant A scrum master |
| `alpha_pm` | `pm123` | `PROJECT_MANAGER` | `11111111-1111-1111-1111-111111111111` | Tenant A project manager |
| `alpha_director` | `director123` | `DELIVERY_DIRECTOR` | `11111111-1111-1111-1111-111111111111` | Tenant A delivery director |
| `alpha_top` | `top123` | `TOP_MANAGEMENT` | `11111111-1111-1111-1111-111111111111` | Tenant A top management |
| `alpha_architect` | `architect123` | `SOLUTION_ARCHITECT` | `11111111-1111-1111-1111-111111111111` | Tenant A solution architect |

#### Tenant Beta (`22222222-2222-2222-2222-222222222222`)
| Username | Password | Roles | `tenant_id` | Purpose |
|---|---|---|---|---|
| `beta_admin` | `admin123` | `TENANT_ADMIN` | `22222222-2222-2222-2222-222222222222` | Tenant B administrator |
| `beta_dev` | `dev123` | `DEVELOPER` | `22222222-2222-2222-2222-222222222222` | Tenant B developer |
| `beta_exec` | `exec123` | `DELIVERY_DIRECTOR` | `22222222-2222-2222-2222-222222222222` | Tenant B delivery director |

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

## Token Issuance & Claim Testing
To request a token for a tenant user via Direct Access Grants (Resource Owner Password Credentials) for testing:
```bash
curl -s -X POST http://localhost:8080/realms/manta/protocol/openid-connect/token \
  -d "client_id=manta-portal" \
  -d "grant_type=password" \
  -d "username=alpha_dev" \
  -d "password=dev123"
```
The decoded access token will include:
```json
{
  "iss": "http://localhost:8080/realms/manta",
  "aud": ["manta-backend", "account"],
  "tenant_id": "11111111-1111-1111-1111-111111111111",
  "realm_access": {
    "roles": ["DEVELOPER"]
  }
}
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
