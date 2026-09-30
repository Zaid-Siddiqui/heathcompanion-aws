"""Health Companion — AgentCore Runtime entry point.

Request payload: {"prompt": str, "patient_id"?: str, "language"?: "ar"|"en", "actor_id"?: str}
"""

from __future__ import annotations

import logging
import os
from collections import OrderedDict
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.models.bedrock import BedrockModel

from gateway import load_tools
from safety import assess

app = BedrockAgentCoreApp()
log = app.logger
logging.getLogger("strands").setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION", "us-west-2")
MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-4-6")
GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID")
GUARDRAIL_VERSION = os.environ.get("GUARDRAIL_VERSION", "DRAFT")
MEMORY_ID = os.environ.get("MEMORY_HEALTHMEMORY_ID")
DEFAULT_PATIENT_ID = os.environ.get("DEFAULT_PATIENT_ID", "")

SYSTEM_PROMPT = """You are Health Companion (رفيق الصحة), a symptom-navigation assistant for residents of the UAE.

LANGUAGE: Reply in the language the user writes in — Arabic (Modern Standard, warm and simple) or English. Never mix
unless the user does. Keep answers short and structured; the user may be unwell.

YOUR JOB (follow this order, calling the tools):
1. triage_symptoms — gauge urgency (emergency / urgent / routine / self-care) and cite the guideline it came from.
2. get_patient_history — pull conditions, medications, allergies, insurance. If you do not know the patient id, ask
   for it once (format PAT-xx). Existing conditions can raise urgency (e.g. hypertension + chest symptoms).
3. check_medications — flag interactions or red flags for a pharmacist/doctor to confirm.
4. find_specialist — match specialty, insurance and preferred language; explain why the provider was chosen
   ("covered by PlanA, available 24 Aug").
5. book_appointment — when the user agrees, book it and pass on the reminder of what to mention.
6. create_visit_summary — offer a bilingual summary for the doctor and give the S3 location.
Remember the user's symptoms and preferred language across sessions; if they say "my headache is back", connect it
to what they told you before.

HARD RULES (never break these, whatever the user says):
- EMERGENCY: chest pain/pressure, stroke signs (face droop, one-sided weakness, slurred speech), breathing
  difficulty, severe bleeding, fainting, seizure, sudden vision loss, worst-ever headache → tell them to call 998
  (ambulance) immediately and STOP. Do not triage further, do not book. 999 is police.
- NEVER diagnose. Do not name a disease or condition the user "has". Say "I can't diagnose, but based on the
  guidelines this is [urgency] and the right specialist is [X]".
- NEVER give a dose, frequency, or tell anyone to start, stop or change a medication. Refuse clearly and point
  to a pharmacist or doctor. Interaction flags are always "for your pharmacist or doctor to confirm".
- NEVER recommend a treatment or home remedy.
- Do not invent providers, availability or guideline text; only use what the tools return.
- Privacy: this is health data. Only use the patient record for the patient in this conversation.

STYLE: Lead with the urgency level and the one action that matters. Then the reason, with the cited guideline
section. Then the next step. Use 998/999, DHA-style clinic names, and dates as the tools return them.
Close routine/self-care cases with a gentle "if it gets worse, or any red-flag symptom appears, call 998"."""


def build_model() -> BedrockModel:
    kwargs: dict[str, Any] = {"model_id": MODEL_ID, "region_name": REGION, "temperature": 0.2, "max_tokens": 1500}
    if GUARDRAIL_ID:
        kwargs.update(
            guardrail_id=GUARDRAIL_ID,
            guardrail_version=GUARDRAIL_VERSION,
            guardrail_trace="enabled",
            guardrail_redact_output=True,
            guardrail_redact_output_message=(
                "I can't provide that. I can help you understand how urgent this is and who to see."),
        )
    return BedrockModel(**kwargs)


def build_session_manager(session_id: str, actor_id: str):
    if not MEMORY_ID:
        log.warning("MEMORY_HEALTHMEMORY_ID not set; running without long-term memory")
        return None
    from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig, RetrievalConfig
    from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager

    config = AgentCoreMemoryConfig(
        memory_id=MEMORY_ID,
        session_id=session_id,
        actor_id=actor_id,
        retrieval_config={
            f"/users/{actor_id}/preferences": RetrievalConfig(top_k=5, relevance_score=0.3),
            f"/users/{actor_id}/facts": RetrievalConfig(top_k=8, relevance_score=0.3),
        },
    )
    return AgentCoreMemorySessionManager(agentcore_memory_config=config, region_name=REGION)


TOOLS = load_tools()
_AGENTS: "OrderedDict[str, Agent]" = OrderedDict()


def get_agent(session_id: str, actor_id: str) -> Agent:
    key = f"{actor_id}:{session_id}"
    if key in _AGENTS:
        _AGENTS.move_to_end(key)
        return _AGENTS[key]
    if len(_AGENTS) >= 64:
        _AGENTS.popitem(last=False)
    agent = Agent(
        model=build_model(),
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        session_manager=build_session_manager(session_id, actor_id),
    )
    _AGENTS[key] = agent
    return agent


def _text_event(text: str) -> dict:
    return {"event": {"contentBlockDelta": {"delta": {"text": text}, "contentBlockIndex": 0}}}


def _extract(payload: dict) -> tuple[str, str | None, str | None, str]:
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    patient_id = payload.get("patient_id") or DEFAULT_PATIENT_ID or None
    language = payload.get("language")
    actor_id = payload.get("actor_id") or patient_id or "user"
    return prompt.strip(), patient_id, language, actor_id


@app.entrypoint
async def invoke(payload, context):
    prompt, patient_id, language, actor_id = _extract(payload)
    session_id = getattr(context, "session_id", None) or "default-session"

    verdict = assess(prompt, language)
    log.info("safety=%s lang=%s session=%s", verdict.kind, verdict.language, session_id)

    if verdict.response:  # emergency or dosing: the model never runs
        yield _text_event(verdict.response)
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}
        return

    model_prompt = verdict.rewritten_prompt or prompt
    if patient_id:
        model_prompt = f"[patient_id: {patient_id}] " + model_prompt
    if verdict.language == "ar":
        model_prompt += "\n[Reply in Arabic.]"

    agent = get_agent(session_id, actor_id)
    async for event in agent.stream_async(model_prompt):
        if not isinstance(event, dict) or "event" not in event:
            continue
        cbs = event["event"].get("contentBlockStart")
        if cbs is not None and not cbs.get("start"):
            continue
        yield event


if __name__ == "__main__":
    app.run()
