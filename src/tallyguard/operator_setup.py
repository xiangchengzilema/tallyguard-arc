"""Provision durable role-separated operator access without public demo sessions.

The command performs no network request and cannot move funds. It writes only
session-token hashes to SQLite; raw bearer tokens are returned once to the
invoking terminal and must be handled as secrets.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import timedelta
import json
import os
from pathlib import Path
import re

from .auth import Authenticator, Principal, Role
from .environment import load_local_environment
from .persistence import SqliteRepository


CONFIRMATION_PHRASE = "PROVISION-TALLYGUARD-OPERATORS"
MAXIMUM_SESSION_HOURS = 24
ORGANIZATION_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")


class OperatorSetupError(RuntimeError):
    """Raised when local operator access cannot be provisioned safely."""


@dataclass(frozen=True, slots=True)
class OperatorSession:
    role: str
    user_id: str
    display_name: str
    bearer_token: str
    expires_at: str
    user_created: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OperatorAccessBundle:
    organization_id: str
    organization_name: str
    organization_created: bool
    database_path: str
    sessions: tuple[OperatorSession, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "classification": "sensitive local operator access; never commit or share",
            "network_activity": "none",
            "funds_moved": False,
            "organization_id": self.organization_id,
            "organization_name": self.organization_name,
            "organization_created": self.organization_created,
            "database_path": self.database_path,
            "sessions": [session.to_dict() for session in self.sessions],
        }


ROLE_IDENTITIES: tuple[tuple[str, str, Role], ...] = (
    ("admin", "Policy Administrator", Role.ADMIN),
    ("operator", "Finance Operator", Role.FINANCE_OPERATOR),
    ("approver", "Payment Approver", Role.APPROVER),
    ("auditor", "Audit Reviewer", Role.AUDITOR),
)


def provision_operator_access(
    *,
    database_path: str | Path,
    organization_id: str,
    organization_name: str,
    confirmation: str,
    session_hours: int = 8,
) -> OperatorAccessBundle:
    """Ensure four durable principals and issue one opaque session per role."""

    normalized_id = organization_id.strip()
    normalized_name = organization_name.strip()
    if confirmation != CONFIRMATION_PHRASE:
        raise OperatorSetupError(
            f"Operator provisioning confirmation must exactly equal {CONFIRMATION_PHRASE}."
        )
    if not ORGANIZATION_ID_PATTERN.fullmatch(normalized_id):
        raise OperatorSetupError(
            "Organization ID must be 3-64 lowercase letters, digits, hyphens, or underscores."
        )
    if not normalized_name or len(normalized_name) > 120:
        raise OperatorSetupError("Organization name must contain 1-120 characters.")
    if session_hours < 1 or session_hours > MAXIMUM_SESSION_HOURS:
        raise OperatorSetupError(
            f"Session lifetime must be between 1 and {MAXIMUM_SESSION_HOURS} hours."
        )

    resolved_path = Path(database_path).expanduser().resolve()
    repository = SqliteRepository(resolved_path)
    try:
        organization_created = repository.ensure_organization(
            organization_id=normalized_id,
            name=normalized_name,
        )
        authenticator = Authenticator(store=repository)
        sessions: list[OperatorSession] = []
        for suffix, display_name, role in ROLE_IDENTITIES:
            user_id = f"{normalized_id}-{suffix}"
            user_created = repository.ensure_user(
                organization_id=normalized_id,
                user_id=user_id,
                display_name=display_name,
                roles=(role.value,),
            )
            bearer_token, session = authenticator.issue_session(
                Principal(
                    user_id=user_id,
                    organization_id=normalized_id,
                    roles=(role,),
                ),
                lifetime=timedelta(hours=session_hours),
            )
            sessions.append(
                OperatorSession(
                    role=role.value,
                    user_id=user_id,
                    display_name=display_name,
                    bearer_token=bearer_token,
                    expires_at=session.expires_at.isoformat(),
                    user_created=user_created,
                )
            )
        return OperatorAccessBundle(
            organization_id=normalized_id,
            organization_name=normalized_name,
            organization_created=organization_created,
            database_path=str(resolved_path),
            sessions=tuple(sessions),
        )
    finally:
        repository.close()


def main() -> None:
    load_local_environment()
    parser = argparse.ArgumentParser(
        description=(
            "Create or verify a durable TallyGuard organization and issue one "
            "short-lived opaque session for each separated finance role"
        )
    )
    parser.add_argument(
        "--database",
        default=os.getenv("TALLYGUARD_DATABASE_PATH", "data/tallyguard.sqlite3"),
        help="SQLite database used by the TallyGuard API",
    )
    parser.add_argument(
        "--organization-id",
        required=True,
        help="stable lowercase tenant identifier",
    )
    parser.add_argument(
        "--organization-name",
        required=True,
        help="human-readable tenant name",
    )
    parser.add_argument(
        "--session-hours",
        type=int,
        default=8,
        help=f"session lifetime from 1 to {MAXIMUM_SESSION_HOURS} hours",
    )
    parser.add_argument(
        "--confirm",
        required=True,
        help=f"must exactly equal {CONFIRMATION_PHRASE}",
    )
    args = parser.parse_args()

    bundle = provision_operator_access(
        database_path=args.database,
        organization_id=args.organization_id,
        organization_name=args.organization_name,
        confirmation=args.confirm,
        session_hours=args.session_hours,
    )
    print(json.dumps(bundle.to_dict(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
