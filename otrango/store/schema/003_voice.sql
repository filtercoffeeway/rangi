-- Voice is an experimental variable: TTS time-to-first-byte feeds into
-- inter-turn gaps, which is what produces the agent-to-agent pathologies in
-- PLAN.md §3.2. Recording it per call lets turn_metrics be grouped by voice
-- rather than silently confounded by it (RESEARCH.md §2.4).
ALTER TABLE calls ADD COLUMN voice_profile TEXT;

CREATE INDEX calls_voice ON calls (voice_profile);
