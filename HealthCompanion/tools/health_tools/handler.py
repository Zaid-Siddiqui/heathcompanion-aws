"""Health Companion tools, exposed through AgentCore Gateway as one Lambda.

The Gateway passes the tool's input as the Lambda event and the tool name in
``context.client_context.custom["bedrockAgentCoreToolName"]`` (formatted as
``<target>___<tool>``). Every resource id is read from SSM at cold start.

None of these tools diagnose, dose, or recommend treatment. They gauge urgency,
surface facts from the record, flag things for a professional to confirm, and
route to the right provider.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION", "us-west-2")
SSM_PREFIX = "/app/workshop/health-companion"

_ssm = boto3.client("ssm", region_name=REGION)
_ddb = boto3.resource("dynamodb", region_name=REGION)
_kb = boto3.client("bedrock-agent-runtime", region_name=REGION)
_s3 = boto3.client("s3", region_name=REGION)
_translate = boto3.client("translate", region_name=REGION)

EMERGENCY_NUMBER = "998"
POLICE_NUMBER = "999"

# Red flags that force "emergency" regardless of anything else. Kept in both
# languages so the check works on raw user text before any translation.
EMERGENCY_PATTERNS = [
    r"chest (pain|pressure|tight)", r"left arm (numb|pain)", r"jaw pain",
    r"(can'?t|cannot|difficult(y)?|trouble|short(ness)? of) breath",
    r"(face|facial) droop", r"slurred speech", r"one[- ]sided (weak|numb)",
    r"(sudden|worst).{0,20}headache", r"sudden .{0,15}(vision|sight) loss",
    r"(severe|heavy|uncontrolled) bleeding", r"seizure", r"faint(ed|ing)?",
    r"unconscious", r"stiff neck.{0,20}fever", r"(swelling|swollen).{0,30}(throat|tongue)",
    r"ألم (في )?الصدر", r"ضغط (في )?الصدر", r"خدر (في )?(الذراع|اليد) (الأيسر|اليسرى)",
    r"ضيق (في )?التنفس", r"صعوبة (في )?التنفس", r"تدلي (في )?الوجه", r"تلعثم",
    r"ضعف (في )?جانب واحد", r"أسوأ صداع", r"صداع مفاجئ", r"فقدان (مفاجئ )?(ال)?(بصر|رؤية)",
    r"نزيف (شديد|حاد|غزير)", r"نوبة (صرع)?", r"إغماء", r"فقدان الوعي", r"تورم (في )?(الحلق|اللسان)",
]

URGENT_PATTERNS = [
    r"high fever", r"fever.{0,30}(days|won'?t|not going)", r"infect", r"pus",
    r"vision (change|blur)", r"blurr?ed", r"worse(ning)? (over|for|since)", r"exertion",
    r"حمى (شديدة|مرتفعة)", r"التهاب", r"صديد", r"(تشوش|تغير) (في )?الرؤية", r"يزداد سوء",
]

SELF_CARE_PATTERNS = [
    r"\bmild\b", r"runny nose", r"sneez", r"slight", r"minor", r"common cold",
    r"خفيف", r"بسيط", r"زكام", r"رشح", r"عطس",
]

SPECIALTY_RULES = [
    (r"chest|palpitat|heart|صدر|خفقان|قلب", "cardiology"),
    (r"headache|migraine|numb|weak|dizz|seizure|صداع|شقيقة|خدر|ضعف|دوخة|نوبة", "neurology"),
    (r"eye|vision|sight|blur|عين|رؤية|بصر|تشوش", "ophthalmology"),
]

ARABIC_RE = re.compile(r"[؀-ۿ]")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=None)
def param(name: str) -> str:
    return _ssm.get_parameter(Name=f"{SSM_PREFIX}/{name}")["Parameter"]["Value"]


def detect_language(text: str, hint: str | None = None) -> str:
    if hint in ("ar", "en"):
        return hint
    return "ar" if ARABIC_RE.search(text or "") else "en"


def _matches(patterns: list[str], text: str) -> list[str]:
    return [p for p in patterns if re.search(p, text, re.IGNORECASE)]


def _split_csv(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip() and v.strip().lower() != "none"]


def retrieve_guidelines(query: str, k: int = 3) -> list[dict[str, str]]:
    resp = _kb.retrieve(
        knowledgeBaseId=param("knowledge-base-id"),
        retrievalQuery={"text": query},
        retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": k}},
    )
    out = []
    for r in resp.get("retrievalResults", []):
        uri = r.get("location", {}).get("s3Location", {}).get("uri", "")
        out.append({
            "source": uri.rsplit("/", 1)[-1] or "guidelines",
            "score": round(r.get("score", 0), 3),
            "text": r.get("content", {}).get("text", ""),
        })
    return out


def translate(text: str, target: str) -> str:
    source = "ar" if target == "en" else "en"
    try:
        return _translate.translate_text(
            Text=text, SourceLanguageCode=source, TargetLanguageCode=target
        )["TranslatedText"]
    except Exception as exc:  # pragma: no cover - best effort
        log.warning("translate failed: %s", exc)
        return text


def get_patient(patient_id: str) -> dict[str, Any] | None:
    table = _ddb.Table(param("patients-table"))
    return table.get_item(Key={"patient_id": patient_id}).get("Item")


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #

def triage_symptoms(symptoms: str, patient_id: str | None = None, language: str | None = None) -> dict:
    lang = detect_language(symptoms, language)
    text = symptoms.lower()

    emergency_hits = _matches(EMERGENCY_PATTERNS, text)
    if emergency_hits:
        return {
            "urgency": "emergency",
            "action": f"Call {EMERGENCY_NUMBER} now" if lang == "en" else f"اتصل بالرقم {EMERGENCY_NUMBER} فوراً",
            "emergency_number": EMERGENCY_NUMBER,
            "reason": "Red-flag symptom present; guidelines require emergency services rather than a booking.",
            "matched": emergency_hits,
            "specialty": None,
            "citation": {"source": "triage-guidelines.md", "section": "Red-flag symptoms that force Emergency"},
            "language": lang,
        }

    history = ""
    if patient_id:
        p = get_patient(patient_id)
        history = (p or {}).get("history", "") or ""

    urgency = "routine"
    modifiers: list[str] = []
    if _matches(URGENT_PATTERNS, text):
        urgency = "urgent"
    elif _matches(SELF_CARE_PATTERNS, text) and not _matches(URGENT_PATTERNS, text):
        urgency = "self-care"

    # History that raises urgency (from the guidelines).
    h = history.lower()
    if "hypertension" in h or "heart" in h:
        if re.search(r"chest|palpitat|صدر|خفقان", text):
            urgency = "urgent" if urgency in ("routine", "self-care") else urgency
            modifiers.append("hypertension raises the urgency of chest symptoms")
    if "diabet" in h:
        if re.search(r"wound|cut|infect|heal|جرح|التهاب", text):
            urgency = "urgent" if urgency in ("routine", "self-care") else urgency
            modifiers.append("diabetes raises the urgency of infections and slow-healing wounds")

    specialty = "general"
    for pattern, spec in SPECIALTY_RULES:
        if re.search(pattern, text):
            specialty = spec
            break

    query_text = symptoms if lang == "en" else translate(symptoms, "en")
    passages = retrieve_guidelines(f"urgency and specialty for: {query_text}")
    citation = passages[0] if passages else {"source": "triage-guidelines.md", "text": ""}

    return {
        "urgency": urgency,
        "specialty": specialty,
        "history_modifiers": modifiers,
        "citation": citation,
        "language": lang,
        "note": "Urgency is guidance, not a diagnosis. When unsure, the more cautious level applies.",
    }


def get_patient_history(patient_id: str) -> dict:
    p = get_patient(patient_id)
    if not p:
        return {"found": False, "patient_id": patient_id}
    return {
        "found": True,
        "patient_id": patient_id,
        "name": p.get("name"),
        "language": p.get("language", "en"),
        "conditions": _split_csv(p.get("history")),
        "medications": _split_csv(p.get("medications")),
        "allergies": _split_csv(p.get("allergies")),
        "insurance": p.get("insurance"),
    }


INTERACTION_RULES = [
    # (set of triggers, message)
    ({"amlodipine", "lisinopril"}, "Blood-pressure medicines combined with other drugs can lower blood pressure too far."),
    ({"metformin"}, "Metformin needs professional review before any imaging scan that uses contrast dye (kidney-related risk)."),
    ({"warfarin"}, "Blood thinners such as warfarin with common pain relievers (e.g. ibuprofen) can raise bleeding risk."),
]
SEDATIVES = {"antihistamine", "diphenhydramine", "zolpidem", "sleep aid", "opioid", "codeine", "tramadol"}
NSAIDS = {"ibuprofen", "naproxen", "diclofenac", "aspirin"}


def check_medications(patient_id: str, symptoms: str, additional_medications: str | None = None) -> dict:
    p = get_patient(patient_id) or {}
    on_record = _split_csv(p.get("medications"))
    mentioned = _split_csv(additional_medications)
    meds = {m.lower() for m in on_record + mentioned}
    allergies = _split_csv(p.get("allergies"))
    text = (symptoms or "").lower()

    flags: list[str] = []
    for triggers, msg in INTERACTION_RULES:
        if meds & triggers:
            flags.append(msg)
    if len(meds & SEDATIVES) >= 2:
        flags.append("Multiple sedating medicines together increase drowsiness and fall risk.")
    if "warfarin" in meds and meds & NSAIDS:
        flags.append("Warfarin plus an NSAID pain reliever is on the serious-interaction list.")
    if re.search(r"dizz|faint|light ?headed|دوخة|إغماء", text) and meds & {"amlodipine", "lisinopril"}:
        flags.append("Dizziness while on a blood-pressure medicine is worth flagging to the prescriber.")
    for allergy in allergies:
        if allergy.lower() in meds:
            flags.append(f"Recorded allergy to {allergy} conflicts with a listed medication.")

    passages = retrieve_guidelines("medication interaction flags " + ", ".join(sorted(meds)), k=1)
    return {
        "patient_id": patient_id,
        "medications_reviewed": sorted(meds),
        "allergies": allergies,
        "flags": flags,
        "raise_urgency": any("serious" in f for f in flags),
        "disclaimer": "Flags are for a pharmacist or doctor to confirm. This is not advice to start, stop, or dose any medication.",
        "citation": passages[0] if passages else {"source": "medication-and-referral.md"},
    }


def find_specialist(specialty: str, insurance: str | None = None, language: str | None = None, location: str | None = None) -> dict:
    table = _ddb.Table(param("providers-table"))
    providers = table.scan().get("Items", [])
    want = (specialty or "general").lower().strip()

    def covers(p: dict) -> bool:
        return bool(insurance) and insurance in _split_csv(p.get("insurance"))

    matches = [p for p in providers if p.get("specialty", "").lower() == want]
    fallback = False
    if not matches:
        matches = [p for p in providers if p.get("specialty", "").lower() == "general"]
        fallback = True

    matches.sort(key=lambda p: (not covers(p), p.get("next_available", "9999-99-99"),
                                0 if location and location.lower() in p.get("location", "").lower() else 1))

    results = []
    for p in matches:
        reasons = []
        if covers(p):
            reasons.append(f"covered by {insurance}")
        elif insurance:
            reasons.append(f"not covered by {insurance}")
        reasons.append(f"next available {p.get('next_available')}")
        results.append({
            "provider_id": p.get("provider_id"),
            "name": p.get("name"),
            "specialty": p.get("specialty"),
            "location": p.get("location"),
            "insurance": _split_csv(p.get("insurance")),
            "next_available": p.get("next_available"),
            "why": ", ".join(reasons),
        })

    return {
        "requested_specialty": want,
        "fell_back_to_general": fallback,
        "language_note": (f"Provider language preference '{language}' noted; confirm with the clinic when booking."
                          if language else None),
        "providers": results,
    }


def create_visit_summary(patient_id: str, symptoms: str, urgency: str, specialty: str | None = None,
                         interaction_flags: str | None = None, language: str | None = None) -> dict:
    p = get_patient(patient_id) or {}
    lang = detect_language(symptoms, language)
    symptoms_en = symptoms if lang == "en" else translate(symptoms, "en")
    symptoms_ar = symptoms if lang == "ar" else translate(symptoms, "ar")
    now = datetime.now(timezone.utc)

    def block(title, concern, history, meds, allergies, flags_label, flags, urgency_label, route_label):
        return "\n".join([
            f"## {title}",
            f"- {concern}: {symptoms_en if title.startswith('Visit') else symptoms_ar}",
            f"- {history}: {p.get('history', 'none')}",
            f"- {meds}: {p.get('medications', 'none')}",
            f"- {allergies}: {p.get('allergies', 'none')}",
            f"- {urgency_label}: {urgency}",
            f"- {route_label}: {specialty or 'general practitioner'}",
            f"- {flags_label}: {interaction_flags or '—'}",
            "",
        ])

    body = "\n".join([
        f"# Visit summary / ملخص الزيارة — {p.get('name', patient_id)} ({patient_id})",
        f"Generated {now.isoformat(timespec='seconds')} by Health Companion. No diagnosis or treatment is included; "
        "flags are for the clinician or pharmacist to confirm.",
        "",
        block("Visit summary (English)", "Main concern", "Relevant history", "Current medications",
              "Known allergies", "Interaction flags (for your doctor or pharmacist to confirm)", interaction_flags,
              "Urgency (triage guidance)", "Suggested routing"),
        block("ملخص الزيارة (العربية)", "الشكوى الرئيسية", "التاريخ المرضي", "الأدوية الحالية",
              "الحساسية المعروفة", "تنبيهات التداخل الدوائي (للتأكيد من الطبيب أو الصيدلي)", interaction_flags,
              "درجة الاستعجال", "التحويل المقترح"),
    ])

    bucket = os.environ.get("SUMMARIES_BUCKET") or _default_summaries_bucket()
    key = f"summaries/{patient_id}/{now.strftime('%Y%m%dT%H%M%SZ')}.md"
    _s3.put_object(Bucket=bucket, Key=key, Body=body.encode("utf-8"), ContentType="text/markdown; charset=utf-8")
    return {"s3_uri": f"s3://{bucket}/{key}", "summary": body, "language": lang}


@lru_cache(maxsize=1)
def _default_summaries_bucket() -> str:
    # The workshop foundation publishes no summaries bucket, so we reuse the
    # health KB bucket under a prefix the KB data source does not index.
    sts = boto3.client("sts", region_name=REGION)
    account = sts.get_caller_identity()["Account"]
    return f"workshop-health-kb-{account}-{REGION}"


TOOLS = {
    "triage_symptoms": triage_symptoms,
    "get_patient_history": get_patient_history,
    "check_medications": check_medications,
    "find_specialist": find_specialist,
    "create_visit_summary": create_visit_summary,
}


# --------------------------------------------------------------------------- #
# Lambda entry
# --------------------------------------------------------------------------- #

def _tool_name(context) -> str:
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    full = custom.get("bedrockAgentCoreToolName", "")
    return full.split("___", 1)[-1]


def handler(event, context):
    name = _tool_name(context) or (event or {}).get("tool_name", "")
    fn = TOOLS.get(name)
    if fn is None:
        return {"error": f"unknown tool '{name}'", "available": sorted(TOOLS)}
    args = {k: v for k, v in (event or {}).items() if k != "tool_name"}
    log.info("tool=%s args=%s", name, json.dumps(args, ensure_ascii=False))
    try:
        return fn(**args)
    except TypeError as exc:
        return {"error": f"bad arguments for {name}: {exc}"}
    except Exception as exc:  # surface, don't hide
        log.exception("tool %s failed", name)
        return {"error": f"{type(exc).__name__}: {exc}"}
