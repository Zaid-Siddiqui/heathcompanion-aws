# Health Companion — environment & shared-foundation inventory

Captured 2026-09-30 from the workshop account (`254266883591`, `us-west-2`).
All IDs below are **already deployed** by `sharedfoundationstack` + `alltracksstack`.
Code must read them from SSM at runtime — never hardcode ARNs.

## Local toolchain

| Tool | Version | Notes |
|---|---|---|
| Python | 3.12.10 | |
| Node | 22.20.0 | |
| `@aws/agentcore` CLI | 0.31.0 | `npm i -g @aws/agentcore` |
| `uv` | 0.12.21 | `%APPDATA%\Python\Python312\Scripts` added to user PATH (new terminals only) |
| AWS CLI | 2.32.11 | credentials in profile **`workshop`**; `AWS_PROFILE=workshop`, `AWS_DEFAULT_REGION=us-west-2` set as user env vars |
| Kiro | installed | |

Workshop credentials are temporary — re-run "Get AWS CLI credentials" from the event page if you see auth errors.

## SSM parameters (Health Companion + shared)

| Parameter | Value |
|---|---|
| `/app/workshop/health-companion/knowledge-base-id` | `7SKKVEADTE` |
| `/app/workshop/health-companion/data-source-id` | `G35JUEVWLT` |
| `/app/workshop/health-companion/patients-table` | `workshop-health-patients` |
| `/app/workshop/health-companion/providers-table` | `workshop-health-providers` |
| `/app/workshop/guardrails/guardrail-id` | `aj1aucep040k` (version `DRAFT`) |
| `/app/workshop/lambda/execution-role-arn` | `arn:aws:iam::254266883591:role/workshop-lambda-role` |
| `/app/workshop/dynamodb/table-prefix` | `workshop-` |

There is **no SSM param for a summaries bucket** — `create_visit_summary` needs a bucket. Options: write to `workshop-health-kb-…` under a `summaries/` prefix (lambda role already has `s3:PutObject` on `workshop-*`), or add a bucket via the CDK stack and publish its own SSM param.

## DynamoDB

### `workshop-health-patients` — PK `patient_id` (S), no GSIs, 3 items

| patient_id | name | language | history | medications | allergies | insurance |
|---|---|---|---|---|---|---|
| PAT-01 | Aisha Rahman | ar | hypertension | amlodipine | none | PlanA |
| PAT-02 | David Chen | en | type-2 diabetes | metformin | penicillin | PlanB |
| PAT-03 | Omar Haddad | ar | none | none | none | PlanA |

All attributes are plain strings (comma-separated where multi-valued).

### `workshop-health-providers` — PK `provider_id` (S), no GSIs, 4 items

| provider_id | name | specialty | location | insurance | next_available |
|---|---|---|---|---|---|
| PRV-01 | Dr. Nadia Farouk | neurology | Central Clinic | PlanA,PlanB | 2026-08-25 |
| PRV-02 | Dr. John Reeves | cardiology | Riverside Medical | PlanA | 2026-08-26 |
| PRV-03 | Dr. Salma Idris | ophthalmology | Vision Center | PlanB | 2026-08-24 |
| PRV-04 | Dr. Mark Liu | general | Community Health | PlanA,PlanB | 2026-08-24 |

No `language` attribute on providers — `find_specialist`'s language filter must be soft (or we seed it).
Only 4 specialties: neurology, cardiology, ophthalmology, general. Fall back to `general` for anything else.

## Knowledge base `workshop-health-kb` (`7SKKVEADTE`)

- S3 Vectors store, Titan Embed v2 (1024-d); ingestion COMPLETE (2 docs).
- Source bucket `s3://workshop-health-kb-254266883591-us-west-2/`
  - `triage-guidelines.md` — urgency levels (Emergency / Urgent / Routine), red-flag list, symptom→specialty table, history & age modifiers
  - `medication-and-referral.md` — interaction flags (amlodipine + BP drugs, metformin + contrast, sedatives, warfarin + NSAIDs, same-class doubling), referral ordering (insurance → earliest → proximity), visit-summary contents
- Local copies: scratchpad `kb/` folder.
- Verified `bedrock-agent-runtime retrieve` works (score ≈0.69 for "chest pain and left arm numbness").
- Note: the KB defines three levels; the spec's fourth level `self-care` is our addition, mapped to the low end of Routine.

## Baseline guardrail `workshop-baseline-guardrail` (`aj1aucep040k`)

Topic DENY: `MedicalDiagnosis`, `MedicationDosing`, `TreatmentRecommendation`.
Content filters HIGH on violence/hate/sexual/misconduct/prompt-attack; profanity list.
Blocked messaging: "I cannot help with that request…". Our agent should refuse *before* the guardrail fires so the user gets a helpful redirect (pharmacist / doctor / 998) instead of the generic block message.

## Lambda execution role `workshop-lambda-role`

Grants: DynamoDB CRUD on `workshop-*`, `bedrock:Retrieve|RetrieveAndGenerate|InvokeModel` on `*`, SSM read under `/app/workshop/*`, S3 get/put/list on `workshop-*`, plus `translate:TranslateText`, `sns:Publish`, geo, textract. Sufficient for all five tools.

## Bedrock models (cross-region profiles available)

Recommended: `global.anthropic.claude-sonnet-4-6` (fast, strong Arabic) for the agent; `us.anthropic.claude-haiku-4-5-20251001-v1:0` if we need a cheap classifier. Also present: `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-fable-5-1`.

## Not yet created (we create these)

- AgentCore Memory (none exist) — `agentcore add` → Memory; strategies: user-preference (language) + semantic (symptom history)
- AgentCore Gateway (none exist) — `agentcore add` → Gateway with the five Lambda tools
- AgentCore Runtime + project scaffold — `agentcore create`

## Tool ↔ resource mapping

| Tool | Reads | Writes |
|---|---|---|
| `triage_symptoms` | KB `7SKKVEADTE` (Retrieve) | — |
| `get_patient_history` | `workshop-health-patients` by `patient_id` | — |
| `check_medications` | patient `medications` + KB medication doc | — |
| `find_specialist` | `workshop-health-providers` (scan + filter specialty/insurance) | — |
| `book_appointment` | providers + patients | `UpdateItem` appends to `appointments` list on the patient item |
| `create_visit_summary` | all of the above | S3 `summaries/<patient_id>/<ts>.md` in the health KB bucket |

## AgentCore CLI 0.31 quirks learned

- `agentcore create/add` are fully scriptable with flags; in PowerShell quote comma lists (`--strategies "A,B"`).
- The CLI's `--type` list has no plain Lambda target; write `targetType: "lambda"` + `compute.host: "Lambda"` by hand.
  `implementation.handler` is passed verbatim as the Lambda `Handler` → use `handler.handler`, not `handler.py`.
- Each Lambda tool dir needs a `pyproject.toml` (packaged with `uv`; `uv` must be on PATH for synth).
- Deployment targets must exist in `aws-targets.json` before synth.
- To synth outside the CLI: `cd agentcore/cdk && npm run build && CDK_OUTDIR=cdk.out node dist/bin/cdk.js`.
- Runtime env vars injected by CDK: `MEMORY_<NAME>_ID`, `AGENTCORE_GATEWAY_<NAME>_URL`, `AGENTCORE_GATEWAY_<NAME>_AUTH_TYPE`.
