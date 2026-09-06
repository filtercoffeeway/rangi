"""Caller adapts the Vapi client to the usecase port. Everything the use
cases know about telephony stops here: the provider's request shape, the
per-call overrides, and the fact that a voice profile becomes a JSON
object.
"""
from __future__ import annotations

from dataclasses import dataclass

from otrango.usecase.ports import CallRequest
from otrango.vapi.client import (
    AssistantOverrides,
    Client,
    CreateCallRequest,
    Customer,
    ModelOverride,
    PromptMessage,
    VoiceOverride,
)


@dataclass
class Caller:
    client: Client
    phone_number_id: str
    assistant_id: str
    # provider and model must accompany a per-call model override: Vapi
    # validates the whole model object, so sending only messages is a 400.
    # Omitting them is invisible until an actual dial, which is why it
    # survived every test.
    provider: str = ""
    model: str = ""

    def place(self, req: CallRequest) -> str:
        overrides = AssistantOverrides(
            first_message=req.greeting,
            max_duration_seconds=req.max_duration_sec,
            model=ModelOverride(
                provider=self._provider(),
                model=self.model,
                messages=[PromptMessage(role="system", content=req.system_prompt)],
            ),
        )
        # Omitted entirely when unconfigured, so the assistant keeps its
        # own default rather than receiving a half-populated voice object.
        if req.voice.configured():
            overrides.voice = VoiceOverride(
                provider=req.voice.provider, voice_id=req.voice.voice_id, model=req.voice.model,
            )

        resp = self.client.create_call(CreateCallRequest(
            phone_number_id=self.phone_number_id,
            assistant_id=self.assistant_id,
            customer=Customer(number=req.to),
            assistant_overrides=overrides,
        ))
        return resp.id

    def lookup(self, provider_call_id: str) -> str:
        resp = self.client.get_call(provider_call_id)
        ended_reason = resp.raw.get("endedReason") or ""
        if ended_reason:
            return ended_reason
        if resp.raw.get("status") == "ended":
            return "ended"
        return ""

    def _provider(self) -> str:
        return self.provider or "anthropic"
