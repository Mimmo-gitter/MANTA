"""Presidio NER + Shannon-entropy token + tenant deny-list ADR scrubber & fail-closed quarantine (TASK-315-1 / US-41)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
import importlib.util
import ipaddress
import logging
import math
import re
from typing import Any, Iterable, Optional
import uuid

from presidio_analyzer import AnalyzerEngine, EntityRecognizer, RecognizerRegistry, RecognizerResult
from presidio_analyzer.nlp_engine import NlpArtifacts, SpacyNlpEngine
from presidio_analyzer.predefined_recognizers import EmailRecognizer, IpRecognizer
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import (
    OperatorConfig,
    RecognizerResult as AnonRecognizerResult,
)
import spacy
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.uuidv7 import generate_uuidv7
from app.db.models.models import ADRSanitizationLog, ADRSource, AnonymizedADR, Tenant
from app.tenants.schemas import TenantADRDenyListConfig, normalize_tenant_deny_list

logger = logging.getLogger("app.adr.scrubber")

REDACTION_EMAIL = "EMAIL"
REDACTION_IP_ADDRESS = "IP_ADDRESS"
REDACTION_API_TOKEN = "API_TOKEN"
REDACTION_COMPANY_NAME = "COMPANY_NAME"

REDACTION_CLASSES: tuple[str, str, str, str] = (
    REDACTION_EMAIL,
    REDACTION_IP_ADDRESS,
    REDACTION_API_TOKEN,
    REDACTION_COMPANY_NAME,
)

PLACEHOLDER_BY_CLASS: dict[str, str] = {
    REDACTION_EMAIL: "[REDACTED_EMAIL]",
    REDACTION_IP_ADDRESS: "[REDACTED_IP_ADDRESS]",
    REDACTION_API_TOKEN: "[REDACTED_API_TOKEN]",
    REDACTION_COMPANY_NAME: "[REDACTED_COMPANY_NAME]",
}

_CLASS_PRIORITY: dict[str, int] = {
    REDACTION_API_TOKEN: 4,
    REDACTION_EMAIL: 3,
    REDACTION_IP_ADDRESS: 2,
    REDACTION_COMPANY_NAME: 1,
}

HEX_ENTROPY_THRESHOLD = 3.30
ALNUM_ENTROPY_THRESHOLD_STANDALONE = 4.20
ALNUM_ENTROPY_THRESHOLD_CONTEXT = 3.90

_EXISTING_PLACEHOLDER_RE = re.compile(r"\[REDACTED_(?:EMAIL|IP_ADDRESS|API_TOKEN|COMPANY_NAME)\]")
_EMAIL_FALLBACK_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_IPV4_RE = re.compile(
    r"(?<![A-Za-z0-9.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![A-Za-z0-9.])"
)
_IPV6_RE = re.compile(
    r"(?<![A-Za-z0-9:])(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{0,4}(?:::[0-9a-fA-F]{0,4})?(?![A-Za-z0-9:])"
)

_TIER1_FULL_MATCH_TOKEN_PATTERNS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    (
        "pem_private_key",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z0-9 ]*PRIVATE KEY-----"
        ),
        1.0,
    ),
    (
        "openai_anthropic_key",
        re.compile(r"\bsk-(?:proj-|ant-api\d+-|live-|test-)?[A-Za-z0-9_-]{20,}\b"),
        1.0,
    ),
    (
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
        1.0,
    ),
    ("gitlab_token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"), 1.0),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), 1.0),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b"), 1.0),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), 1.0),
    ("stripe_key", re.compile(r"\b(?:rk|pk)_(?:live|test)_[0-9A-Za-z]{16,}\b"), 1.0),
    (
        "sendgrid_key",
        re.compile(r"\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b"),
        1.0,
    ),
    (
        "jwt_token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
        0.98,
    ),
)

_TIER1_GROUP1_TOKEN_PATTERNS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    (
        "bearer_token",
        re.compile(r"\bBearer\s+([A-Za-z0-9._~+/=-]{16,})", re.IGNORECASE),
        0.96,
    ),
    (
        "uri_userinfo_password",
        re.compile(r"\b[a-z][a-z0-9+.-]*://[^:/\s@]+:([^\s/]+)@", re.IGNORECASE),
        0.98,
    ),
    (
        "secret_kv_assignment",
        re.compile(
            r"\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|refresh[_-]?token|"
            r"client[_-]?secret|master[_-]?key|auth[_-]?token|private[_-]?key|"
            r"token|secret|password|passwd|pwd)\b[ \t]*[:=][ \t]*[\"']?([^\s\"'`]{8,}?(?<![.,;:)]))[\"']?",
            re.IGNORECASE,
        ),
        0.95,
    ),
)

_HEX_CANDIDATE_RE = re.compile(r"\b[0-9a-fA-F]{32,128}\b")
_ALNUM_CANDIDATE_RE = re.compile(r"(?<![A-Za-z0-9/_.-])[A-Za-z0-9+/=_-]{24,256}(?![A-Za-z0-9/_.-])")
_SECRET_CONTEXT_RE = re.compile(
    r"\b(?:token|secret|key|apikey|api_key|bearer|password|passwd|credential|signature|salt)\b",
    re.IGNORECASE,
)
_COMMIT_CONTEXT_RE = re.compile(r"\b(?:commit|sha|checksum|content_hash|digest)\b", re.IGNORECASE)
_NON_SECRET_LITERALS = frozenset(
    {"true", "false", "null", "none", "undefined", "changeme", "placeholder", "example"}
)

_COMPANY_SUFFIX_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9&'.-]*(?:[ \t]+[A-Z][A-Za-z0-9&'.-]*){0,3}[ \t]+"
    r"(?:Inc\.?|Corp\.?|Corporation|LLC|L\.L\.C\.|Ltd\.?|Limited|GmbH|AG|PLC|S\.A\.|B\.V\.|"
    r"Holdings|Financial|Enterprises))\b"
)
_COMPANY_CAMEL_SUFFIX_RE = re.compile(r"\b([A-Z][a-z]+(?:Bank|Corp|FinTech|Ventures))\b")

_TECH_ALLOWLIST = frozenset(
    {
        "postgresql",
        "postgres",
        "pgvector",
        "pgbouncer",
        "redis",
        "valkey",
        "docker",
        "kubernetes",
        "github",
        "gitlab",
        "bitbucket",
        "jira",
        "slack",
        "keycloak",
        "fastapi",
        "pydantic",
        "sqlalchemy",
        "alembic",
        "uvicorn",
        "nextjs",
        "next.js",
        "react",
        "typescript",
        "python",
        "opentelemetry",
        "prometheus",
        "jaeger",
        "kafka",
        "rabbitmq",
        "graphql",
        "openai",
        "anthropic",
        "ollama",
        "vllm",
        "microsoft",
        "presidio",
        "spacy",
        "amazon",
        "google",
        "azure",
        "cloudflare",
    }
)
_GENERIC_SLUGS = frozenset({"default", "local", "test", "demo", "sandbox", "manta"})
_GENERIC_DOMAIN_LABELS = frozenset(
    {"localhost", "local", "internal", "example", "test", "manta", "gmail", "outlook", "yahoo"}
)


class ADRScrubberError(RuntimeError):
    """Base exception raised by ADR Presidio/entropy scrubbing."""


class ADRScannerFailureError(ADRScrubberError):
    """Raised when the ADR scanner encounters an internal or configuration failure."""


def compute_shannon_entropy(value: str) -> float:
    """Compute Shannon entropy H(s) in bits per character over base-2 log probabilities."""
    if not value:
        return 0.0
    length = len(value)
    counts = Counter(value)
    return -sum((cnt / length) * math.log2(cnt / length) for cnt in counts.values())


def empty_redaction_counts() -> dict[str, int]:
    """Return zeroed canonical redaction counts across all 4 required classes."""
    return {cls: 0 for cls in REDACTION_CLASSES}


def build_presidio_entities_payload(counts: dict[str, int]) -> dict[str, Any]:
    """Build ADRSanitizationLog.presidio_entities_scrubbed payload compatible with ADR Governance."""
    normalized = {cls: int(counts.get(cls, 0)) for cls in REDACTION_CLASSES}
    total = sum(normalized.values())
    return {
        **normalized,
        "counts": normalized,
        "total": total,
    }


@dataclass(frozen=True, slots=True)
class ScrubRedactionEvent:
    """Metadata for a single redacted span (stores offsets and class only — never raw secrets)."""

    redaction_class: str
    start: int
    end: int
    score: float
    detector: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.redaction_class,
            "start": self.start,
            "end": self.end,
            "score": round(self.score, 4),
            "detector": self.detector,
        }


@dataclass(frozen=True)
class ADRScrubResult:
    """Result returned by ADRScrubber.scrub_text."""

    scrubbed_text: str
    counts: dict[str, int]
    total_redactions: int
    redactions: list[ScrubRedactionEvent] = field(default_factory=list)

    def to_presidio_entities_scrubbed(self) -> dict[str, Any]:
        return build_presidio_entities_payload(self.counts)


@dataclass(frozen=True)
class ADRScrubPersistenceOutcome:
    """Outcome of running ADR scrubbing (or fail-closed quarantine) against DB records."""

    adr_id: uuid.UUID
    tenant_id: uuid.UUID
    quarantined: bool
    scrubbed_title: Optional[str] = None
    scrubbed_markdown: Optional[str] = None
    counts: dict[str, int] = field(default_factory=empty_redaction_counts)
    total_redactions: int = 0
    combined_entities_scrubbed: dict[str, Any] = field(
        default_factory=lambda: build_presidio_entities_payload(empty_redaction_counts())
    )
    redactions: list[dict[str, Any]] = field(default_factory=list)
    error_message: Optional[str] = None


class OfflineSpacyNlpEngine(SpacyNlpEngine):
    """Offline spaCy NLP engine for Presidio that never attempts WAN model downloads."""

    def __init__(self, loaded_spacy_model: Any | None = None) -> None:
        super().__init__()
        if loaded_spacy_model is None:
            loaded_spacy_model = _load_offline_spacy_model()
        self.nlp = {"en": loaded_spacy_model}

    def load(self) -> None:  # pragma: no cover - model already loaded in __init__
        if not self.nlp:
            self.nlp = {"en": _load_offline_spacy_model()}


@lru_cache(maxsize=1)
def _load_offline_spacy_model() -> Any:
    for model_name in ("en_core_web_sm", "en_core_web_lg"):
        if importlib.util.find_spec(model_name) is not None:
            try:
                return spacy.load(model_name)
            except Exception:
                continue
    return spacy.blank("en")


class CanonicalEmailRecognizer(EntityRecognizer):
    """Presidio recognizer detecting emails and emitting canonical EMAIL entities."""

    def __init__(self) -> None:
        self._presidio_email = EmailRecognizer(supported_language="en")
        super().__init__(
            supported_entities=[REDACTION_EMAIL],
            name="CanonicalEmailRecognizer",
            supported_language="en",
        )

    def load(self) -> None:
        self._presidio_email.load()

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: NlpArtifacts | None = None,
    ) -> list[RecognizerResult]:
        if REDACTION_EMAIL not in entities:
            return []
        results: list[RecognizerResult] = []
        seen_spans: set[tuple[int, int]] = set()
        for res in self._presidio_email.analyze(
            text, ["EMAIL_ADDRESS"], nlp_artifacts=nlp_artifacts
        ):
            span = (res.start, res.end)
            seen_spans.add(span)
            results.append(
                RecognizerResult(
                    entity_type=REDACTION_EMAIL,
                    start=res.start,
                    end=res.end,
                    score=max(float(res.score), 0.9),
                )
            )
        for match in _EMAIL_FALLBACK_RE.finditer(text):
            span = (match.start(), match.end())
            if span not in seen_spans:
                seen_spans.add(span)
                results.append(
                    RecognizerResult(
                        entity_type=REDACTION_EMAIL,
                        start=match.start(),
                        end=match.end(),
                        score=0.95,
                    )
                )
        return results


class CanonicalIpRecognizer(EntityRecognizer):
    """Presidio recognizer detecting IPv4/IPv6 addresses while ignoring version numbers."""

    def __init__(self) -> None:
        self._presidio_ip = IpRecognizer(supported_language="en")
        super().__init__(
            supported_entities=[REDACTION_IP_ADDRESS],
            name="CanonicalIpRecognizer",
            supported_language="en",
        )

    def load(self) -> None:
        self._presidio_ip.load()

    @staticmethod
    def _is_valid_ip_span(text: str, start: int, end: int) -> bool:
        candidate = text[start:end]
        if start > 0 and text[start - 1].lower() == "v":
            # Avoid false positives on software versions such as `v1.2.3.4`
            return False
        try:
            ipaddress.ip_address(candidate)
            return True
        except ValueError:
            return False

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: NlpArtifacts | None = None,
    ) -> list[RecognizerResult]:
        if REDACTION_IP_ADDRESS not in entities:
            return []
        results: list[RecognizerResult] = []
        seen_spans: set[tuple[int, int]] = set()

        for res in self._presidio_ip.analyze(text, ["IP_ADDRESS"], nlp_artifacts=nlp_artifacts):
            if self._is_valid_ip_span(text, res.start, res.end):
                span = (res.start, res.end)
                seen_spans.add(span)
                results.append(
                    RecognizerResult(
                        entity_type=REDACTION_IP_ADDRESS,
                        start=res.start,
                        end=res.end,
                        score=max(float(res.score), 0.85),
                    )
                )

        for pattern in (_IPV4_RE, _IPV6_RE):
            for match in pattern.finditer(text):
                span = (match.start(), match.end())
                if span in seen_spans:
                    continue
                if self._is_valid_ip_span(text, match.start(), match.end()):
                    seen_spans.add(span)
                    results.append(
                        RecognizerResult(
                            entity_type=REDACTION_IP_ADDRESS,
                            start=match.start(),
                            end=match.end(),
                            score=0.90,
                        )
                    )
        return results


class ApiTokenEntropyRecognizer(EntityRecognizer):
    """Custom Presidio recognizer combining deterministic secret regexes and Shannon entropy."""

    def __init__(self) -> None:
        super().__init__(
            supported_entities=[REDACTION_API_TOKEN],
            name="ApiTokenEntropyRecognizer",
            supported_language="en",
        )

    def load(self) -> None:
        return None

    @staticmethod
    def _looks_like_mixed_secret(candidate: str) -> bool:
        has_digit = any(ch.isdigit() for ch in candidate)
        has_upper = any(ch.isupper() for ch in candidate)
        has_lower = any(ch.islower() for ch in candidate)
        has_symbol = any(ch in "+/=_-." for ch in candidate)
        if not has_digit:
            return False
        class_count = int(has_upper) + int(has_lower) + int(has_symbol)
        return class_count >= 2

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: NlpArtifacts | None = None,
    ) -> list[RecognizerResult]:
        if REDACTION_API_TOKEN not in entities:
            return []

        results: list[RecognizerResult] = []
        seen_spans: set[tuple[int, int]] = set()

        for _label, pattern, score in _TIER1_FULL_MATCH_TOKEN_PATTERNS:
            for match in pattern.finditer(text):
                span = (match.start(), match.end())
                if span not in seen_spans:
                    seen_spans.add(span)
                    results.append(
                        RecognizerResult(
                            entity_type=REDACTION_API_TOKEN,
                            start=match.start(),
                            end=match.end(),
                            score=score,
                        )
                    )

        for _label, pattern, score in _TIER1_GROUP1_TOKEN_PATTERNS:
            for match in pattern.finditer(text):
                val = match.group(1)
                if not val or val.startswith("[REDACTED_") or val.lower() in _NON_SECRET_LITERALS:
                    continue
                span = (match.start(1), match.end(1))
                if span not in seen_spans:
                    seen_spans.add(span)
                    results.append(
                        RecognizerResult(
                            entity_type=REDACTION_API_TOKEN,
                            start=span[0],
                            end=span[1],
                            score=score,
                        )
                    )

        for match in _HEX_CANDIDATE_RE.finditer(text):
            val = match.group(0)
            span = (match.start(), match.end())
            if span in seen_spans:
                continue
            has_digit = any(ch.isdigit() for ch in val)
            has_hex_alpha = any(ch.lower() in "abcdef" for ch in val)
            if not (has_digit and has_hex_alpha):
                continue
            window_before = text[max(0, match.start() - 32) : match.start()]
            if _COMMIT_CONTEXT_RE.search(window_before) and not _SECRET_CONTEXT_RE.search(
                window_before
            ):
                continue
            if compute_shannon_entropy(val) >= HEX_ENTROPY_THRESHOLD:
                seen_spans.add(span)
                results.append(
                    RecognizerResult(
                        entity_type=REDACTION_API_TOKEN,
                        start=span[0],
                        end=span[1],
                        score=0.85,
                    )
                )

        for match in _ALNUM_CANDIDATE_RE.finditer(text):
            val = match.group(0)
            span = (match.start(), match.end())
            if span in seen_spans:
                continue
            if val.startswith("REDACTED_"):
                continue
            if not self._looks_like_mixed_secret(val):
                continue
            entropy = compute_shannon_entropy(val)
            ctx_window = text[max(0, match.start() - 48) : min(len(text), match.end() + 24)]
            threshold = (
                ALNUM_ENTROPY_THRESHOLD_CONTEXT
                if _SECRET_CONTEXT_RE.search(ctx_window)
                else ALNUM_ENTROPY_THRESHOLD_STANDALONE
            )
            if entropy >= threshold:
                seen_spans.add(span)
                results.append(
                    RecognizerResult(
                        entity_type=REDACTION_API_TOKEN,
                        start=span[0],
                        end=span[1],
                        score=0.85,
                    )
                )

        return results


class CompanyNameRecognizer(EntityRecognizer):
    """Detects corporate legal-entity suffixes while preserving open-source technology names."""

    def __init__(self) -> None:
        super().__init__(
            supported_entities=[REDACTION_COMPANY_NAME],
            name="CompanyNameRecognizer",
            supported_language="en",
        )

    def load(self) -> None:
        return None

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: NlpArtifacts | None = None,
    ) -> list[RecognizerResult]:
        if REDACTION_COMPANY_NAME not in entities:
            return []

        results: list[RecognizerResult] = []
        seen_spans: set[tuple[int, int]] = set()

        for pattern, score in ((_COMPANY_SUFFIX_RE, 0.85), (_COMPANY_CAMEL_SUFFIX_RE, 0.80)):
            for match in pattern.finditer(text):
                candidate = match.group(1).strip()
                if candidate.lower() in _TECH_ALLOWLIST:
                    continue
                span = (match.start(1), match.end(1))
                if span not in seen_spans:
                    seen_spans.add(span)
                    results.append(
                        RecognizerResult(
                            entity_type=REDACTION_COMPANY_NAME,
                            start=span[0],
                            end=span[1],
                            score=score,
                        )
                    )

        if nlp_artifacts is not None and getattr(nlp_artifacts, "entities", None):
            for ent in nlp_artifacts.entities:
                if getattr(ent, "label_", "") == "ORG":
                    candidate = ent.text.strip()
                    if len(candidate) >= 2 and candidate.lower() not in _TECH_ALLOWLIST:
                        span = (ent.start_char, ent.end_char)
                        if span not in seen_spans:
                            seen_spans.add(span)
                            results.append(
                                RecognizerResult(
                                    entity_type=REDACTION_COMPANY_NAME,
                                    start=span[0],
                                    end=span[1],
                                    score=0.75,
                                )
                            )
        return results


class TenantDenyListRecognizer(EntityRecognizer):
    """Ad-hoc per-tenant Presidio recognizer matching deny-list terms, regexes, and tenant identity."""

    def __init__(
        self,
        *,
        deny_list: TenantADRDenyListConfig | None = None,
        tenant_name: str | None = None,
        tenant_slug: str | None = None,
        allowed_domain: str | None = None,
    ) -> None:
        super().__init__(
            supported_entities=list(REDACTION_CLASSES),
            name="TenantDenyListRecognizer",
            supported_language="en",
        )
        self._compiled_rules: list[tuple[str, re.Pattern[str], float]] = []
        cfg = deny_list or TenantADRDenyListConfig()

        for term in cfg.terms:
            self._compiled_rules.append(
                (REDACTION_COMPANY_NAME, self._compile_literal(term, case_sensitive=False), 0.98)
            )

        for entry in cfg.entries:
            if entry.match_mode == "regex":
                flags = 0 if entry.case_sensitive else re.IGNORECASE
                compiled = re.compile(entry.pattern, flags)
            else:
                compiled = self._compile_literal(entry.pattern, case_sensitive=entry.case_sensitive)
            self._compiled_rules.append((entry.redaction_class, compiled, 0.99))

        if cfg.auto_include_tenant_identity:
            for identity_term in _derive_tenant_identity_terms(
                tenant_name=tenant_name,
                tenant_slug=tenant_slug,
                allowed_domain=allowed_domain,
            ):
                self._compiled_rules.append(
                    (
                        REDACTION_COMPANY_NAME,
                        self._compile_literal(identity_term, case_sensitive=False),
                        0.96,
                    )
                )

    @staticmethod
    def _compile_literal(term: str, *, case_sensitive: bool) -> re.Pattern[str]:
        escaped = re.escape(term.strip())
        prefix = r"(?<!\w)" if term[0].isalnum() or term[0] == "_" else ""
        suffix = r"(?!\w)" if term[-1].isalnum() or term[-1] == "_" else ""
        flags = 0 if case_sensitive else re.IGNORECASE
        return re.compile(f"{prefix}{escaped}{suffix}", flags)

    def load(self) -> None:
        return None

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: NlpArtifacts | None = None,
    ) -> list[RecognizerResult]:
        if not self._compiled_rules:
            return []
        wanted = set(entities)
        results: list[RecognizerResult] = []
        for redaction_class, pattern, score in self._compiled_rules:
            if redaction_class not in wanted:
                continue
            for match in pattern.finditer(text):
                if match.start() == match.end():
                    continue
                results.append(
                    RecognizerResult(
                        entity_type=redaction_class,
                        start=match.start(),
                        end=match.end(),
                        score=score,
                    )
                )
        return results


def _derive_tenant_identity_terms(
    *,
    tenant_name: str | None,
    tenant_slug: str | None,
    allowed_domain: str | None,
) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()

    def _add(raw: str | None) -> None:
        if not raw:
            return
        cleaned = raw.strip()
        if len(cleaned) < 3:
            return
        key = cleaned.lower()
        if key not in seen:
            seen.add(key)
            terms.append(cleaned)

    _add(tenant_name)
    if tenant_slug and tenant_slug.strip().lower() not in _GENERIC_SLUGS:
        _add(tenant_slug)
    if allowed_domain:
        dom = allowed_domain.strip().lower().lstrip("@")
        if dom and dom not in _GENERIC_DOMAIN_LABELS:
            _add(dom)
            primary_label = dom.split(".")[0]
            if primary_label not in _GENERIC_DOMAIN_LABELS and primary_label not in _TECH_ALLOWLIST:
                _add(primary_label)
    return terms


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _resolve_overlapping_results(
    text: str,
    raw_results: Iterable[RecognizerResult],
) -> list[RecognizerResult]:
    """Select deterministic non-overlapping spans respecting priority and span containment."""
    placeholder_spans = [(m.start(), m.end()) for m in _EXISTING_PLACEHOLDER_RE.finditer(text)]

    candidates: list[RecognizerResult] = []
    for res in raw_results:
        if res.entity_type not in PLACEHOLDER_BY_CLASS:
            continue
        if res.start < 0 or res.end <= res.start or res.end > len(text):
            continue
        if any(_overlaps((res.start, res.end), ph) for ph in placeholder_spans):
            continue
        candidates.append(res)

    candidates.sort(
        key=lambda r: (
            -(r.end - r.start),
            -_CLASS_PRIORITY.get(r.entity_type, 0),
            -float(r.score),
            r.start,
        )
    )

    chosen: list[RecognizerResult] = []
    for cand in candidates:
        if any(_overlaps((cand.start, cand.end), (c.start, c.end)) for c in chosen):
            continue
        chosen.append(cand)

    chosen.sort(key=lambda r: (r.start, r.end))
    return chosen


def _build_default_analyzer() -> AnalyzerEngine:
    nlp_engine = OfflineSpacyNlpEngine()
    registry = RecognizerRegistry(supported_languages=["en"])
    registry.add_recognizer(CanonicalEmailRecognizer())
    registry.add_recognizer(CanonicalIpRecognizer())
    registry.add_recognizer(ApiTokenEntropyRecognizer())
    registry.add_recognizer(CompanyNameRecognizer())
    return AnalyzerEngine(
        nlp_engine=nlp_engine,
        registry=registry,
        supported_languages=["en"],
    )


class ADRScrubber:
    """Presidio + Shannon-entropy + tenant deny-list scrubber for raw ADR Markdown."""

    def __init__(
        self,
        *,
        analyzer: AnalyzerEngine | None = None,
        anonymizer: AnonymizerEngine | None = None,
    ) -> None:
        self._analyzer = analyzer or _build_default_analyzer()
        self._anonymizer = anonymizer or AnonymizerEngine()
        self._operators = {
            cls: OperatorConfig("replace", {"new_value": placeholder})
            for cls, placeholder in PLACEHOLDER_BY_CLASS.items()
        }

    def scrub_text(
        self,
        raw_text: str,
        *,
        tenant_deny_list: TenantADRDenyListConfig | dict[str, Any] | list[str] | None = None,
        tenant_name: str | None = None,
        tenant_slug: str | None = None,
        allowed_domain: str | None = None,
    ) -> ADRScrubResult:
        if not isinstance(raw_text, str):
            raise ADRScannerFailureError("raw_text must be a string")
        if not raw_text:
            return ADRScrubResult(
                scrubbed_text="",
                counts=empty_redaction_counts(),
                total_redactions=0,
                redactions=[],
            )

        try:
            deny_cfg = normalize_tenant_deny_list(tenant_deny_list)
            ad_hoc: list[EntityRecognizer] = [
                TenantDenyListRecognizer(
                    deny_list=deny_cfg,
                    tenant_name=tenant_name,
                    tenant_slug=tenant_slug,
                    allowed_domain=allowed_domain,
                )
            ]
            raw_results = self._analyzer.analyze(
                text=raw_text,
                language="en",
                entities=list(REDACTION_CLASSES),
                ad_hoc_recognizers=ad_hoc,
            )
            chosen = _resolve_overlapping_results(raw_text, raw_results)

            counts = empty_redaction_counts()
            redactions: list[ScrubRedactionEvent] = []
            anon_results: list[AnonRecognizerResult] = []

            for item in chosen:
                counts[item.entity_type] += 1
                redactions.append(
                    ScrubRedactionEvent(
                        redaction_class=item.entity_type,
                        start=item.start,
                        end=item.end,
                        score=float(item.score),
                        detector=(
                            item.recognition_metadata.get("recognizer_name", "presidio")
                            if isinstance(item.recognition_metadata, dict)
                            else "presidio"
                        ),
                    )
                )
                anon_results.append(
                    AnonRecognizerResult(
                        entity_type=item.entity_type,
                        start=item.start,
                        end=item.end,
                        score=float(item.score),
                    )
                )

            if anon_results:
                anonymized = self._anonymizer.anonymize(
                    text=raw_text,
                    analyzer_results=anon_results,
                    operators=self._operators,
                    merge_entities_with_spaces=False,
                )
                scrubbed_text = anonymized.text
            else:
                scrubbed_text = raw_text

            return ADRScrubResult(
                scrubbed_text=scrubbed_text,
                counts=counts,
                total_redactions=sum(counts.values()),
                redactions=redactions,
            )
        except ADRScrubberError:
            raise
        except Exception as exc:
            raise ADRScannerFailureError(f"Presidio ADR scanner failed: {exc}") from exc


async def _quarantine_adr_fail_closed(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    adr: AnonymizedADR,
    stage: str,
    error_message: str,
    error_type: str,
) -> ADRScrubPersistenceOutcome:
    now = datetime.now(timezone.utc)
    adr.status = "quarantined"
    adr.sanitization_verified = False
    adr.decision_summary = None
    adr.embedding_384 = None
    adr.embedding_1536 = None
    adr.updated_at = now

    zero_counts = empty_redaction_counts()
    payload = build_presidio_entities_payload(zero_counts)
    db.add(
        ADRSanitizationLog(
            id=generate_uuidv7(),
            tenant_id=tenant_id,
            adr_id=adr.id,
            raw_input_snippet=None,
            presidio_entities_scrubbed=payload,
            llm_abstraction_prompt_tokens=None,
            solution_architect_diff_payload={
                "status": "quarantined",
                "stage": stage,
                "error_type": error_type,
            },
        )
    )
    await db.flush()
    return ADRScrubPersistenceOutcome(
        adr_id=adr.id,
        tenant_id=tenant_id,
        quarantined=True,
        scrubbed_title=None,
        scrubbed_markdown=None,
        counts=zero_counts,
        total_redactions=0,
        combined_entities_scrubbed=payload,
        redactions=[],
        error_message=error_message,
    )


async def scrub_adr_source(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    adr_id: uuid.UUID,
    scrubber: Optional[ADRScrubber] = None,
    persist_log: bool = True,
) -> ADRScrubPersistenceOutcome:
    """
    Scrub an ADR's raw source Markdown + title using Presidio, entropy tokens, and tenant deny-list.

    Fails closed: any scanner exception or missing raw source immediately quarantines the ADR,
    records an `ADRSanitizationLog` row with `raw_input_snippet=None`, and never forwards raw text.
    """
    tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if tenant is None:
        raise ValueError(f"Tenant {tenant_id} not found")

    adr = await db.scalar(
        select(AnonymizedADR).where(
            AnonymizedADR.tenant_id == tenant_id,
            AnonymizedADR.id == adr_id,
        )
    )
    if adr is None:
        raise ValueError(f"AnonymizedADR {adr_id} not found for tenant {tenant_id}")

    source = await db.scalar(
        select(ADRSource).where(
            ADRSource.tenant_id == tenant_id,
            ADRSource.adr_id == adr_id,
        )
    )
    if source is None or not (source.raw_markdown or "").strip():
        logger.warning(
            "Quarantining ADR %s for tenant %s: missing or empty ADRSource",
            adr_id,
            tenant_id,
        )
        return await _quarantine_adr_fail_closed(
            db,
            tenant_id=tenant_id,
            adr=adr,
            stage="source_lookup",
            error_message="Missing or empty raw ADR source; quarantined fail-closed.",
            error_type="MissingADRSourceError",
        )

    active_scrubber = scrubber or ADRScrubber()
    try:
        deny_cfg = normalize_tenant_deny_list(tenant.adr_deny_list)
        title_result = active_scrubber.scrub_text(
            adr.title,
            tenant_deny_list=deny_cfg,
            tenant_name=tenant.name,
            tenant_slug=tenant.slug,
            allowed_domain=tenant.allowed_domain,
        )
        body_result = active_scrubber.scrub_text(
            source.raw_markdown,
            tenant_deny_list=deny_cfg,
            tenant_name=tenant.name,
            tenant_slug=tenant.slug,
            allowed_domain=tenant.allowed_domain,
        )
    except Exception as exc:
        logger.exception(
            "Scanner failure scrubbing ADR %s for tenant %s; quarantining fail-closed",
            adr_id,
            tenant_id,
        )
        return await _quarantine_adr_fail_closed(
            db,
            tenant_id=tenant_id,
            adr=adr,
            stage="presidio_scrub",
            error_message=str(exc),
            error_type=type(exc).__name__,
        )

    combined_counts = {
        cls: int(title_result.counts.get(cls, 0)) + int(body_result.counts.get(cls, 0))
        for cls in REDACTION_CLASSES
    }
    combined_payload = build_presidio_entities_payload(combined_counts)
    redaction_dicts = [event.to_dict() for event in body_result.redactions]

    if persist_log:
        db.add(
            ADRSanitizationLog(
                id=generate_uuidv7(),
                tenant_id=tenant_id,
                adr_id=adr.id,
                raw_input_snippet=body_result.scrubbed_text[:500],
                presidio_entities_scrubbed=combined_payload,
                llm_abstraction_prompt_tokens=None,
                solution_architect_diff_payload={
                    "status": adr.status,
                    "scrubbed_title": title_result.scrubbed_text,
                    "scrubbed_markdown": body_result.scrubbed_text,
                    "redactions": redaction_dicts,
                },
            )
        )
        await db.flush()

    return ADRScrubPersistenceOutcome(
        adr_id=adr.id,
        tenant_id=tenant_id,
        quarantined=False,
        scrubbed_title=title_result.scrubbed_text,
        scrubbed_markdown=body_result.scrubbed_text,
        counts=combined_counts,
        total_redactions=sum(combined_counts.values()),
        combined_entities_scrubbed=combined_payload,
        redactions=redaction_dicts,
        error_message=None,
    )
