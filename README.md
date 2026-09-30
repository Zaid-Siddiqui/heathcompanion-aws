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

### How the layers stack

| Layer | Where | Catches |
|---|---|---|
| `safety.py` (deterministic, bilingual regex) | before anything else | emergency red flags → 998; dosing questions → refusal; diagnosis requests → reframed so triage still runs |
| Baseline Bedrock Guardrail (`ApplyGuardrail`, source=INPUT) | on the raw user message | prompt attacks, hate/violence/sexual/misconduct, and its `MedicalDiagnosis` / `MedicationDosing` / `TreatmentRecommendation` topics — each mapped to a specific, useful refusal instead of the generic block text |
| System prompt + tool design | model + tools | tools never return a diagnosis or dose; interaction flags are always "for your doctor or pharmacist to confirm" |

Why the guardrail is applied on input rather than through the model's `guardrailConfig`: probing the baseline
with `ApplyGuardrail(source=OUTPUT)` showed it blocks the *compliant* phrasing the track requires
("I can't diagnose, but…" → `MedicalDiagnosis`; "flag for your doctor to confirm" → `MedicationDosing`),
which would silently replace correct answers. The same probe found its `PROMPT_ATTACK` filter fires at MEDIUM
confidence on ordinary patient phrasing ("Patient PAT-01 here, can you check my history…"), so only HIGH-confidence
prompt-attack detections block; the rest are logged.

## Layout

```
HealthCompanion/
  agentcore/                    deployment config (agentcore.json) + CDK project
  app/HealthCompanionAgent/     Strands agent: main.py, safety.py (998 / no-dose / no-diagnosis), gateway.py
  tools/health_tools/           one Lambda behind AgentCore Gateway, six tools
  tests/                        pytest: emergency short-circuit, refusals, triage levels, tool logic
RESOURCES.md                    inventory of the pre-provisioned workshop resources
```

## Status

Deployed and verified end-to-end in `us-west-2` on 2026-09-30: AgentCore Runtime + Memory + Gateway (7 Lambda
tools) + baseline Guardrail, exercised through both the CLI helper and the Streamlit web UI. See
[DEMO.md](DEMO.md) for the scripted walkthrough and [RESOURCES.md](RESOURCES.md) for resource ids.

## Running

```bash
cd HealthCompanion
agentcore dev        # local hot-reload server on :8080 + inspector (uses in-process tools before first deploy)
agentcore deploy     # build + deploy runtime, memory, gateway and Lambda to us-west-2
agentcore invoke --prompt "عندي صداع منذ ثلاثة أيام ورؤيتي مشوشة"
app/HealthCompanionAgent/.venv/Scripts/python -m pytest
```

All resource IDs are read from SSM Parameter Store under `/app/workshop/...` — nothing is hardcoded.
