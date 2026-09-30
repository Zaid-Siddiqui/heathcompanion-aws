# Health Companion — Web UI

Two ways to run the interface, for two different moments:

| UI | File(s) | Talks to | Needs | Best for |
| --- | --- | --- | --- | --- |
| **Local dev UI** (this guide) | `index.html` + `server.py` | local `agentcore dev` on :8080 | nothing (stdlib only) | fast iteration, offline demo, MVP |
| **Deployed demo UI** | `app.py` (Streamlit) | the deployed AgentCore runtime | `pip install streamlit boto3`, Cognito login | the hosted/authenticated demo |

Both are bilingual (Arabic / English) and stream the same agent responses.
This README covers the **local dev UI**; see the header of `app.py` for the
Streamlit one.

---

## Local dev UI

A clean, cinematic, self-contained page (`index.html`) served by a tiny
stdlib-only Python proxy (`server.py`) that streams the agent's responses to the
browser. No login, no build step, no dependencies.

## Run it

1. Start the agent (from the project root):

   ```bash
   agentcore dev
   ```

   This serves the runtime on `http://127.0.0.1:8080`.

2. In a second terminal, start the UI:

   ```bash
   python web-ui/server.py
   ```

3. Open <http://127.0.0.1:3000>.

The status dot (top right) turns green when it can reach the agent, amber/red
when it can't.

## Options

```bash
python web-ui/server.py --port 4000                 # change the UI port
python web-ui/server.py --agent http://127.0.0.1:8080   # point at a different runtime
```

## How it works

- `GET /` and static assets → served from this directory.
- `GET /api/health` → cheap TCP check of the agent (no model call).
- `POST /api/chat` → forwards `{prompt, patient_id?, language?, actor_id?}` to
  the agent's `/invocations` endpoint and streams the SSE response back. Serving
  the page and the API from the same origin means no CORS configuration.

## Notes

- No build step, no `npm install`, no external runtime dependencies.
- Each browser tab keeps a stable session id so AgentCore memory carries across
  turns within that tab.
- The UI never diagnoses or doses — it mirrors the agent's safety behaviour and
  highlights emergency (998) responses.
