from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

import pytest
from pydantic import ValidationError
from app.mobile.schemas import RegisterMobileDeviceRequest
from app.mobile.service import resolve_or_create_mobile_device


def test_register_mobile_device_request_defaults_and_trims_metadata() -> None:
    request = RegisterMobileDeviceRequest(
        fcm_token="fcm-registration-token",
        device_name="  Pixel  ",
        app_version="  1.2.3  ",
    )

    assert request.platform == "android"
    assert request.device_name == "Pixel"
    assert request.app_version == "1.2.3"


def test_register_mobile_device_request_accepts_field_limits() -> None:
    request = RegisterMobileDeviceRequest(
        fcm_token="t" * 512,
        platform="p" * 32,
        device_name="d" * 128,
        app_version="v" * 32,
    )

    assert len(request.fcm_token) == 512
    assert len(request.device_name or "") == 128


@pytest.mark.parametrize(
    "payload",
    [
        {"fcm_token": " "},
        {"fcm_token": " token "},
        {"fcm_token": "t" * 513},
        {"fcm_token": "token", "platform": " "},
        {"fcm_token": "token", "device_name": " "},
        {"fcm_token": "token", "app_version": "v" * 33},
        {"fcm_token": "token", "tenant_id": "client-supplied"},
    ],
)
def test_register_mobile_device_request_rejects_invalid_values(
    payload: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        RegisterMobileDeviceRequest(**payload)


@pytest.mark.asyncio
async def test_register_mobile_device_returns_only_non_critical_device_data() -> None:
    now = datetime.now(timezone.utc)
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    device = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        fcm_token="sensitive-fcm-token",
        platform="ios",
        device_name="iPhone",
        app_version="1.2.3",
        is_active=True,
        last_seen_at=now,
        created_at=now,
        updated_at=now,
    )

    class FakeSession:
        async def execute(self, _statement: object) -> SimpleNamespace:
            assert _statement is not None
            return SimpleNamespace(scalar_one=lambda: device)

    response = await resolve_or_create_mobile_device(
        FakeSession(),  # type: ignore[arg-type]
        tenant_id,
        user_id,
        RegisterMobileDeviceRequest(fcm_token="sensitive-fcm-token", platform="ios"),
    )

    assert response.platform == "ios"
    assert response.device_name == "iPhone"
    assert response.model_dump() == {
        "id": device.id,
        "platform": "ios",
        "device_name": "iPhone",
        "app_version": "1.2.3",
        "is_active": True,
        "last_seen_at": now,
        "created_at": now,
        "updated_at": now,
    }
