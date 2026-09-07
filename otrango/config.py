"""Package config loads runtime configuration from the environment."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from otrango.profile.profile import Profile
from otrango.voice.voice import NAME_AGENT, NAME_MINE, Profile as VoiceProfile, Registry as VoiceRegistry


@dataclass
class Config:
    port: str = "8080"
    database_path: str = "./otrango.db"
    public_base_url: str = ""

    vapi_api_key: str = ""
    vapi_phone_number_id: str = ""
    vapi_assistant_id: str = ""
    vapi_webhook_secret: str = ""

    # telegram_bot_token and telegram_chat_id are the owner's channel, in
    # both directions. telegram_chat_id is the only chat the poller
    # accepts and the destination for outcome messages, so it is what
    # stops anyone who knows the bot's handle from making this service
    # place a call.
    telegram_bot_token: str = ""
    telegram_chat_id: int = 0

    owner_user_id: str = "owner"
    customer_name: str = "Mahesh"
    # business_name is the counterparty as a human would name it. Without
    # it a confirmation can only say "calling +1415…", which tells the
    # owner nothing about what they are authorizing.
    business_name: str = "the restaurant"

    # target_phone_number is the counterparty dialed when a mandate omits
    # one.
    target_phone_number: str = ""
    test_phone_number: str = ""

    anthropic_api_key: str = ""
    # llm_model is the model behind order PARSING (parse.LLM) and the T0
    # eval tier. Anthropic-only: parse.LLM and the eval harness both talk
    # to the Anthropic SDK directly with anthropic_api_key, not through
    # Vapi, so this cannot point at another provider.
    llm_model: str = "claude-haiku-4-5-20251001"

    # voice_llm_provider/voice_llm_model are what actually goes on the
    # PHONE CALL, via a Vapi assistantOverride (adapter.Caller). These are
    # separate from llm_model on purpose: Vapi itself talks to the
    # provider, so this can be any provider/model Vapi supports (Anthropic,
    # OpenAI, ...) independent of what parses orders. The tradeoff this
    # creates: T0 evals replay text against llm_model, so once these two
    # diverge, T0 no longer proves what a live call actually says -- know
    # that before pointing this at a different provider than llm_model.
    # Blank model falls back to llm_model, so the common case (both on the
    # same Anthropic model) needs no second setting.
    voice_llm_provider: str = "anthropic"
    voice_llm_model: str = ""

    # profile is the owner's standing preferences: places, menus, routing.
    # None when no file is configured; the skills fall back to a
    # built-in menu.
    profile: Optional[Profile] = None

    # voices holds the selectable TTS profiles. Voice affects turn timing,
    # so it is a variable to sweep, not a setting to fix (RESEARCH.md
    # S2.4).
    voices: Optional[VoiceRegistry] = None

    def telegram_ready(self) -> bool:
        """Reports whether the trigger and outcome path can run."""
        return bool(self.telegram_bot_token) and self.telegram_chat_id != 0

    def require_vapi(self) -> Optional[str]:
        """Reports what is missing for outbound dialing, as an error
        message, or None when everything needed is present. The server
        starts without these so the console and webhook sink stay usable
        before signup.
        """
        missing = []
        if not self.vapi_api_key:
            missing.append("VAPI_API_KEY")
        if not self.vapi_phone_number_id:
            missing.append("VAPI_PHONE_NUMBER_ID")
        if not self.vapi_assistant_id:
            missing.append("VAPI_ASSISTANT_ID")
        if missing:
            return "missing: " + ", ".join(missing)
        return None


def _env_or(key: str, fallback: str) -> str:
    return os.environ.get(key) or fallback


def _load_dotenv(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("\"'")
            if key not in os.environ:
                os.environ[key] = val


def load(dotenv_path: str = ".env") -> Config:
    """Reads .env (if present) into the environment, then builds a
    Config. Real environment variables always win over .env values.
    """
    _load_dotenv(dotenv_path)

    c = Config(
        port=_env_or("PORT", "8080"),
        database_path=_env_or("DATABASE_PATH", "./otrango.db"),
        public_base_url=os.environ.get("PUBLIC_BASE_URL", ""),
        vapi_api_key=os.environ.get("VAPI_API_KEY", ""),
        vapi_phone_number_id=os.environ.get("VAPI_PHONE_NUMBER_ID", ""),
        vapi_assistant_id=os.environ.get("VAPI_ASSISTANT_ID", ""),
        vapi_webhook_secret=os.environ.get("VAPI_WEBHOOK_SECRET", ""),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        owner_user_id=_env_or("OWNER_USER_ID", "owner"),
        customer_name=_env_or("CUSTOMER_NAME", "Mahesh"),
        business_name=_env_or("TARGET_BUSINESS_NAME", "the restaurant"),
        target_phone_number=os.environ.get("TARGET_PHONE_NUMBER", ""),
        test_phone_number=os.environ.get("TEST_PHONE_NUMBER", ""),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        # Talks to the Anthropic SDK directly (parse.LLM, the T0 harness),
        # not through Vapi -- Anthropic only. A voice agent lives inside a
        # sub-second turn budget (PLAN.md S3.1), so the default is the
        # fastest capable model rather than the most capable one.
        llm_model=_env_or("OTRANGO_LLM_MODEL", "claude-haiku-4-5-20251001"),
        # What actually goes on the call, via Vapi -- can be any
        # provider/model Vapi supports. Must be a DATED id if the provider
        # is Anthropic: Vapi validates against its own allow-list and
        # rejects the undated aliases the Anthropic API accepts (its list
        # tops out at claude-sonnet-5 -- it does not accept opus). Other
        # providers (e.g. openai) use their own normal model names.
        voice_llm_provider=_env_or("VOICE_LLM_PROVIDER", "anthropic"),
        voice_llm_model=os.environ.get("VOICE_LLM_MODEL", ""),
    )

    # The common case is one model for both parsing and the call, so a
    # blank voice_llm_model means "same as llm_model" rather than a second
    # setting everyone has to keep in sync.
    if not c.voice_llm_model:
        c.voice_llm_model = c.llm_model

    # A chat ID that does not parse is a hard error rather than a zero
    # value: silently leaving it unset would start the bot with a
    # whitelist nothing matches, which looks exactly like a bot that is
    # merely quiet.
    v = os.environ.get("TELEGRAM_CHAT_ID", "")
    if v:
        try:
            c.telegram_chat_id = int(v)
        except ValueError as e:
            raise ValueError(f"TELEGRAM_CHAT_ID {v!r} is not a number: {e}") from e

    # Two profiles so an A/B sweep needs no code change: the stock
    # provider voice, and the operator's cloned voice. Either may be left
    # unset, in which case calls fall back to whatever the assistant
    # already carries. A profile that exists but does not parse is a
    # hard error: silently falling back to the built-in menu would dial
    # the wrong place.
    path = _env_or("PROFILE_PATH", "./profile.json")
    if path:
        if os.path.exists(path):
            prof = Profile.load(path)
            c.profile = prof
            if not c.customer_name and prof.customer_name:
                c.customer_name = prof.customer_name

    c.voices = VoiceRegistry(
        _env_or("DEFAULT_VOICE", NAME_AGENT),
        VoiceProfile(
            name=NAME_AGENT, label="Stock agent voice",
            provider=os.environ.get("VOICE_PROVIDER", ""),
            voice_id=os.environ.get("VOICE_ID", ""),
            model=os.environ.get("VOICE_MODEL", ""),
        ),
        VoiceProfile(
            name=NAME_MINE, label="Cloned operator voice",
            provider=os.environ.get("MY_VOICE_PROVIDER", ""),
            voice_id=os.environ.get("MY_VOICE_ID", ""),
            model=os.environ.get("MY_VOICE_MODEL", ""),
        ),
    )
    return c
