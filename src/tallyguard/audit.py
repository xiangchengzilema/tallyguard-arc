"""Tamper-evident append-only audit events."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any


GENESIS_HASH = "0" * 64


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    sequence: int
    aggregate_type: str
    aggregate_id: str
    event_type: str
    payload: dict[str, Any]
    previous_hash: str
    event_hash: str
    created_at: datetime


def audit_event_hash(
    *,
    sequence: int,
    aggregate_type: str,
    aggregate_id: str,
    event_type: str,
    payload: dict[str, Any],
    previous_hash: str,
    created_at: datetime,
) -> str:
    envelope = {
        "sequence": sequence,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "event_type": event_type,
        "payload": payload,
        "previous_hash": previous_hash,
        "created_at": created_at,
    }
    return hashlib.sha256(canonical_json(envelope).encode("utf-8")).hexdigest()


class AuditChain:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    @property
    def events(self) -> tuple[AuditEvent, ...]:
        return tuple(self._events)

    def append(
        self,
        *,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any],
        created_at: datetime | None = None,
    ) -> AuditEvent:
        timestamp = created_at or datetime.now(timezone.utc)
        sequence = len(self._events) + 1
        previous_hash = self._events[-1].event_hash if self._events else GENESIS_HASH
        event_hash = audit_event_hash(
            sequence=sequence,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload,
            previous_hash=previous_hash,
            created_at=timestamp,
        )
        event = AuditEvent(
            sequence=sequence,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload,
            previous_hash=previous_hash,
            event_hash=event_hash,
            created_at=timestamp,
        )
        self._events.append(event)
        return event

    def verify(self) -> bool:
        previous_hash = GENESIS_HASH
        for expected_sequence, event in enumerate(self._events, start=1):
            if event.sequence != expected_sequence or event.previous_hash != previous_hash:
                return False
            expected_hash = audit_event_hash(
                sequence=event.sequence,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                event_type=event.event_type,
                payload=event.payload,
                previous_hash=event.previous_hash,
                created_at=event.created_at,
            )
            if event.event_hash != expected_hash:
                return False
            previous_hash = event.event_hash
        return True

