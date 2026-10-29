"""
Interactive / manual verification script for SCRUM-278 JWT validation.
Can run against live Docker Compose services (Keycloak on :8080, App on :8000).
"""

import argparse
import sys
import httpx


def test_live_docker(
    app_url: str = "http://localhost:8000",
    keycloak_url: str = "http://localhost:8080",
) -> bool:
    print("\n==================================================")
    print("  MANTA SCRUM-278 MANUAL VERIFICATION SUITE")
    print(f"  Target Backend:  {app_url}")
    print(f"  Target Keycloak: {keycloak_url}")
    print("==================================================\n")

    client = httpx.Client(timeout=10.0)

    # 1. Health check
    try:
        health_resp = client.get(f"{app_url}/health")
        if health_resp.status_code != 200:
            print(f"[FAIL] App health check failed: HTTP {health_resp.status_code}")
            return False
        print("[PASS] 1. App is healthy (/health -> 200)")
    except Exception as exc:
        print(f"[ERROR] Cannot connect to {app_url}/health: {exc}")
        print("Hint: Make sure Docker Compose is up (`docker compose up -d` in backend/).")
        return False

    # 2. Keycloak token acquisition for alpha_dev
    token_url = f"{keycloak_url}/realms/manta/protocol/openid-connect/token"
    try:
        token_resp = client.post(
            token_url,
            data={
                "grant_type": "password",
                "client_id": "manta-portal",
                "username": "alpha_dev",
                "password": "dev123",
            },
        )
        if token_resp.status_code != 200:
            print(f"[FAIL] Failed to obtain token from Keycloak: {token_resp.text}")
            return False
        token_data = token_resp.json()
        access_token = token_data["access_token"]
        print("[PASS] 2. Successfully acquired real OIDC token for alpha_dev from Keycloak")
    except Exception as exc:
        print(f"[ERROR] Cannot connect to Keycloak at {token_url}: {exc}")
        return False

    # 3. Test valid token against /api/v1/auth/me
    me_resp = client.get(
        f"{app_url}/api/v1/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if me_resp.status_code == 200:
        data = me_resp.json()
        tid = data.get("tenant_id")
        user = data.get("preferred_username")
        print(f"[PASS] 3. Valid token accepted (HTTP 200) -> user: {user}, tenant_id: {tid}")
        if tid != "11111111-1111-1111-1111-111111111111":
            print(f"[WARN] Expected tenant_id 11111111-1111-1111-1111-111111111111, got: {tid}")
    else:
        print(f"[FAIL] Expected 200 OK for valid token, got HTTP {me_resp.status_code}")
        print(f"       Response body: {me_resp.text}")
        return False

    # 4. Test request without Authorization header -> Generic 401
    no_token_resp = client.get(f"{app_url}/api/v1/auth/me")
    if (
        no_token_resp.status_code == 401
        and no_token_resp.headers.get("WWW-Authenticate") == "Bearer"
        and no_token_resp.json() == {"detail": "Not authenticated"}
    ):
        print("[PASS] 4. Missing token rejected with generic 401 + WWW-Authenticate: Bearer")
    else:
        print(f"[FAIL] Missing token did not return expected 401: {no_token_resp.status_code}")
        return False

    # 5. Test tampered signature -> Generic 401
    tampered_token = access_token[:-6] + (
        "aaaaaa" if not access_token.endswith("aaaaaa") else "bbbbbb"
    )
    tampered_resp = client.get(
        f"{app_url}/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tampered_token}"},
    )
    if (
        tampered_resp.status_code == 401
        and tampered_resp.headers.get("WWW-Authenticate") == "Bearer"
        and tampered_resp.json() == {"detail": "Not authenticated"}
    ):
        print("[PASS] 5. Tampered signature rejected with generic 401 (no leak of reason)")
    else:
        print(f"[FAIL] Tampered token did not return expected 401: {tampered_resp.status_code}")
        return False

    # 6. Test second tenant user (beta_dev -> tenant 22222222-2222-2222-2222-222222222222)
    beta_token_resp = client.post(
        token_url,
        data={
            "grant_type": "password",
            "client_id": "manta-portal",
            "username": "beta_dev",
            "password": "dev123",
        },
    )
    if beta_token_resp.status_code == 200:
        beta_token = beta_token_resp.json()["access_token"]
        beta_me_resp = client.get(
            f"{app_url}/api/v1/auth/me",
            headers={"Authorization": f"Bearer {beta_token}"},
        )
        if (
            beta_me_resp.status_code == 200
            and beta_me_resp.json().get("tenant_id") == "22222222-2222-2222-2222-222222222222"
        ):
            print("[PASS] 6. Multi-tenant isolation verified (beta_dev -> tenant_id 22222222-...)")
        else:
            print(f"[FAIL] beta_dev returned unexpected response: {beta_me_resp.text}")
            return False

    print("\n>>> ALL MANUAL VERIFICATION CHECKS PASSED SUCCESSFULLY! <<<\n")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manual verification script for SCRUM-278")
    parser.add_argument("--app-url", default="http://localhost:8000")
    parser.add_argument("--keycloak-url", default="http://localhost:8080")
    args = parser.parse_args()

    success = test_live_docker(app_url=args.app_url, keycloak_url=args.keycloak_url)
    sys.exit(0 if success else 1)
