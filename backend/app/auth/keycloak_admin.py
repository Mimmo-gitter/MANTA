import asyncio
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


class KeycloakError(Exception):
    def __init__(self, message: str):
        self.message = message


class UserAlreadyExistsError(KeycloakError):
    pass


def get_issuer_url() -> str:
    return f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}"


async def get_admin_token(client: httpx.AsyncClient) -> str:
    response = await client.post(
        f"{get_issuer_url()}/protocol/openid-connect/token",
        data={
            "grant_type": "client_credentials",
            "client_id": settings.KC_SERVICE_CLIENT_ID,
            "client_secret": settings.KC_SERVICE_CLIENT_SECRET,
        },
    )
    if response.status_code != 200:
        raise KeycloakError("Не вдалося отримати токен сервісного акаунта Keycloak")
    return response.json()["access_token"]


async def find_user_id_by_email(client: httpx.AsyncClient, token: str, email: str):
    response = await client.get(
        f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users",
        headers={"Authorization": f"Bearer {token}"},
        params={"email": email, "exact": "true"},
    )
    if response.status_code != 200:
        return None
    users = response.json()
    if len(users) == 0:
        return None
    return users[0]["id"]


async def create_user(email: str, tenant_id: str) -> str:
    """Створює користувача в Keycloak з атрибутом tenant_id. Повертає id користувача в Keycloak."""
    last_error_message = "Keycloak недоступний, спробуйте пізніше"

    async with httpx.AsyncClient(timeout=10) as client:
        for attempt in range(MAX_RETRIES):
            try:
                token = await get_admin_token(client)
                response = await client.post(
                    f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "username": email,
                        "email": email,
                        "enabled": True,
                        "emailVerified": True,
                        "attributes": {"tenant_id": [str(tenant_id)]},
                    },
                )

                if response.status_code == 201:
                    return response.headers["Location"].rsplit("/", 1)[-1]

                if response.status_code == 409:
                    # Якщо це повторна спроба, можливо, перший запит встиг створити користувача
                    if attempt > 0:
                        existing_id = await find_user_id_by_email(client, token, email)
                        if existing_id:
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


async def send_set_password_email(keycloak_user_id: str) -> None:
    """Keycloak сам надсилає лист із посиланням на встановлення пароля."""
    async with httpx.AsyncClient(timeout=10) as client:
        token = await get_admin_token(client)
        response = await client.put(
            f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}"
            f"/users/{keycloak_user_id}/execute-actions-email",
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
                f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users/{keycloak_user_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
    except Exception:
        logger.error("Не вдалося видалити користувача Keycloak %s при відкаті", keycloak_user_id)


async def provision_user(email: str, tenant_id: str) -> str:
    """Створює користувача і надсилає лист. Якщо лист не пішов, користувача видаляє."""
    keycloak_user_id = await create_user(email, tenant_id)
    try:
        await send_set_password_email(keycloak_user_id)
    except KeycloakError:
        await delete_user(keycloak_user_id)
        raise
    return keycloak_user_id
