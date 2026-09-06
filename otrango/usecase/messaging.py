from __future__ import annotations

from otrango.skills.coffee.coffee import ErrAsk
from otrango.usecase.ordering import DraftRequest

# ReservedByCarrier are the opt-out keywords the carrier intercepts before
# this service is reached: it replies itself and blocks the number for
# good.
#
# No command may collide with one. An earlier version used CANCEL to
# discard a draft, which would have silently unsubscribed the owner from
# their own campaign -- outcome texts would stop arriving with no error
# anywhere here.
ReservedByCarrier = ["stop", "stopall", "unsubscribe", "cancel", "end", "quit"]


class MessagingMixin:
    """See otrango.usecase.service.Service."""

    def handle_message(self, body: str, src: str) -> str:
        """Interprets an inbound text from the owner and returns the
        reply.

        A branch on exact keywords, on purpose: a model in the trigger
        path buys nothing and adds latency plus a failure mode.

        src records which channel the order arrived on. It is a parameter
        rather than a constant because every adapter shares this method, so
        a hardcoded channel would misfile every order that arrived on a
        different one.
        """
        word = body.strip().lower()
        if word in ("y", "ok", "go"):
            return self.authorize_latest()
        if word in ("n", "no", "wrong"):
            return self.dispute_latest()
        if word in ("drop", "scrap", "nevermind"):
            return self.discard_draft()
        if word in ("help", "?"):
            return (
                'Send an order ("coffee") to draft it, Y to place the call, '
                "N to flag the last result wrong, DROP to discard a pending draft."
            )

        try:
            m = self.draft(self.owner_user_id, DraftRequest(input=body), src)
        except ErrAsk as ask:
            # A question from the parser is an answer, not a failure: it
            # means the request was understood well enough to know what is
            # missing.
            return ask.question
        except Exception:
            return "Didn't understand that. Reply HELP for options."
        return self._confirmation_line(m)

    def _confirmation_line(self, m) -> str:
        """What the owner authorizes against. Uses preview, not summary: a
        draft has not been attempted, so formatting it as an outcome would
        tell the owner their un-dialed order had failed.
        """
        try:
            skill = self.skills.get(m.objective_type)
        except Exception:
            return "Draft ready — reply Y to dial."
        # The skill composes the whole description, including the cap: it
        # is one sentence the owner reads, not a summary with fields
        # appended to it.
        line = skill.preview(m)
        if m.constraints.dry_run:
            line += " (Dry run — I'll talk it through but not commit.)"
        return line + " Reply Y to confirm."

    def authorize_latest(self) -> str:
        try:
            m = self.mandates.latest_draft(self.owner_user_id)
        except Exception:
            return "Nothing pending. Send an order first."
        if self.now() > m.expires_at:
            return "That draft expired. Send the order again."
        line = "Calling now. I'll text you the result."
        try:
            sk = self.skills.get(m.objective_type)
            line = sk.calling(m) + " I'll text you the result."
        except Exception:
            pass
        try:
            self.authorize_and_dial(m.id)
        except Exception:
            return "Couldn't place the call. Check the console."
        return line

    def dispute_latest(self) -> str:
        try:
            call = self.calls.latest_needing_review()
        except Exception:
            return "Nothing recent to flag."
        try:
            self.record_verdict(call.id, "wrong")
        except Exception:
            return "Couldn't record that."
        return "Noted — marked wrong. The transcript is on the console."

    def discard_draft(self) -> str:
        try:
            m = self.mandates.latest_draft(self.owner_user_id)
        except Exception:
            return "Nothing pending to discard."
        try:
            self.mandates.supersede(m.id)
        except Exception:
            return "Couldn't discard that."
        return "Discarded. Nothing was called."
