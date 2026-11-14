import asyncio
import logging
import uuid
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
_TENANT_PROFILE_CONFIGURED = False

MANTA_SYSTEM_ROLES: frozenset[str] = frozenset(
    {
        "TENANT_ADMIN",
        "DELIVERY_DIRECTOR",
        "DELIVERY_EXEC",
        "PROJECT_MANAGER",
        "SCRUM_MASTER",
        "SOLUTION_ARCHITECT",
        "DEVELOPER",
        "TOP_MANAGEMENT",
    }
)


class KeycloakError(Exception):
    def __init__(self, message: str):
        self.message = message


class UserAlreadyExistsError(KeycloakError):
    pass


def get_issuer_url() -> str:
    return f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}"


def _admin_users_url() -> str:
    return f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users"


def _admin_roles_url() -> str:
    return f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/roles"


def _is_uuid(value: str | None) -> bool:
    if not value:
        return False
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def _split_full_name(full_name: str | None) -> tuple[str | None, str | None]:
    if not full_name or not full_name.strip():
        return None, None
    parts = full_name.strip().split(None, 1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else ""
    return first_name, last_name


async def get_admin_token(client: httpx.AsyncClient) -> str:
    if settings.KC_SERVICE_CLIENT_SECRET:
        response = await client.post(
            f"{get_issuer_url()}/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": settings.KC_SERVICE_CLIENT_ID,
                "client_secret": settings.KC_SERVICE_CLIENT_SECRET,
            },
        )
        if response.status_code == 200:
            return response.json()["access_token"]

    if settings.KEYCLOAK_ADMIN and settings.KEYCLOAK_ADMIN_PASSWORD:
        admin_response = await client.post(
            f"{settings.KEYCLOAK_URL.rstrip('/')}/realms/{settings.KEYCLOAK_ADMIN_REALM}/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": settings.KEYCLOAK_ADMIN,
                "password": settings.KEYCLOAK_ADMIN_PASSWORD,
            },
        )
        if admin_response.status_code == 200:
            return admin_response.json()["access_token"]

    raise KeycloakError("Не вдалося отримати токен сервісного акаунта Keycloak")


async def _ensure_tenant_id_profile_attribute(
    client: httpx.AsyncClient, token: str
) -> None:
    """Ensure Keycloak 24+ Declarative User Profile allows persisting `tenant_id`."""
    global _TENANT_PROFILE_CONFIGURED
    if _TENANT_PROFILE_CONFIGURED:
        return

    profile_url = f"{settings.KEYCLOAK_URL.rstrip('/')}/admin/realms/{settings.KEYCLOAK_REALM}/users/profile"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        prof_resp = await client.get(profile_url, headers=headers)
        if prof_resp.status_code != 200:
            return
        profile = prof_resp.json()
        attrs = profile.setdefault("attributes", [])
        if any(a.get("name") == "tenant_id" for a in attrs):
            _TENANT_PROFILE_CONFIGURED = True
            return
        attrs.append(
            {
                "name": "tenant_id",
                "displayName": "Tenant ID",
                "permissions": {"view": ["admin", "user"], "edit": ["admin"]},
                "multivalued": False,
            }
        )
        put_resp = await client.put(profile_url, headers=headers, json=profile)
        if put_resp.status_code in (200, 204):
            _TENANT_PROFILE_CONFIGURED = True
    except Exception as exc:
        logger.debug("Skipping Keycloak User Profile tenant_id check: %s", exc)


async def find_user_id_by_email(
    client: httpx.AsyncClient, token: str, email: str
) -> str | None:
    response = await client.get(
        _admin_users_url(),
        headers={"Authorization": f"Bearer {token}"},
        params={"email": email, "exact": "true"},
    )
    if response.status_code != 200:
        return None
    users = response.json()
    if len(users) == 0:
        return None
    return users[0]["id"]


async def _resolve_keycloak_user_id(
    client: httpx.AsyncClient,
    token: str,
    email: str | None = None,
    google_sub: str | None = None,
) -> str | None:
    if _is_uuid(google_sub):
        res = await client.get(
            f"{_admin_users_url()}/{google_sub}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if res.status_code == 200:
            return google_sub
    if email:
        return await find_user_id_by_email(client, token, email)
    return None


async def assign_realm_role(
    client: httpx.AsyncClient,
    token: str,
    keycloak_user_id: str,
    role_name: str,
) -> None:
    """Assigns a realm role (removing stale MANTA system roles) to a Keycloak user."""
    normalized_role = role_name.strip().upper()
    role_resp = await client.get(
        f"{_admin_roles_url()}/{normalized_role}",
        headers={"Authorization": f"Bearer {token}"},
    )
    if role_resp.status_code != 200:
        raise KeycloakError(f"Роль '{normalized_role}' не знайдена в Keycloak ({role_resp.status_code})")
    role_rep = role_resp.json()

    current_resp = await client.get(
        f"{_admin_users_url()}/{keycloak_user_id}/role-mappings/realm",
        headers={"Authorization": f"Bearer {token}"},
    )
    if current_resp.status_code == 200:
        stale_roles = [
            r
            for r in current_resp.json()
            if r.get("name") in MANTA_SYSTEM_ROLES and r.get("name") != normalized_role
        ]
        if stale_roles:
            await client.request(
                "DELETE",
                f"{_admin_users_url()}/{keycloak_user_id}/role-mappings/realm",
                headers={"Authorization": f"Bearer {token}"},
                json=stale_roles,
            )

    assign_resp = await client.post(
        f"{_admin_users_url()}/{keycloak_user_id}/role-mappings/realm",
        headers={"Authorization": f"Bearer {token}"},
        json=[{"id": role_rep["id"], "name": role_rep["name"]}],
    )
    if assign_resp.status_code not in (200, 204):
        raise KeycloakError(
            f"Не вдалося призначити роль '{normalized_role}' у Keycloak ({assign_resp.status_code})"
        )


async def create_user(
    email: str,
    tenant_id: str,
    full_name: str | None = None,
    password: str | None = None,
    role: str | None = None,
) -> str:
    """Створює користувача в Keycloak з атрибутом tenant_id та опційними паролем/роллю."""
    last_error_message = "Keycloak недоступний, спробуйте пізніше"
    first_name, last_name = _split_full_name(full_name)

    payload: dict[str, Any] = {
        "username": email,
        "email": email,
        "enabled": True,
        "emailVerified": True,
        "attributes": {"tenant_id": [str(tenant_id)]},
    }
    if first_name is not None:
        payload["firstName"] = first_name
    if last_name is not None:
        payload["lastName"] = last_name
    if password is not None:
        payload["credentials"] = [
            {"type": "password", "value": password, "temporary": False}
        ]
        payload["requiredActions"] = []

    async with httpx.AsyncClient(timeout=10) as client:
        for attempt in range(MAX_RETRIES):
            try:
                token = await get_admin_token(client)
                await _ensure_tenant_id_profile_attribute(client, token)
                response = await client.post(
                    _admin_users_url(),
                    headers={"Authorization": f"Bearer {token}"},
                    json=payload,
                )

                if response.status_code == 201:
                    kc_user_id = response.headers["Location"].rsplit("/", 1)[-1]
                    if role:
                        await assign_realm_role(client, token, kc_user_id, role)
                    return kc_user_id

                if response.status_code == 409:
                    if attempt > 0:
                        existing_id = await find_user_id_by_email(client, token, email)
                        if existing_id:
                            if role:
                                await assign_realm_role(client, token, existing_id, role)
                            return existing_id
                    raise UserAlreadyExistsError(
                        "Користувач із таким email уже існує в системі входу"
                    )

                if response.status_code < 500:
                    raise KeycloakError(f"Keycloak відхилив запит ({response.status_code})")

                last_error_message = f"Keycloak повернув помилку {response.status_code}"

            except (httpx.TransportError, httpx.TimeoutException):
                last_error_message = "Keycloak недоступний, спробуйте пізніше"

            await asyncio.sleep(2**attempt)  # 1с, 2с, 4с

    raise KeycloakError(last_error_message)


async def sync_user_realm_role(
    email: str | None,
    new_role: str,
    google_sub: str | None = None,
) -> bool:
    """Best-effort synchronization of a user's MANTA role to Keycloak realm role mappings."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            token = await get_admin_token(client)
            await _ensure_tenant_id_profile_attribute(client, token)
            kc_user_id = await _resolve_keycloak_user_id(
                client, token, email=email, google_sub=google_sub
            )
            if not kc_user_id:
                logger.warning(
                    "Keycloak user not found for role sync (email=%s, sub=%s)", email, google_sub
                )
                return False
            await assign_realm_role(client, token, kc_user_id, new_role)
            return True
    except Exception as exc:
        logger.warning("Keycloak role sync skipped/failed for %s: %s", email or google_sub, exc)
        return False


async def disable_user(email: str | None, google_sub: str | None = None) -> bool:
    """Disables a user account in Keycloak when removed from workspace."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            token = await get_admin_token(client)
            await _ensure_tenant_id_profile_attribute(client, token)
            kc_user_id = await _resolve_keycloak_user_id(
                client, token, email=email, google_sub=google_sub
            )
            if not kc_user_id:
                return False
            resp = await client.put(
                f"{_admin_users_url()}/{kc_user_id}",
                headers={"Authorization": f"Bearer {token}"},
                json={"enabled": False},
            )
            return resp.status_code in (200, 204)
    except Exception as exc:
        logger.warning("Keycloak disable_user skipped/failed for %s: %s", email or google_sub, exc)
        return False


async def switch_user_tenant_context(
    email: str | None,
    google_sub: str | None,
    target_tenant_id: str,
    target_role: str,
) -> bool:
    """Updates user's tenant_id attribute and realm role in Keycloak for workspace switching."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            token = await get_admin_token(client)
            await _ensure_tenant_id_profile_attribute(client, token)
            kc_user_id = await _resolve_keycloak_user_id(
                client, token, email=email, google_sub=google_sub
            )
            if not kc_user_id:
                return False
            user_resp = await client.get(
                f"{_admin_users_url()}/{kc_user_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if user_resp.status_code != 200:
                return False
            user_rep = user_resp.json()
            attrs = dict(user_rep.get("attributes") or {})
            attrs["tenant_id"] = [str(target_tenant_id)]
            update_resp = await client.put(
                f"{_admin_users_url()}/{kc_user_id}",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "username": user_rep.get("username"),
                    "email": user_rep.get("email"),
                    "firstName": user_rep.get("firstName"),
                    "lastName": user_rep.get("lastName"),
                    "enabled": user_rep.get("enabled", True),
                    "emailVerified": user_rep.get("emailVerified", True),
                    "attributes": attrs,
                },
            )
            if update_resp.status_code not in (200, 204):
                return False
            await assign_realm_role(client, token, kc_user_id, target_role)
            return True
    except Exception as exc:
        logger.warning("Keycloak workspace switch skipped/failed: %s", exc)
        return False


async def send_set_password_email(keycloak_user_id: str) -> None:
    """Keycloak сам надсилає лист із посиланням на встановлення пароля."""
    async with httpx.AsyncClient(timeout=10) as client:
        token = await get_admin_token(client)
        response = await client.put(
            f"{_admin_users_url()}/{keycloak_user_id}/execute-actions-email",
            headers={"Authorization": f"Bearer {token}"},
            json=["UPDATE_PASSWORD"],
        )
        if response.status_code != 204:
            raise KeycloakError("Не вдалося надіслати лист для встановлення пароля")


async def delete_user(keycloak_user_id: str) -> None:
    """Прибирає користувача при відкаті. Помилки тільки логуються."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            token = await get_admin_token(client)
            await client.delete(
                f"{_admin_users_url()}/{keycloak_user_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
    except Exception:
        logger.error("Не вдалося видалити користувача Keycloak %s при відкаті", keycloak_user_id)


async def provision_user(email: str, tenant_id: str, role: str | None = None) -> str:
    """Створює користувача і надсилає лист. Якщо лист не пішов, користувача видаляє."""
    keycloak_user_id = await create_user(email, tenant_id, role=role)
    try:
        await send_set_password_email(keycloak_user_id)
    except KeycloakError:
        await delete_user(keycloak_user_id)
        raise
    return keycloak_user_id
