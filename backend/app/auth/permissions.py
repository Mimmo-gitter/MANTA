import functools
import inspect
import json
import logging
from collections.abc import Iterable
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import or_, select

from app.auth.dependencies import get_current_user
from app.auth.schemas import TokenClaims
from app.db.models.models import User, UserRole
from app.db.session import get_tenant_db_context

logger = logging.getLogger("app.auth.permissions")

FORBIDDEN_DETAIL = "Forbidden: insufficient permissions"
FORBIDDEN_RESPONSE = {"detail": FORBIDDEN_DETAIL}


class Role(StrEnum):
    """The seven canonical M.A.N.T.A. roles (SRS §2.3)."""

    DEVELOPER = "DEVELOPER"
    SCRUM_MASTER = "SCRUM_MASTER"
    PROJECT_MANAGER = "PROJECT_MANAGER"
    SOLUTION_ARCHITECT = "SOLUTION_ARCHITECT"
    DELIVERY_DIRECTOR = "DELIVERY_DIRECTOR"
    TOP_MANAGEMENT = "TOP_MANAGEMENT"
    TENANT_ADMIN = "TENANT_ADMIN"


class Permission(StrEnum):
    """The thirteen platform actions defined in the role-permission matrix."""

    VIEW_PROJECT_DORA = "VIEW_PROJECT_DORA"
    VIEW_PORTFOLIO_OVERVIEW = "VIEW_PORTFOLIO_OVERVIEW"
    VIEW_SPRINT_BURNDOWN_WIP = "VIEW_SPRINT_BURNDOWN_WIP"
    RECEIVE_SCOPE_CREEP_ALERT = "RECEIVE_SCOPE_CREEP_ALERT"
    REASSIGN_PR_REVIEWER_MOBILE = "REASSIGN_PR_REVIEWER_MOBILE"
    VIEW_OWN_CODE_QUALITY = "VIEW_OWN_CODE_QUALITY"
    VIEW_TECH_DEBT_AND_SCIP_GRAPH = "VIEW_TECH_DEBT_AND_SCIP_GRAPH"
    TRIGGER_REFACTOR_ANALYSIS = "TRIGGER_REFACTOR_ANALYSIS"
    SEARCH_ADR = "SEARCH_ADR"
    APPROVE_ADR_TENANT_PRIVATE = "APPROVE_ADR_TENANT_PRIVATE"
    PUBLISH_GLOBAL_ADR = "PUBLISH_GLOBAL_ADR"
    MANAGE_WEBHOOK_INTEGRATIONS = "MANAGE_WEBHOOK_INTEGRATIONS"
    MANAGE_TENANT_USERS_AND_ROLES = "MANAGE_TENANT_USERS_AND_ROLES"


class PermissionMatrix:
    """Authoritative role-permission matrix loader and decision engine."""

    def __init__(self, matrix_path: Path | str | None = None) -> None:
        if matrix_path is None:
            resolved = Path(__file__).resolve()
            candidates = [
                resolved.parent.parent.parent.parent / "docs" / "roles-permissions.json",
                resolved.parent.parent.parent / "docs" / "roles-permissions.json",
                resolved.parent / "roles-permissions.json",
            ]
            matrix_path = next((p for p in candidates if p.is_file()), candidates[0])
        self.matrix_path = Path(matrix_path)
        if not self.matrix_path.is_file():
            raise FileNotFoundError(f"Role permissions matrix file not found: {self.matrix_path}")

        with open(self.matrix_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.version = data.get("version", "1.0.0")
        self.roles_data = {r["id"]: r for r in data.get("roles", [])}
        self.actions_data = {a["id"]: a for a in data.get("actions", [])}
        self.matrix = data.get("matrix", {})

        # Build alias map: e.g. DELIVERY_EXEC -> DELIVERY_DIRECTOR
        self.alias_map: dict[str, str] = {}
        for role_id, role_info in self.roles_data.items():
            for alias in role_info.get("aliases", []):
                self.alias_map[alias.upper().strip()] = role_id.upper().strip()

    def normalize_roles(self, raw_roles: Iterable[str]) -> set[str]:
        """Normalize role identifiers, map deprecated aliases, and keep canonical roles only."""
        normalized = set()
        for r in raw_roles:
            if not isinstance(r, str):
                continue
            clean = r.strip().upper()
            mapped = self.alias_map.get(clean, clean)
            if mapped in self.roles_data:
                normalized.add(mapped)
        return normalized

    def is_allowed(
        self,
        roles: Iterable[str],
        permissions: Iterable[str | Permission],
    ) -> bool:
        """
        Evaluate whether the caller holding `roles` is authorized for at least
        one of the specified `permissions`. Deny by default.
        """
        norm_roles = self.normalize_roles(roles)
        if not norm_roles:
            return False

        norm_perms = {
            p.value if isinstance(p, Permission) else str(p).strip().upper() for p in permissions
        }

        for role in norm_roles:
            role_decisions = self.matrix.get(role, {})
            for perm in norm_perms:
                cell = role_decisions.get(perm)
                if cell and cell.get("decision") == "ALLOW":
                    return True

        return False


@lru_cache(maxsize=1)
def get_permission_matrix() -> PermissionMatrix:
    """Return the cached singleton PermissionMatrix instance."""
    return PermissionMatrix()


async def resolve_caller_roles(user: TokenClaims) -> set[str]:
    """
    Роль читається з БД на кожному запиті, тому зміна ролі діє одразу.
    Токен використовується лише якщо користувача в БД немає (або БД недоступна).
    Деактивований користувач у БД = порожній набір ролей (403).
    """
    matrix = get_permission_matrix()

    conditions = [User.google_sub == user.sub]
    if user.email:
        conditions.append(User.email == user.email)

    try:
        async with get_tenant_db_context(user.tenant_uuid) as session:
            stmt = (
                select(User.role, User.is_active, UserRole.role_id)
                .outerjoin(UserRole, UserRole.user_id == User.id)
                .where(User.tenant_id == user.tenant_uuid, or_(*conditions))
            )
            result = await session.execute(stmt)
            rows = result.all()
    except Exception as exc:
        logger.warning("Role lookup in DB failed for sub '%s': %s", user.sub, exc)
        return matrix.normalize_roles(user.roles)

    if not rows:
        return matrix.normalize_roles(user.roles)

    db_roles: set[str] = set()
    for role_value, is_active, user_role_value in rows:
        if not is_active:
            continue
        if role_value:
            db_roles.add(role_value)
        if user_role_value:
            db_roles.add(user_role_value)

    return matrix.normalize_roles(db_roles)


class PermissionChecker:
    """
    Dual-purpose permission enforcement callable:
    - FastAPI dependency: `Depends(require_permission(...))`
    - Route decorator: `@require_permission(...)`
    """

    def __init__(self, *permissions: Permission | str) -> None:
        if not permissions:
            raise ValueError("require_permission requires at least one declared permission")

        matrix = get_permission_matrix()
        valid_actions = set(matrix.actions_data.keys())

        cleaned_perms: set[str] = set()
        for p in permissions:
            p_str = p.value if isinstance(p, Permission) else str(p).strip().upper()
            if p_str not in valid_actions:
                raise ValueError(
                    f"Unknown permission '{p_str}'. Must be one of: {sorted(valid_actions)}"
                )
            cleaned_perms.add(p_str)

        self._required_permissions = frozenset(cleaned_perms)
        self.required_permissions = self._required_permissions

        async def dependency(user: TokenClaims = Depends(get_current_user)) -> TokenClaims:
            roles = await resolve_caller_roles(user)
            if not get_permission_matrix().is_allowed(roles, self._required_permissions):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=FORBIDDEN_DETAIL,
                )
            return user

        dependency._required_permissions = self._required_permissions
        self._dependency = dependency
        self.__call__ = dependency  # Enables FastAPI coroutine callable detection
        self.__signature__ = inspect.signature(dependency)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        # Check if used as decorator: @require_permission(...)
        if (
            len(args) == 1
            and (inspect.iscoroutinefunction(args[0]) or inspect.isfunction(args[0]))
            and not kwargs
        ):
            fn = args[0]
            perms = self._required_permissions

            @functools.wraps(fn)
            async def wrapper(*f_args: Any, **f_kwargs: Any) -> Any:
                req: Request | None = f_kwargs.get("request") or next(
                    (a for a in f_args if isinstance(a, Request)), None
                )
                user: TokenClaims | None = f_kwargs.get("user") or next(
                    (a for a in f_args if isinstance(a, TokenClaims)), None
                )

                if user is None and req is not None:
                    user = getattr(req.state, "user", None)
                    if user is None:
                        claims = getattr(req.state, "claims", None)
                        if claims:
                            user = TokenClaims.from_claims(claims)

                if user is None:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=FORBIDDEN_DETAIL,
                    )

                roles = await resolve_caller_roles(user)
                if not get_permission_matrix().is_allowed(roles, perms):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=FORBIDDEN_DETAIL,
                    )

                if inspect.iscoroutinefunction(fn):
                    return await fn(*f_args, **f_kwargs)
                return fn(*f_args, **f_kwargs)

            wrapper._required_permissions = perms
            return wrapper

        # Otherwise delegate to FastAPI dependency
        return self._dependency(*args, **kwargs)


def require_permission(*permissions: Permission | str) -> PermissionChecker:
    """Factory creating a PermissionChecker dependency/decorator."""
    return PermissionChecker(*permissions)


def find_matched_route(app: Any, scope: dict[str, Any]) -> tuple[Any, Any, Any]:
    """
    Search app.routes to find the matching APIRoute and endpoint function for incoming scope.
    Handles Starlette Routes, FastAPI APIRoutes, and FastAPI _IncludedRouter wrappers.
    """
    from starlette.routing import Match

    routes = getattr(app, "routes", [])
    partial_match = None

    for r in routes:
        if hasattr(r, "_match"):
            match, child_scope, route, _ = r._match(scope)
            if match == Match.FULL:
                endpoint = child_scope.get("endpoint") or getattr(route, "endpoint", None)
                return match, route, endpoint
            if match == Match.PARTIAL and partial_match is None:
                endpoint = child_scope.get("endpoint") or getattr(route, "endpoint", None)
                partial_match = (match, route, endpoint)
        else:
            match, child_scope = r.matches(scope)
            if match == Match.FULL:
                route = child_scope.get("route", r)
                endpoint = child_scope.get("endpoint") or getattr(route, "endpoint", None)
                return match, route, endpoint
            if match == Match.PARTIAL and partial_match is None:
                route = child_scope.get("route", r)
                endpoint = child_scope.get("endpoint") or getattr(route, "endpoint", None)
                partial_match = (match, route, endpoint)

    if partial_match is not None:
        return partial_match

    return Match.NONE, None, None


def get_route_declared_permissions(route: Any, endpoint: Any = None) -> frozenset[str]:
    """
    Extract declared permissions from an APIRoute or endpoint function,
    inspecting function attributes and FastAPI dependant dependencies recursively.
    """
    declared: set[str] = set()

    # Check direct function/route attributes
    for target in (endpoint, getattr(route, "endpoint", None), route):
        if target is not None:
            p = getattr(target, "_required_permissions", None)
            if p:
                declared.update(p)

    # Check route dependant dependencies recursively
    dependant = getattr(route, "dependant", None)
    if dependant is not None:
        declared.update(_extract_permissions_from_dependant(dependant))

    return frozenset(declared)


def _extract_permissions_from_dependant(dependant: Any) -> set[str]:
    perms: set[str] = set()
    for dep in getattr(dependant, "dependencies", []):
        call = getattr(dep, "call", None)
        if call is not None:
            p = getattr(call, "_required_permissions", None)
            if p:
                perms.update(p)
        perms.update(_extract_permissions_from_dependant(dep))
    return perms
