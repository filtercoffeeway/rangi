"""Package objective is the vertical-agnostic authority layer (PLAN.md S2).

It knows nothing about coffee, restaurants, or menus. A mandate says what an
agent is authorized to do; constraints say where the boundary is; the server
-- not the model -- decides whether a proposal is inside it (PLAN.md S5.3).

Phase 4's pass condition is that adding a second vertical changes nothing in
this package.
"""
from .call import Call, CallUpdate, Event, CALL_QUEUED, CALL_DIALING, CALL_IN_PROGRESS, CALL_ENDED, CALL_FAILED
from .mandate import (
    Constraints,
    Decision,
    LineItem,
    Mandate,
    Proposal,
    STATUS_AUTHORIZED,
    STATUS_CONSUMED,
    STATUS_DRAFT,
    STATUS_EXPIRED,
    STATUS_SUPERSEDED,
    SOURCE_CONSOLE,
    SOURCE_TELEGRAM,
    money,
)
from .outcome import (
    Outcome,
    Classify,
    ClassifyEndReason,
    MachineGreeting,
    RESULT_AMBIGUOUS,
    RESULT_ESCALATED,
    RESULT_FAILED,
    RESULT_PARTIAL,
    RESULT_REFUSED,
    RESULT_SUCCESS,
    RESULT_UNREACH,
    CONF_HIGH,
    CONF_LOW,
    CONF_MEDIUM,
    FAIL_AMBIGUOUS,
    FAIL_BUSY,
    FAIL_CALL_TIMEOUT,
    FAIL_CLOSED,
    FAIL_DEADLOCK,
    FAIL_DRY_RUN,
    FAIL_IVR_TRAPPED,
    FAIL_ITEM_UNAVAILABLE,
    FAIL_LANGUAGE,
    FAIL_MANDATE_EXPIRED,
    FAIL_MUTUAL_BARGE_IN,
    FAIL_NO_ANSWER,
    FAIL_PEER_REFUSED,
    FAIL_POLITENESS_LOOP,
    FAIL_PRICE_EXCEEDED,
    FAIL_PROVIDER_ERROR,
    FAIL_TURN_CAP,
    FAIL_VOICEMAIL,
    FAIL_WRONG_NUMBER,
)

__all__ = [
    "Call", "CallUpdate", "Event",
    "CALL_QUEUED", "CALL_DIALING", "CALL_IN_PROGRESS", "CALL_ENDED", "CALL_FAILED",
    "Constraints", "Decision", "LineItem", "Mandate", "Proposal",
    "STATUS_DRAFT", "STATUS_AUTHORIZED", "STATUS_CONSUMED", "STATUS_EXPIRED", "STATUS_SUPERSEDED",
    "SOURCE_CONSOLE", "SOURCE_TELEGRAM", "money",
    "Outcome", "Classify", "ClassifyEndReason", "MachineGreeting",
    "RESULT_SUCCESS", "RESULT_PARTIAL", "RESULT_FAILED", "RESULT_ESCALATED",
    "RESULT_REFUSED", "RESULT_UNREACH", "RESULT_AMBIGUOUS",
    "CONF_HIGH", "CONF_MEDIUM", "CONF_LOW",
    "FAIL_NO_ANSWER", "FAIL_BUSY", "FAIL_VOICEMAIL", "FAIL_IVR_TRAPPED", "FAIL_CLOSED",
    "FAIL_WRONG_NUMBER", "FAIL_ITEM_UNAVAILABLE", "FAIL_PRICE_EXCEEDED", "FAIL_PEER_REFUSED",
    "FAIL_LANGUAGE", "FAIL_DEADLOCK", "FAIL_MUTUAL_BARGE_IN", "FAIL_POLITENESS_LOOP",
    "FAIL_TURN_CAP", "FAIL_MANDATE_EXPIRED", "FAIL_CALL_TIMEOUT", "FAIL_PROVIDER_ERROR",
    "FAIL_AMBIGUOUS", "FAIL_DRY_RUN",
]
