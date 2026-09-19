"""Opaque sessions and tenant-aware role-based authorization.

The API layer can later swap this reference implementation for an external
identity provider. Password handling is deliberately out of scope: TallyGuard
accepts a pre-verified principal, issues a high-entropy opaque session token,
and stores only its SHA-256 digest.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import hashlib
import secrets
from threading import Lock
from typing import Protocol


class AuthenticationDenied(RuntimeError):
    """Raised when a session is missing, expired, inactive, or revoked."""


class AuthorizationDenied(RuntimeError):
    """Raised when a principal cannot perform an operation."""


class Role(StrEnum):
    ADMIN = "ADMIN"
    FINANCE_OPERATOR = "FINANCE_OPERATOR"
    APPROVER = "APPROVER"
    AUDITOR = "AUDITOR"


class Permission(StrEnum):
    VENDOR_WRITE = "VENDOR_WRITE"
    EVIDENCE_WRITE = "EVIDENCE_WRITE"
    INVOICE_READ = "INVOICE_READ"
    DECISION_RUN = "DECISION_RUN"
    PAYMENT_APPROVE = "PAYMENT_APPROVE"
    SETTLEMENT_EXECUTE = "SETTLEMENT_EXECUTE"
    AUDIT_READ = "AUDIT_READ"
    POLICY_WRITE = "POLICY_WRITE"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.FINANCE_OPERATOR: frozenset(
        {
            Permission.VENDOR_WRITE,
            Permission.EVIDENCE_WRITE,
            Permission.INVOICE_READ,
            Permission.DECISION_RUN,
            Permission.AUDIT_READ,
        }
    ),
    Role.APPROVER: frozenset(
        {
            Permission.INVOICE_READ,
            Permission.PAYMENT_APPROVE,
            Permission.SETTLEMENT_EXECUTE,
            Permission.AUDIT_READ,
        }
    ),
    Role.AUDITOR: frozenset({Permission.INVOICE_READ, Permission.AUDIT_READ}),
}


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    organization_id: str
    roles: tuple[Role, ...]
    active: bool = True

    def __post_init__(self) -> None:
        if not self.user_id.strip() or not self.organization_id.strip():
            raise ValueError("User ID and organization ID are required.")
        if not self.roles:
            raise ValueError("At least one role is required.")
        if len(self.roles) != len(set(self.roles)):
            raise ValueError("Principal roles must be unique.")

    @property
    def permissions(self) -> frozenset[Permission]:
        merged: set[Permission] = set()
        for role in self.roles:
            merged.update(ROLE_PERMISSIONS[role])
        return frozenset(merged)


@dataclass(frozen=True, slots=True)
class Session:
    token_hash: str
    principal: Principal
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.revoked_at is None and self.principal.active


class SessionStore(Protocol):
    def save_session(self, session: Session) -> None: ...

    def get_session(self, token_hash: str) -> Session | None: ...

    def revoke_session(self, token_hash: str, revoked_at: datetime) -> Session | None: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._guard = Lock()
        self._sessions: dict[str, Session] = {}

    def save_session(self, session: Session) -> None:
        with self._guard:
            self._sessions[session.token_hash] = session

    def get_session(self, token_hash: str) -> Session | None:
        with self._guard:
            return self._sessions.get(token_hash)

    def revoke_session(self, token_hash: str, revoked_at: datetime) -> Session | None:
        with self._guard:
            session = self._sessions.get(token_hash)
            if session is None:
                return None
            if session.revoked_at is None:
                session = replace(session, revoked_at=revoked_at)
                self._sessions[token_hash] = session
            return session


class Authenticator:
    """Thread-safe opaque session issuer storing no bearer-token plaintext."""

    def __init__(self, *, store: SessionStore | None = None) -> None:
        self.store = store or InMemorySessionStore()

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def issue_session(
        self,
        principal: Principal,
        *,
        lifetime: timedelta = timedelta(hours=8),
        now: datetime | None = None,
    ) -> tuple[str, Session]:
        if not principal.active:
            raise AuthenticationDenied("Cannot issue a session for an inactive principal.")
        if lifetime <= timedelta(0):
            raise ValueError("Session lifetime must be positive.")
        issued_at = now or datetime.now(timezone.utc)
        if issued_at.tzinfo is None:
            raise ValueError("Session timestamps must be timezone-aware.")
        raw_token = secrets.token_urlsafe(32)
        token_hash = self._token_hash(raw_token)
        session = Session(
            token_hash=token_hash,
            principal=principal,
            issued_at=issued_at,
            expires_at=issued_at + lifetime,
        )
        self.store.save_session(session)
        return raw_token, session

    def authenticate(self, token: str, *, now: datetime | None = None) -> Principal:
        if not token:
            raise AuthenticationDenied("Bearer token is required.")
        checked_at = now or datetime.now(timezone.utc)
        if checked_at.tzinfo is None:
            raise ValueError("Authentication timestamp must be timezone-aware.")
        token_hash = self._token_hash(token)
        session = self.store.get_session(token_hash)
        if session is None:
            raise AuthenticationDenied("Session is invalid.")
        if not session.active:
            raise AuthenticationDenied("Session is inactive or revoked.")
        if checked_at >= session.expires_at:
            raise AuthenticationDenied("Session has expired.")
        return session.principal

    def revoke(self, token: str, *, now: datetime | None = None) -> None:
        revoked_at = now or datetime.now(timezone.utc)
        token_hash = self._token_hash(token)
        session = self.store.revoke_session(token_hash, revoked_at)
        if session is None:
            raise AuthenticationDenied("Session is invalid.")


def authorize(
    principal: Principal,
    *,
    permission: Permission,
    resource_organization_id: str,
) -> None:
    """Require both tenant equality and an explicit role permission."""

    if not principal.active:
        raise AuthorizationDenied("Principal is inactive.")
    if principal.organization_id != resource_organization_id:
        raise AuthorizationDenied("Cross-organization access is forbidden.")
    if permission not in principal.permissions:
        raise AuthorizationDenied(f"Permission is required: {permission.value}")
