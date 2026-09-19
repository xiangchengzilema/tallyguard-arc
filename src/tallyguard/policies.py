"""Immutable versioned policy storage and human-readable diffs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from threading import Lock
from typing import Any

from .audit import canonical_json
from .policy import Policy


class PolicyRepositoryError(ValueError):
    """Raised when policy history invariants are violated."""


def policy_content_hash(policy: Policy) -> str:
    return hashlib.sha256(canonical_json(policy).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class StoredPolicy:
    policy: Policy
    content_hash: str
    activated_by_user_id: str
    activated_at: datetime


@dataclass(frozen=True, slots=True)
class PolicyFieldChange:
    field: str
    before: Any
    after: Any


class PolicyRepository:
    def __init__(self) -> None:
        self._guard = Lock()
        self._versions: dict[tuple[str, str], StoredPolicy] = {}
        self._active: dict[str, str] = {}

    def activate(
        self,
        policy: Policy,
        *,
        activated_by_user_id: str,
        activated_at: datetime | None = None,
    ) -> StoredPolicy:
        timestamp = activated_at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            raise PolicyRepositoryError("Policy activation timestamp must be timezone-aware.")
        if not activated_by_user_id.strip():
            raise PolicyRepositoryError("Policy activator is required.")
        scope = (policy.organization_id, policy.version)
        stored = StoredPolicy(
            policy=policy,
            content_hash=policy_content_hash(policy),
            activated_by_user_id=activated_by_user_id,
            activated_at=timestamp,
        )
        with self._guard:
            if scope in self._versions:
                raise PolicyRepositoryError("Policy versions are immutable and cannot be overwritten.")
            self._versions[scope] = stored
            self._active[policy.organization_id] = policy.version
        return stored

    def get(self, *, organization_id: str, version: str) -> StoredPolicy:
        with self._guard:
            try:
                return self._versions[(organization_id, version)]
            except KeyError as exc:
                raise PolicyRepositoryError("Policy version was not found in this organization.") from exc

    def active(self, *, organization_id: str) -> StoredPolicy:
        with self._guard:
            version = self._active.get(organization_id)
        if version is None:
            raise PolicyRepositoryError("No active policy exists for this organization.")
        return self.get(organization_id=organization_id, version=version)

    def diff(
        self,
        *,
        organization_id: str,
        from_version: str,
        to_version: str,
    ) -> tuple[PolicyFieldChange, ...]:
        before = asdict(self.get(organization_id=organization_id, version=from_version).policy)
        after = asdict(self.get(organization_id=organization_id, version=to_version).policy)
        return tuple(
            PolicyFieldChange(field=field, before=before[field], after=after[field])
            for field in sorted(before)
            if before[field] != after[field]
        )
