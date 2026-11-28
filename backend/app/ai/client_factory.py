"""Dual-mode AI client factory enforcing Local-Only vs Cloud ZDR outbound policies (SCRUM-560 / US-62)."""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
import ipaddress
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import settings

AI_MODE_LOCAL_ONLY = "local_only"
AI_MODE_CLOUD_ZDR = "cloud_zdr"
VALID_AI_PROCESSING_MODES: tuple[str, str] = (AI_MODE_LOCAL_ONLY, AI_MODE_CLOUD_ZDR)

LOCAL_EMBEDDING_DIMENSIONS = 384
CLOUD_ZDR_EMBEDDING_DIMENSIONS = 1536

_SINGLE_LABEL_HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


class AIClientError(RuntimeError):
    """Base error raised by AI provider routing or inference clients."""


class LocalOnlyPolicyViolationError(AIClientError):
    """Raised fail-closed when local_only policy encounters an external WAN endpoint."""


class CloudPolicyViolationError(AIClientError):
    """Raised fail-closed when a Cloud LLM call is attempted without active cloud_zdr policy."""


class AIClientConfigurationError(AIClientError):
    """Raised when required AI provider settings (e.g., Cloud ZDR URL/API key) are missing."""


@dataclass(frozen=True, slots=True)
class ChatCompletionResult:
    """Structured chat completion text paired with token usage metadata."""

    content: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    model: str | None = None


def normalize_ai_processing_mode(mode: str | None) -> str:
    """Normalize tenant ai_processing_mode strings into canonical constants."""
    if mode is None:
        raise ValueError("ai_processing_mode must not be None")
    cleaned = mode.strip().lower()
    if cleaned in ("local_only", "local"):
        return AI_MODE_LOCAL_ONLY
    if cleaned == "cloud_zdr":
        return AI_MODE_CLOUD_ZDR
    raise ValueError(
        f"Unsupported ai_processing_mode '{mode}'. Expected one of {VALID_AI_PROCESSING_MODES}."
    )


def get_mode_metadata(mode: str) -> dict[str, Any]:
    """Return vector column, dimensions, and active model names for a processing mode."""
    normalized = normalize_ai_processing_mode(mode)
    if normalized == AI_MODE_LOCAL_ONLY:
        return {
            "mode": AI_MODE_LOCAL_ONLY,
            "embedding_dimensions": LOCAL_EMBEDDING_DIMENSIONS,
            "embedding_column": "embedding_384",
            "embedding_model": settings.LOCAL_EMBEDDING_MODEL,
            "llm_model": settings.LOCAL_LLM_MODEL,
        }
    return {
        "mode": AI_MODE_CLOUD_ZDR,
        "embedding_dimensions": CLOUD_ZDR_EMBEDDING_DIMENSIONS,
        "embedding_column": "embedding_1536",
        "embedding_model": settings.CLOUD_ZDR_EMBEDDING_MODEL,
        "llm_model": settings.CLOUD_ZDR_LLM_MODEL,
    }


def _is_permitted_local_hostname(hostname: str) -> bool:
    host = hostname.strip().lower().rstrip(".")
    if not host:
        return False

    if host in {"localhost", "host.docker.internal"}:
        return True
    if host.endswith(".localhost") or host.endswith(".local") or host.endswith(".internal"):
        return True

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None

    if ip is not None:
        return bool(
            (ip.is_loopback or ip.is_private or ip.is_link_local)
            and not ip.is_multicast
            and not ip.is_unspecified
        )

    # Allow single-label internal service names inside Docker / cluster DNS (e.g. `ollama`, `vllm`).
    return "." not in host and bool(_SINGLE_LABEL_HOST_RE.match(host))


def assert_local_only_endpoint(base_url: str | None) -> str:
    """
    Validate that an AI endpoint is strictly loopback, private RFC1918/link-local, or internal DNS.

    Fails closed with LocalOnlyPolicyViolationError for any public IP or public FQDN so
    zero outbound WAN calls can ever leave the deployment perimeter in local_only mode.
    """
    raw = (base_url or "").strip()
    if not raw:
        raise LocalOnlyPolicyViolationError(
            "LOCAL_LLM_BASE_URL must be set to a local/internal endpoint when ai_processing_mode='local_only'."
        )

    parsed = urlparse(raw)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise LocalOnlyPolicyViolationError(
            f"Invalid local AI endpoint URL '{raw}': expected http(s) URL with hostname."
        )

    hostname = parsed.hostname.lower().rstrip(".")
    if not _is_permitted_local_hostname(hostname):
        raise LocalOnlyPolicyViolationError(
            f"Outbound WAN endpoint '{hostname}' is strictly forbidden when ai_processing_mode='local_only'."
        )
    return raw.rstrip("/")


class BaseAIClient(ABC):
    """Shared async client contract for OpenAI-compatible Local and Cloud ZDR inference."""

    mode: str
    allows_outbound_wan: bool
    embedding_dimensions: int
    embedding_column: str

    def __init__(
        self,
        *,
        base_url: str,
        llm_model: str,
        embedding_model: str,
        headers: dict[str, str] | None = None,
        trust_env: bool = True,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        event_hooks: dict[str, list[Any]] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.llm_model = llm_model
        self.embedding_model = embedding_model
        self._http = httpx.AsyncClient(
            base_url=f"{self.base_url}/",
            headers=headers or {},
            timeout=timeout if timeout is not None else settings.AI_REQUEST_TIMEOUT_SECONDS,
            trust_env=trust_env,
            follow_redirects=False,
            transport=transport,
            event_hooks=event_hooks,
        )

    async def __aenter__(self) -> BaseAIClient:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Generate and dimension-validate dense embeddings for one or more texts."""
        if not texts:
            return []

        response = await self._http.post(
            "embeddings",
            json={"model": self.embedding_model, "input": texts},
        )
        response.raise_for_status()
        payload = response.json()
        raw_items = payload.get("data")
        if not isinstance(raw_items, list) or len(raw_items) != len(texts):
            raise ValueError(
                f"Embedding response count mismatch: expected {len(texts)}, "
                f"got {len(raw_items) if isinstance(raw_items, list) else type(raw_items).__name__}."
            )

        ordered = sorted(raw_items, key=lambda item: int(item.get("index", 0)))
        vectors: list[list[float]] = []
        for idx, item in enumerate(ordered):
            vec = item.get("embedding")
            if not isinstance(vec, list) or len(vec) != self.embedding_dimensions:
                actual_dim = len(vec) if isinstance(vec, list) else None
                raise ValueError(
                    f"Embedding dimension mismatch at index {idx} for mode '{self.mode}': "
                    f"expected {self.embedding_dimensions}, got {actual_dim}."
                )
            vectors.append([float(v) for v in vec])
        return vectors

    async def chat_complete_with_usage(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> ChatCompletionResult:
        """Execute an OpenAI-compatible chat completion and return content + token usage."""
        body: dict[str, Any] = {
            "model": self.llm_model,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format is not None:
            body["response_format"] = response_format

        response = await self._http.post("chat/completions", json=body)
        response.raise_for_status()
        payload = response.json()
        choices = payload.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            raise ValueError("Chat completion response contained no choices.")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if content is None:
            raise ValueError("Chat completion choice message contained no content.")

        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}

        def _opt_int(key: str) -> int | None:
            val = usage.get(key)
            return int(val) if isinstance(val, (int, float)) and not isinstance(val, bool) else None

        return ChatCompletionResult(
            content=str(content),
            prompt_tokens=_opt_int("prompt_tokens"),
            completion_tokens=_opt_int("completion_tokens"),
            total_tokens=_opt_int("total_tokens"),
            model=str(payload.get("model")) if payload.get("model") is not None else self.llm_model,
        )

    async def chat_complete(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> str:
        """Execute an OpenAI-compatible chat completion request."""
        result = await self.chat_complete_with_usage(
            messages,
            response_format=response_format,
            temperature=temperature,
        )
        return result.content


class LocalAIClient(BaseAIClient):
    """384d local inference client (Ollama/vLLM) with fail-closed WAN isolation."""

    mode = AI_MODE_LOCAL_ONLY
    allows_outbound_wan = False
    embedding_dimensions = LOCAL_EMBEDDING_DIMENSIONS
    embedding_column = "embedding_384"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        llm_model: str | None = None,
        embedding_model: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        validated_url = assert_local_only_endpoint(
            base_url if base_url is not None else settings.LOCAL_LLM_BASE_URL
        )

        async def _guard_local_request(request: httpx.Request) -> None:
            assert_local_only_endpoint(str(request.url))

        super().__init__(
            base_url=validated_url,
            llm_model=llm_model or settings.LOCAL_LLM_MODEL,
            embedding_model=embedding_model or settings.LOCAL_EMBEDDING_MODEL,
            headers={"X-MANTA-AI-Mode": AI_MODE_LOCAL_ONLY},
            trust_env=False,
            timeout=timeout,
            transport=transport,
            event_hooks={"request": [_guard_local_request]},
        )


class CloudZDRAIClient(BaseAIClient):
    """1536d Cloud Zero Data Retention inference client."""

    mode = AI_MODE_CLOUD_ZDR
    allows_outbound_wan = True
    embedding_dimensions = CLOUD_ZDR_EMBEDDING_DIMENSIONS
    embedding_column = "embedding_1536"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        llm_model: str | None = None,
        embedding_model: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        raw_url = (
            base_url if base_url is not None else settings.CLOUD_ZDR_LLM_BASE_URL or ""
        ).strip()
        raw_key = (api_key if api_key is not None else settings.CLOUD_ZDR_API_KEY or "").strip()
        if not raw_url:
            raise AIClientConfigurationError(
                "CLOUD_ZDR_LLM_BASE_URL is not configured for ai_processing_mode='cloud_zdr'."
            )
        if not raw_key:
            raise AIClientConfigurationError(
                "CLOUD_ZDR_API_KEY is not configured for ai_processing_mode='cloud_zdr'."
            )
        parsed = urlparse(raw_url)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
            raise AIClientConfigurationError(
                f"Invalid CLOUD_ZDR_LLM_BASE_URL '{raw_url}': expected http(s) URL with hostname."
            )

        super().__init__(
            base_url=raw_url,
            llm_model=llm_model or settings.CLOUD_ZDR_LLM_MODEL,
            embedding_model=embedding_model or settings.CLOUD_ZDR_EMBEDDING_MODEL,
            headers={
                "Authorization": f"Bearer {raw_key}",
                "X-MANTA-ZDR": "enforced",
                "X-MANTA-AI-Mode": AI_MODE_CLOUD_ZDR,
            },
            trust_env=True,
            timeout=timeout,
            transport=transport,
        )


def enforce_ai_client_policy(
    tenant_mode: str | None,
    *,
    requested_mode: str | None = None,
    ai_client: BaseAIClient | None = None,
) -> str:
    """
    Fail-closed US-62 policy guard ensuring local_only tenants never call Cloud/WAN endpoints
    and unapproved Cloud calls without active cloud_zdr policy are refused before any request.
    """
    effective_mode = normalize_ai_processing_mode(tenant_mode)

    if requested_mode is not None:
        norm_requested = normalize_ai_processing_mode(requested_mode)
        if norm_requested != effective_mode:
            if norm_requested == AI_MODE_CLOUD_ZDR:
                raise CloudPolicyViolationError(
                    f"Cloud LLM invocation refused: tenant ai_processing_mode is '{effective_mode}', not 'cloud_zdr'."
                )
            raise LocalOnlyPolicyViolationError(
                f"AI mode mismatch: requested '{norm_requested}' while tenant policy requires '{effective_mode}'."
            )

    if ai_client is not None:
        client_mode = normalize_ai_processing_mode(getattr(ai_client, "mode", None))
        if effective_mode == AI_MODE_LOCAL_ONLY:
            if client_mode != AI_MODE_LOCAL_ONLY or bool(
                getattr(ai_client, "allows_outbound_wan", True)
            ):
                raise CloudPolicyViolationError(
                    "Unapproved Cloud AI client refused: tenant policy is 'local_only' (0 outbound WAN calls allowed)."
                )
            assert_local_only_endpoint(getattr(ai_client, "base_url", None))
        elif client_mode != AI_MODE_CLOUD_ZDR:
            raise AIClientConfigurationError(
                f"AI client mode '{client_mode}' does not match tenant policy '{effective_mode}'."
            )

    return effective_mode


def create_ai_client(
    mode: str,
    *,
    local_base_url: str | None = None,
    cloud_base_url: str | None = None,
    cloud_api_key: str | None = None,
    llm_model: str | None = None,
    embedding_model: str | None = None,
    timeout: float | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> BaseAIClient:
    """Instantiate the policy-enforced AI client matching the tenant's ai_processing_mode."""
    normalized = normalize_ai_processing_mode(mode)
    if normalized == AI_MODE_LOCAL_ONLY:
        return LocalAIClient(
            base_url=local_base_url,
            llm_model=llm_model,
            embedding_model=embedding_model,
            timeout=timeout,
            transport=transport,
        )
    return CloudZDRAIClient(
        base_url=cloud_base_url,
        api_key=cloud_api_key,
        llm_model=llm_model,
        embedding_model=embedding_model,
        timeout=timeout,
        transport=transport,
    )
