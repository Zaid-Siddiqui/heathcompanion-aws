# Health Companion

Bilingual (Arabic / English) symptom-triage agent for UAE residents, built for the
AWS Agentic AI Hackathon — Future Vision (Dubai, 2026) on **Amazon Bedrock AgentCore**
with the **Strands Agents SDK**.

> Fatima, 58, diabetic, Arabic-speaking, describes her symptoms. The agent gauges urgency
> against clinical triage guidelines, checks her history and medications for red flags,
> finds a covered specialist, and writes a bilingual visit summary for the doctor —
> saving an unnecessary ER visit and getting her to the right care the same day.

## What it does

| Step | Tool | Backed by |
|---|---|---|
| 1 | `triage_symptoms` | Bedrock Knowledge Base (triage guidelines) → emergency / urgent / routine / self-care, with citation |
| 2 | `get_patient_history` | DynamoDB patients table |
| 3 | `check_medications` | patient meds + interaction reference in the KB |
| 4 | `find_specialist` | DynamoDB providers table, filtered by specialty, insurance, availability |
| 5 | `book_appointment` | records the booking on the patient file, returns "what to mention" reminder |
| 6 | `create_visit_summary` | bilingual Markdown summary saved to S3 |

Cross-cutting: AgentCore **Memory** (symptom history, preferred language), AgentCore
**Gateway** (tools as Lambda), Bedrock **Guardrails** baseline.

## Safety behaviour (the point of the track)

- **Emergency short-circuit** — chest pain, stroke signs, breathing difficulty, severe
  bleeding → "Call **998** now" before any other tool runs.
- **No diagnosis** — "I can't diagnose, but here's who to see."
- **No dosing** — clear refusal plus a pointer to a pharmacist or doctor.
- Responds in the user's language (Arabic or English).
- PHI stays in-region (`me-central-1` in production; `us-west-2` for the hackathon).

## Layout

```
HealthCompanion/
  agentcore/                    deployment config (agentcore.json) + CDK project
  app/HealthCompanionAgent/     Strands agent: main.py, safety.py (998 / no-dose / no-diagnosis), gateway.py
  tools/health_tools/           one Lambda behind AgentCore Gateway, six tools
  tests/                        pytest: emergency short-circuit, refusals, triage levels, tool logic
RESOURCES.md                    inventory of the pre-provisioned workshop resources
```

## Running

```bash
cd HealthCompanion
agentcore dev        # local hot-reload server on :8080 + inspector (uses in-process tools before first deploy)
agentcore deploy     # build + deploy runtime, memory, gateway and Lambda to us-west-2
agentcore invoke --prompt "عندي صداع منذ ثلاثة أيام ورؤيتي مشوشة"
app/HealthCompanionAgent/.venv/Scripts/python -m pytest
```

All resource IDs are read from SSM Parameter Store under `/app/workshop/...` — nothing is hardcoded.
