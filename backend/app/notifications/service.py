import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

INVITATION_TTL_HOURS = 48

ALLOWED_ROLES = {
    "DEVELOPER",
    "SCRUM_MASTER",
    "PROJECT_MANAGER",
    "SOLUTION_ARCHITECT",
    "DELIVERY_DIRECTOR",
    "TOP_MANAGEMENT",
    "TENANT_ADMIN",
}


class InvitationError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()  # 64 символи = VARCHAR(64)


async def create_invitation(session, tenant_id, invited_by, email, role) -> str:
    role = role.upper()
    if role not in ALLOWED_ROLES:
        raise InvitationError(422, "Невідома роль")

    email = email.strip().lower()
    if len(email) > 255:
        raise InvitationError(422, "Email задовгий")

    raw_token = secrets.token_urlsafe(32)  # 256 біт ентропії
    expires_at = datetime.now(timezone.utc) + timedelta(hours=INVITATION_TTL_HOURS)
    await session.execute(
        text("""INSERT INTO invitations (tenant_id, email, role, token_hash, invited_by, expires_at)
                VALUES (:tenant_id, :email, :role, :token_hash, :invited_by, :expires_at)"""),
        {
            "tenant_id": tenant_id,
            "email": email,
            "role": role,
            "token_hash": hash_token(raw_token),
            "invited_by": invited_by,
            "expires_at": expires_at,
        },
    )
    return raw_token


async def consume_invitation(session, raw_token: str):
    """Атомарно позначає запрошення використаним через SECURITY DEFINER функцію
    (RLS без tenant_id напряму не дозволив би знайти рядок)."""
    result = await session.execute(
        text("SELECT * FROM consume_invitation(:token_hash)"),
        {"token_hash": hash_token(raw_token)},
    )
    row = result.mappings().first()

    if row["r_status"] == "ok":
        return {
            "id": row["r_id"],
            "tenant_id": row["r_tenant_id"],
            "email": row["r_email"],
            "role": row["r_role"],
        }
    if row["r_status"] == "not_found":
        raise InvitationError(404, "Недійсне посилання-запрошення")
    if row["r_status"] == "used":
        raise InvitationError(410, "Це запрошення вже використано")
    raise InvitationError(410, "Термін дії запрошення закінчився")
