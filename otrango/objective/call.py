from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from otrango.jsonutil import iso, omit_none

# Call status values.
CALL_QUEUED = "queued"
CALL_DIALING = "dialing"
CALL_IN_PROGRESS = "in_progress"
CALL_ENDED = "ended"
CALL_FAILED = "failed"


@dataclass
class Call:
    """One attempt to carry out a mandate over the phone. It is an entity,
    not a database row: persistence stores it, but the rules about it live
    here.
    """

    id: str
    to_number: str
    status: str
    provider: str = "vapi"
    mandate_id: str | None = None
    provider_call_id: str | None = None
    end_reason: str | None = None
    recording_url: str | None = None
    transcript: str | None = None
    cost_cents: int | None = None
    voice_profile: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None

    def to_json(self) -> dict[str, Any]:
        return omit_none({
            "id": self.id,
            "mandate_id": self.mandate_id,
            "provider": self.provider,
            "provider_call_id": self.provider_call_id,
            "to_number": self.to_number,
            "status": self.status,
            "end_reason": self.end_reason,
            "recording_url": self.recording_url,
            "transcript": self.transcript,
            "cost_cents": self.cost_cents,
            "voice_profile": self.voice_profile,
            "created_at": iso(self.created_at),
            "started_at": iso(self.started_at),
            "ended_at": iso(self.ended_at),
        })


@dataclass
class CallUpdate:
    """Carries the fields a webhook may touch. None means "not present in
    this payload" and leaves the stored value alone -- webhooks arrive
    partial and out of order, so absent must never overwrite known.
    """

    status: str | None = None
    end_reason: str | None = None
    recording_url: str | None = None
    transcript: str | None = None
    cost_cents: int | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


@dataclass
class Event:
    """One delivery from the telephony provider, stored raw so anything
    mis-parsed today stays recoverable from the database.
    """

    id: int
    kind: str
    payload: str
    call_id: str | None = None
    received_at: datetime | None = None

    def to_json(self) -> dict[str, Any]:
        return omit_none({
            "id": self.id,
            "call_id": self.call_id,
            "kind": self.kind,
            "payload": self.payload,
            "received_at": iso(self.received_at),
        })
