"""Package vapi is a thin client for the Vapi REST API.

Only the calls Phase 0 needs are implemented: create a call, and fetch one
(the latter is for the Phase 2 reconciler, PLAN.md S6).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import requests

_DEFAULT_BASE_URL = "https://api.vapi.ai"


@dataclass
class Customer:
    number: str  # E.164

    def to_json(self) -> dict[str, Any]:
        return {"number": self.number}


@dataclass
class PromptMessage:
    role: str
    content: str

    def to_json(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.content}


@dataclass
class ModelOverride:
    provider: str = ""
    model: str = ""
    messages: list[PromptMessage] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.provider:
            d["provider"] = self.provider
        if self.model:
            d["model"] = self.model
        if self.messages:
            d["messages"] = [m.to_json() for m in self.messages]
        return d


@dataclass
class VoiceOverride:
    """Swaps the assistant's voice for one call. Omitted entirely when no
    profile is configured, so the assistant keeps its dashboard default
    rather than being handed a half-populated object.
    """

    provider: str
    voice_id: str
    model: str = ""

    def to_json(self) -> dict[str, Any]:
        d = {"provider": self.provider, "voiceId": self.voice_id}
        if self.model:
            d["model"] = self.model
        return d


@dataclass
class AssistantOverrides:
    """Carries the per-call system prompt and the hard duration cap. The
    prompt is per-call because it encodes the specific mandate; the cap is
    per-call because it is the only guaranteed way a bot-to-bot call ends
    (PLAN.md S3.2).
    """

    # first_message is spoken before the model runs, so the callee hears
    # something immediately. Set per call so it can name the order -- and
    # so it is the ONLY greeting, rather than one the model then repeats.
    first_message: str = ""
    max_duration_seconds: int = 0
    model: Optional[ModelOverride] = None
    voice: Optional[VoiceOverride] = None

    def to_json(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.first_message:
            d["firstMessage"] = self.first_message
        if self.max_duration_seconds:
            d["maxDurationSeconds"] = self.max_duration_seconds
        if self.model is not None:
            d["model"] = self.model.to_json()
        if self.voice is not None:
            d["voice"] = self.voice.to_json()
        return d


@dataclass
class CreateCallRequest:
    phone_number_id: str
    assistant_id: str
    customer: Customer
    assistant_overrides: Optional[AssistantOverrides] = None

    def to_json(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "phoneNumberId": self.phone_number_id,
            "assistantId": self.assistant_id,
            "customer": self.customer.to_json(),
        }
        if self.assistant_overrides is not None:
            d["assistantOverrides"] = self.assistant_overrides.to_json()
        return d


@dataclass
class CallResponse:
    """Deliberately partial. Vapi returns considerably more; we decode
    only what we store and keep the raw body for anything else.
    """

    id: str = ""
    status: str = ""
    created_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class Client:
    def __init__(self, api_key: str, base_url: str = _DEFAULT_BASE_URL, timeout: float = 20.0):
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()

    def create_call(self, req: CreateCallRequest) -> CallResponse:
        return self._do("POST", "/call", req.to_json())

    def get_call(self, id: str) -> CallResponse:
        return self._do("GET", f"/call/{id}", None)

    def _do(self, method: str, path: str, body: Optional[dict[str, Any]]) -> CallResponse:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            resp = self.session.request(
                method, self.base_url + path, json=body, headers=headers, timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise RuntimeError(f"vapi {method} {path}: {e}") from e

        raw_text = resp.text[:1 << 20]
        if not (200 <= resp.status_code < 300):
            raise RuntimeError(
                f"vapi {method} {path}: {resp.status_code} {resp.reason}: {_truncate(raw_text, 400)}"
            )
        try:
            data = resp.json()
        except ValueError as e:
            raise RuntimeError(f"vapi {method} {path}: decode: {e}") from e

        out = CallResponse(
            id=data.get("id", ""), status=data.get("status", ""),
            created_at=data.get("createdAt", ""), raw=data,
        )
        return out


def _truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[:n] + "…"
