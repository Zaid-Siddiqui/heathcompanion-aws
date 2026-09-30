# HealthCompanionAgent

Strands agent served by AgentCore Runtime (`POST /invocations`, `GET /ping` on 8080).

| File | Role |
|---|---|
| `main.py` | entrypoint: payload validation → safety layer → Strands agent with memory, guardrail and gateway tools |
| `safety.py` | pure pre-model checks: emergency short-circuit (998), dosing refusal, diagnosis reframe — bilingual |
| `gateway.py` | SigV4-signed MCP client for the IAM-auth AgentCore Gateway; in-process fallback for local dev |
| `runtime-policy.json` | extra IAM for the runtime role (apply guardrail, invoke gateway) |

Payload: `{"prompt": "...", "patient_id": "PAT-01", "language": "ar"}` — only `prompt` is required.

Environment (injected by the CDK stack at deploy; set in `agentcore/.env.local` for `agentcore dev`):
`MEMORY_HEALTHMEMORY_ID`, `AGENTCORE_GATEWAY_HEALTH_TOOLS_URL`, `GUARDRAIL_ID`, `GUARDRAIL_VERSION`, `MODEL_ID`.
