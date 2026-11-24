"""
TASK-301-5 (SCRUM-500 / STORY-301): Production Keycloak Realm Overlay Renderer.

Merges the base realm structure (`infra/keycloak/realm-export.json`) with the
hardened production overlay (`infra/keycloak/realm-prod-overlay.json`), injects
validated HTTPS URLs (`https://manta.<domain>/*`), strips all dev test users,
and validates security invariants before writing the importable realm JSON.

Usage:
    python infra/keycloak/render_prod_realm.py \
        --domain example.com \
        --output /rendered/realm-export.json
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_BASE_PATH = SCRIPT_DIR / "realm-export.json"
DEFAULT_OVERLAY_PATH = SCRIPT_DIR / "realm-prod-overlay.json"

_DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_FORBIDDEN_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})
_CANONICAL_ROLES = frozenset(
    {
        "DEVELOPER",
        "SCRUM_MASTER",
        "PROJECT_MANAGER",
        "SOLUTION_ARCHITECT",
        "DELIVERY_DIRECTOR",
        "TOP_MANAGEMENT",
        "TENANT_ADMIN",
    }
)


def validate_production_domain(domain: str) -> str:
    """Validate and normalize a public production domain (e.g. `example.com`)."""
    cleaned = (domain or "").strip().lower()
    if not cleaned:
        raise ValueError("MANTA_DOMAIN must be a non-empty public domain name.")
    if "://" in cleaned or "/" in cleaned or ":" in cleaned or "*" in cleaned or " " in cleaned:
        raise ValueError(
            f"MANTA_DOMAIN must be a bare DNS domain without scheme, port, path, or wildcards: {domain!r}"
        )
    if cleaned in _FORBIDDEN_HOSTS or cleaned.endswith((".local", ".localhost", ".internal")):
        raise ValueError(
            f"MANTA_DOMAIN cannot be a local/loopback domain in production: {domain!r}"
        )

    labels = cleaned.split(".")
    if len(labels) < 2 or not all(_DOMAIN_LABEL_RE.match(label) for label in labels):
        raise ValueError(f"MANTA_DOMAIN is not a valid FQDN: {domain!r}")
    return cleaned


def resolve_portal_origin(
    domain: str | None = None, portal_origin: str | None = None
) -> tuple[str, str]:
    """Resolve `(normalized_domain, https_portal_origin)` for production realm rendering."""
    if portal_origin and portal_origin.strip():
        raw_origin = portal_origin.strip().rstrip("/")
        parsed = urlparse(raw_origin)
        if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query:
            raise ValueError(
                f"PORTAL_ORIGIN must be an explicit https://<host> origin without path/query: {portal_origin!r}"
            )
        host = validate_production_domain(parsed.hostname)
        normalized_domain = validate_production_domain(domain) if domain else host
        port_part = f":{parsed.port}" if parsed.port else ""
        return normalized_domain, f"https://{host}{port_part}"

    normalized_domain = validate_production_domain(domain or "")
    if normalized_domain.startswith("manta.") and normalized_domain.count(".") >= 2:
        host = normalized_domain
    else:
        host = f"manta.{normalized_domain}"
    return normalized_domain, f"https://{host}"


def _substitute_placeholders(obj: Any, replacements: dict[str, str]) -> Any:
    if isinstance(obj, str):
        rendered = obj
        for key, value in replacements.items():
            rendered = rendered.replace(f"${{{key}}}", value)
        return rendered
    if isinstance(obj, list):
        return [_substitute_placeholders(item, replacements) for item in obj]
    if isinstance(obj, dict):
        return {k: _substitute_placeholders(v, replacements) for k, v in obj.items()}
    return obj


def _merge_client_entry(base_client: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base_client)
    for key, value in patch.items():
        if key == "attributes" and isinstance(value, dict):
            attrs = dict(merged.get("attributes") or {})
            attrs.update(value)
            merged["attributes"] = attrs
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def merge_prod_realm(
    base_realm: dict[str, Any],
    overlay: dict[str, Any],
    *,
    domain: str | None = None,
    portal_origin: str | None = None,
) -> dict[str, Any]:
    """Merge base Keycloak realm JSON with production overlay and validate invariants."""
    norm_domain, norm_origin = resolve_portal_origin(domain=domain, portal_origin=portal_origin)
    merged = copy.deepcopy(base_realm)

    for key, value in overlay.items():
        if key == "clients" and isinstance(value, list):
            base_clients = list(merged.get("clients") or [])
            by_client_id = {
                c.get("clientId"): idx
                for idx, c in enumerate(base_clients)
                if isinstance(c, dict) and c.get("clientId")
            }
            for patch in value:
                cid = patch.get("clientId")
                if cid in by_client_id:
                    idx = by_client_id[cid]
                    base_clients[idx] = _merge_client_entry(base_clients[idx], patch)
                else:
                    base_clients.append(copy.deepcopy(patch))
            merged["clients"] = base_clients
        else:
            merged[key] = copy.deepcopy(value)

    rendered = _substitute_placeholders(
        merged,
        {
            "MANTA_DOMAIN": norm_domain,
            "PORTAL_ORIGIN": norm_origin,
        },
    )
    validate_rendered_prod_realm(rendered)
    return rendered


def validate_rendered_prod_realm(realm: dict[str, Any]) -> None:
    """Assert mandatory production security invariants on the rendered Keycloak realm."""
    errors: list[str] = []

    if realm.get("realm") != "manta" or not realm.get("enabled"):
        errors.append("Realm must be named 'manta' and enabled.")
    if realm.get("sslRequired") != "external":
        errors.append(
            f"sslRequired must be 'external' in production, got {realm.get('sslRequired')!r}"
        )
    if realm.get("registrationAllowed") is not False:
        errors.append("registrationAllowed must be false in production realm.")
    if realm.get("users"):
        errors.append(
            f"Production realm must contain 0 hardcoded dev users, found {len(realm['users'])}."
        )

    access_ttl = int(realm.get("accessTokenLifespan") or 0)
    sso_idle = int(realm.get("ssoSessionIdleTimeout") or 0)
    sso_max = int(realm.get("ssoSessionMaxLifespan") or 0)
    if access_ttl < 300:
        errors.append(
            f"accessTokenLifespan ({access_ttl}s) is too short for production (expected >= 300s)."
        )
    if sso_idle < 900:
        errors.append(
            f"ssoSessionIdleTimeout ({sso_idle}s) is too short for production (expected >= 900s)."
        )
    if not (access_ttl < sso_idle <= sso_max):
        errors.append(
            f"Lifespan ordering violated: access={access_ttl}, idle={sso_idle}, max={sso_max}."
        )

    realm_roles = {
        r.get("name") for r in realm.get("roles", {}).get("realm", []) if isinstance(r, dict)
    }
    missing_roles = _CANONICAL_ROLES - realm_roles
    if missing_roles:
        errors.append(f"Missing canonical SRS roles: {sorted(missing_roles)}")
    if "DELIVERY_EXEC" in realm_roles:
        errors.append("Deprecated role DELIVERY_EXEC must not exist in production realm.")

    clients = {c.get("clientId"): c for c in realm.get("clients", []) if isinstance(c, dict)}
    portal = clients.get("manta-portal")
    if not portal:
        errors.append("Missing manta-portal client.")
    else:
        root_url = str(portal.get("rootUrl") or "")
        if not root_url.startswith("https://") or "localhost" in root_url or "${" in root_url:
            errors.append(f"manta-portal rootUrl must be a resolved https:// URL, got {root_url!r}")

        redirect_uris = portal.get("redirectUris") or []
        if not redirect_uris or any(
            not isinstance(u, str)
            or not u.startswith("https://")
            or "localhost" in u
            or "127.0.0.1" in u
            or "${" in u
            for u in redirect_uris
        ):
            errors.append(
                f"manta-portal redirectUris must be HTTPS non-localhost URIs: {redirect_uris!r}"
            )

        web_origins = portal.get("webOrigins") or []
        if not web_origins or any(
            not isinstance(o, str)
            or o == "*"
            or not o.startswith("https://")
            or "localhost" in o
            or "${" in o
            for o in web_origins
        ):
            errors.append(
                f"manta-portal webOrigins must be explicit HTTPS origins (no '*'): {web_origins!r}"
            )

        attrs = portal.get("attributes") or {}
        if attrs.get("pkce.code.challenge.method") != "S256":
            errors.append("manta-portal must enforce PKCE S256.")
        post_logout = str(attrs.get("post.logout.redirect.uris") or "")
        if (
            not post_logout
            or not post_logout.startswith("https://")
            or "localhost" in post_logout
            or "${" in post_logout
        ):
            errors.append(
                f"manta-portal post.logout.redirect.uris must be HTTPS non-localhost: {post_logout!r}"
            )

    if errors:
        raise ValueError("Production realm validation failed:\n- " + "\n- ".join(errors))


def render_prod_realm_file(
    *,
    output_path: Path,
    domain: str | None = None,
    portal_origin: str | None = None,
    base_path: Path = DEFAULT_BASE_PATH,
    overlay_path: Path = DEFAULT_OVERLAY_PATH,
) -> dict[str, Any]:
    """Load base + overlay realm JSONs, render for production domain, and write output file."""
    with base_path.open("r", encoding="utf-8") as fh:
        base_realm = json.load(fh)
    with overlay_path.open("r", encoding="utf-8") as fh:
        overlay = json.load(fh)

    rendered = merge_prod_realm(
        base_realm,
        overlay,
        domain=domain,
        portal_origin=portal_origin,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(rendered, indent=2) + "\n", encoding="utf-8")
    return rendered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render hardened Keycloak production realm JSON from base + prod overlay."
    )
    parser.add_argument(
        "--domain",
        default=os.getenv("MANTA_DOMAIN", ""),
        help="Public production base domain (e.g. example.com -> https://manta.example.com/*).",
    )
    parser.add_argument(
        "--portal-origin",
        default=os.getenv("PORTAL_ORIGIN", ""),
        help="Optional explicit portal origin override (e.g. https://manta.example.com).",
    )
    parser.add_argument(
        "--base",
        type=Path,
        default=DEFAULT_BASE_PATH,
        help="Path to base realm-export.json.",
    )
    parser.add_argument(
        "--overlay",
        type=Path,
        default=DEFAULT_OVERLAY_PATH,
        help="Path to realm-prod-overlay.json.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Destination path for rendered production realm JSON.",
    )
    args = parser.parse_args(argv)

    rendered = render_prod_realm_file(
        output_path=args.output,
        domain=args.domain or None,
        portal_origin=args.portal_origin or None,
        base_path=args.base,
        overlay_path=args.overlay,
    )
    portal = next(c for c in rendered["clients"] if c["clientId"] == "manta-portal")
    print(
        f"[render_prod_realm] Rendered production realm '{rendered['realm']}' "
        f"(sslRequired={rendered['sslRequired']}, accessTokenLifespan={rendered['accessTokenLifespan']}s, "
        f"portal={portal['rootUrl']}, users={len(rendered['users'])}) -> {args.output}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
