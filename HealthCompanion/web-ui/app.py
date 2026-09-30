"""
Health Companion — bilingual (English / Arabic), right-to-left aware chat UI.

Based on the Future Vision hackathon shared interface. Signs in with Cognito,
then streams replies from the deployed AgentCore Runtime. The signed-in user's
email prefix is the memory actor_id; the demo patient is chosen in the sidebar.

Run locally:
    pip install streamlit boto3
    streamlit run app.py --server.port 8501
"""

from __future__ import annotations

import json
import os
import uuid

import boto3
import streamlit as st

REGION = os.environ.get("AWS_REGION", "us-west-2")
STACK = os.environ.get("AGENTCORE_STACK", "AgentCore-HealthCompanion-default")

STRINGS = {
    "en": {
        "title": "Health Companion",
        "tagline": "Symptom guidance, the right doctor, and a booked appointment — never a diagnosis.",
        "welcome": "Hello. Tell me what you're feeling, or share a reading like \"my blood pressure was 152 over 94\".",
        "input": "Describe your symptoms",
        "signin": "Sign in",
        "email": "Email",
        "password": "Password",
        "new_password": "Choose a new password",
        "set_password": "Set password",
        "logout": "Sign out",
        "patient": "Patient",
        "emergency": "Emergency? Call 998",
        "thinking": "Checking the guidelines…",
    },
    "ar": {
        "title": "رفيق الصحة",
        "tagline": "إرشاد حول الأعراض، والطبيب المناسب، وحجز الموعد — دون أي تشخيص.",
        "welcome": "مرحباً. صِف لي ما تشعر به، أو شارك قراءة مثل «ضغطي كان 152 على 94».",
        "input": "صِف أعراضك",
        "signin": "تسجيل الدخول",
        "email": "البريد الإلكتروني",
        "password": "كلمة المرور",
        "new_password": "اختر كلمة مرور جديدة",
        "set_password": "حفظ كلمة المرور",
        "logout": "تسجيل الخروج",
        "patient": "المريض",
        "emergency": "حالة طارئة؟ اتصل بالرقم 998",
        "thinking": "جارٍ مراجعة الإرشادات…",
    },
}

PATIENTS = {
    "PAT-01": "Aisha Rahman — عائشة رحمن (hypertension, PlanA, ar)",
    "PAT-02": "David Chen (type-2 diabetes, PlanB, en)",
    "PAT-03": "Omar Haddad — عمر حداد (no history, PlanA, ar)",
}


@st.cache_data(show_spinner=False)
def runtime_arn_from_stack() -> str:
    try:
        cfn = boto3.client("cloudformation", region_name=REGION)
        for o in cfn.describe_stacks(StackName=STACK)["Stacks"][0]["Outputs"]:
            if "RuntimeArn" in o["OutputKey"]:
                return o["OutputValue"]
    except Exception:  # noqa: BLE001 - fall back to config/env
        pass
    return ""


def load_config() -> dict:
    """Cognito + runtime configuration from cognito_config.json, env, or the deployed stack."""
    config = {}
    path = os.environ.get("COGNITO_CONFIG", os.path.join(os.path.dirname(__file__), "cognito_config.json"))
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            config = json.load(handle)
    config.setdefault("client_id", os.environ.get("COGNITO_CLIENT_ID", ""))
    config["runtime_arn"] = config.get("runtime_arn") or os.environ.get("AGENT_RUNTIME_ARN") or runtime_arn_from_stack()
    return config


def apply_direction(lang: str) -> None:
    if lang == "ar":
        st.markdown("<style>.stApp { direction: rtl; text-align: right; }</style>", unsafe_allow_html=True)


def _finish_sign_in(resp: dict, email: str) -> None:
    st.session_state["token"] = resp.get("AuthenticationResult", {}).get("IdToken")
    st.session_state["actor_id"] = email.split("@")[0]
    st.session_state["session_id"] = str(uuid.uuid4())  # ≥33 chars as the runtime requires
    st.session_state.pop("challenge", None)


def sign_in(config: dict, email: str, password: str) -> bool:
    client = boto3.client("cognito-idp", region_name=REGION)
    try:
        resp = client.initiate_auth(
            ClientId=config["client_id"],
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": email, "PASSWORD": password},
        )
    except Exception as exc:  # noqa: BLE001
        st.error(str(exc))
        return False
    if resp.get("ChallengeName") == "NEW_PASSWORD_REQUIRED":
        st.session_state["challenge"] = {"session": resp["Session"], "email": email}
        return False
    _finish_sign_in(resp, email)
    return True


def complete_new_password(config: dict, new_password: str) -> bool:
    ch = st.session_state["challenge"]
    client = boto3.client("cognito-idp", region_name=REGION)
    try:
        resp = client.respond_to_auth_challenge(
            ClientId=config["client_id"],
            ChallengeName="NEW_PASSWORD_REQUIRED",
            Session=ch["session"],
            ChallengeResponses={"USERNAME": ch["email"], "NEW_PASSWORD": new_password},
        )
    except Exception as exc:  # noqa: BLE001
        st.error(str(exc))
        return False
    _finish_sign_in(resp, ch["email"])
    return True


def stream_agent(config: dict, prompt: str, lang: str, patient_id: str):
    """Yield text chunks from the deployed AgentCore Runtime (SSE)."""
    client = boto3.client("bedrock-agentcore", region_name=REGION)
    payload = {"prompt": prompt, "language": lang, "patient_id": patient_id,
               "actor_id": st.session_state["actor_id"]}
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=config["runtime_arn"],
        runtimeSessionId=st.session_state["session_id"],
        payload=json.dumps(payload).encode("utf-8"),
        contentType="application/json",
        accept="text/event-stream",
    )
    for raw in resp["response"].iter_lines():
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        delta = event.get("event", {}).get("contentBlockDelta", {}).get("delta", {})
        if "text" in delta:
            yield delta["text"]


def main() -> None:
    st.set_page_config(page_title="Health Companion · رفيق الصحة", page_icon="🩺", layout="centered")
    config = load_config()
    lang = st.sidebar.selectbox("Language / اللغة", ["en", "ar"], format_func=lambda v: {"en": "English", "ar": "العربية"}[v])
    apply_direction(lang)
    text = STRINGS[lang]

    st.title("🩺 " + text["title"])
    st.caption(text["tagline"])
    st.sidebar.error("🚨 " + text["emergency"])

    if "token" not in st.session_state:
        if "challenge" in st.session_state:
            st.subheader(text["new_password"])
            new_pw = st.text_input(text["new_password"], type="password")
            if st.button(text["set_password"]) and new_pw and complete_new_password(config, new_pw):
                st.rerun()
            return
        st.subheader(text["signin"])
        email = st.text_input(text["email"])
        password = st.text_input(text["password"], type="password")
        if st.button(text["signin"]) and email and password:
            if sign_in(config, email, password):
                st.rerun()
            elif "challenge" in st.session_state:
                st.rerun()
        return

    patient_id = st.sidebar.selectbox(text["patient"], list(PATIENTS), format_func=lambda p: f"{p} · {PATIENTS[p]}")
    st.sidebar.caption(f"actor: {st.session_state['actor_id']} · session: {st.session_state['session_id'][:8]}…")
    if st.sidebar.button(text["logout"]):
        st.session_state.clear()
        st.rerun()
    if not config.get("runtime_arn"):
        st.warning("No runtime ARN found — deploy the stack or set AGENT_RUNTIME_ARN.")

    if "history" not in st.session_state:
        st.session_state["history"] = [("assistant", text["welcome"])]
    for role, message in st.session_state["history"]:
        with st.chat_message(role):
            st.markdown(message)

    prompt = st.chat_input(text["input"])
    if prompt:
        st.session_state["history"].append(("user", prompt))
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner(text["thinking"]):
                reply = st.write_stream(stream_agent(config, prompt, lang, patient_id))
        st.session_state["history"].append(("assistant", reply if isinstance(reply, str) else "".join(reply)))


if __name__ == "__main__":
    main()
