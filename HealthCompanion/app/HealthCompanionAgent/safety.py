"""Pre-model safety layer.

These checks run on the raw user text *before* anything reaches the model or a
tool, so the three behaviours the track is judged on are deterministic:

1. Emergency symptoms short-circuit to "Call 998 now".
2. Dosing questions are refused with a pointer to a pharmacist or doctor.
3. Diagnosis requests are reframed: no diagnosis, but triage and routing continue.

Everything here is pure Python so it is unit-tested without AWS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

EMERGENCY_NUMBER = "998"  # UAE ambulance
POLICE_NUMBER = "999"

ARABIC_RE = re.compile(r"[؀-ۿ]")

EMERGENCY_PATTERNS = [
    # English
    r"chest (pain|pressure|tight)", r"pressure (in|on) my chest", r"left arm (numb|pain|tingl)",
    r"(can'?t|cannot|difficult(y)?|trouble|struggling to|short(ness)? of) breath",
    r"(face|facial).{0,12}droop", r"slurred speech", r"speech.{0,12}slurred", r"(one[- ]sided|one side).{0,20}(weak|numb)",
    r"(sudden|worst).{0,25}headache", r"headache.{0,20}worst", r"sudden(ly)? .{0,15}(vision|sight|can'?t see)",
    r"(severe|heavy|uncontrolled|won'?t stop) bleed", r"seizure", r"convuls", r"faint(ed|ing)?",
    r"pass(ed)? out", r"unconscious", r"not breathing", r"stiff neck.{0,25}fever",
    r"(swelling|swollen).{0,30}(throat|tongue|lips)", r"anaphyla", r"overdose", r"suicid",
    # Arabic
    r"ألم (في )?(ال)?صدر", r"الم (في )?(ال)?صدر", r"ضغط (في |على )?(ال)?صدر",
    r"(خدر|تنميل) (في )?(ال)?(ذراع|يد|إيد)", r"ضيق (في )?(ال)?تنفس", r"صعوبة (في )?(ال)?تنفس",
    r"(ما|لا) أقدر أتنفس", r"تدلي (في )?(ال)?وجه", r"تلعثم", r"ضعف (في )?(جانب|جهة) واحد",
    r"أسوأ صداع", r"صداع مفاجئ", r"فقدان (مفاجئ )?(ال|لل)?(بصر|رؤية|نظر)", r"(فجأة|مفاجئ).{0,15}(أشوف|ارى|أرى)",
    r"نزيف (شديد|حاد|غزير|ما يوقف)", r"نوبة", r"تشنج", r"إغماء", r"اغماء", r"(أغمي|اغمي) علي", r"فقدان (ال)?وعي",
    r"(لا|ما) يستجيب",
    r"تورم (في )?(ال)?(حلق|لسان|شفايف)", r"جرعة زائدة", r"انتحار",
]

DOSING_PATTERNS = [
    r"how (many|much) (mg|milligrams?|ml|tablets?|pills?|drops?)", r"\bmg\b.{0,30}(take|should)",
    r"(what|which) dos(e|age)", r"dos(e|age) (of|for|should)", r"how often (should|do) i take",
    r"can i (take|double|increase|stop|skip) (my |the )?([\w-]+ )?(dose|medication|medicine|pills?)",
    r"(increase|decrease|double|halve|skip|reduce) (my |the )?([\w-]+ )?dose", r"prescribe",
    r"كم (ملغ|ملليغرام|ملجم|مل|حبة|حبات|قرص|نقطة)", r"(كم|ما هي|شو) (ال)?جرعة", r"جرعة .{0,20}(آخذ|اخذ|أاخذ)",
    r"(أزيد|ازيد|أوقف|اوقف|أضاعف|اضاعف) (ال)?(جرعة|دواء|الدوا)", r"كم مرة (آخذ|اخذ)", r"اوصف لي|وصف لي دواء",
]

DIAGNOSIS_PATTERNS = [
    r"what (disease|illness|condition) do i have", r"what('s| is) (wrong with me|my diagnosis|this)",
    r"diagnos(e|is) me", r"do i have (a |an )?[a-z]", r"is (it|this) (cancer|a stroke|a heart attack|diabetes|covid|meningitis)",
    r"am i having a", r"tell me what i have",
    r"(ما هو|شو|ايش|وش) مرضي", r"(ما|شو|ايش) (ال)?تشخيص", r"شخص(ني| لي| حالتي)", r"هل (عندي|أعاني من|لدي) ",
    r"هل (هذا|هذه|هاي) (سرطان|جلطة|سكتة|نوبة قلبية|سكري)",
]


@dataclass(frozen=True)
class SafetyVerdict:
    kind: str            # "ok" | "emergency" | "dosing" | "diagnosis"
    language: str        # "ar" | "en"
    response: str | None = None   # canned reply when the model must not run
    rewritten_prompt: str | None = None  # prompt to send instead (diagnosis)


def detect_language(text: str, hint: str | None = None) -> str:
    if hint in ("ar", "en"):
        return hint
    return "ar" if ARABIC_RE.search(text or "") else "en"


def _any(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def is_emergency(text: str) -> bool:
    return _any(EMERGENCY_PATTERNS, text or "")


def is_dosing_request(text: str) -> bool:
    return _any(DOSING_PATTERNS, text or "")


def is_diagnosis_request(text: str) -> bool:
    return _any(DIAGNOSIS_PATTERNS, text or "")


def emergency_message(lang: str) -> str:
    if lang == "ar":
        return (
            f"🚨 هذه قد تكون حالة طارئة. اتصل بالرقم {EMERGENCY_NUMBER} (الإسعاف) الآن، أو اطلب من شخص قريب أن يتصل.\n"
            "لا تقد السيارة بنفسك. إن كان هناك ألم في الصدر فاجلس وابقَ هادئاً حتى وصول المساعدة.\n"
            f"في حالة الخطر على الحياة اتصل أيضاً بالرقم {POLICE_NUMBER}.\n"
            "لن أكمل أي خطوات أخرى قبل أن تحصل على مساعدة طارئة."
        )
    return (
        f"🚨 This may be an emergency. Call {EMERGENCY_NUMBER} (ambulance) now, or ask someone near you to call.\n"
        "Do not drive yourself. If you have chest pain, sit down and stay still until help arrives.\n"
        f"For a life-threatening situation you can also call {POLICE_NUMBER}.\n"
        "I will not continue with any other steps until you have emergency help."
    )


def dosing_refusal(lang: str) -> str:
    if lang == "ar":
        return (
            "لا أستطيع تحديد جرعات الأدوية أو تعديلها، لأن الجرعة الصحيحة تعتمد على وزنك وحالتك وأدويتك الأخرى.\n"
            "الأفضل: اسأل الصيدلي (متوفر في أي صيدلية دون موعد) أو طبيبك. إن رغبت، أستطيع مراجعة أدويتك الحالية "
            "بحثاً عن أي تداخلات تستحق ذكرها لهم، أو مساعدتك في إيجاد الطبيب المناسب."
        )
    return (
        "I can't tell you how much of a medication to take or change a dose. The right dose depends on your weight, "
        "your condition, and what else you take.\n"
        "Best next step: ask a pharmacist (available at any pharmacy without an appointment) or your doctor. "
        "If you like, I can review your current medications for anything worth mentioning to them, or help you "
        "find the right doctor."
    )


def diagnosis_refusal(lang: str) -> str:
    if lang == "ar":
        return ("لا أستطيع تشخيص الحالات، فهذا يحتاج فحصاً من طبيب. لكن أستطيع مساعدتك في معرفة مدى استعجال الأمر "
                "والطبيب المناسب. صِف لي الأعراض ومتى بدأت.")
    return ("I can't diagnose — that needs a doctor's examination. What I can do is help you judge how urgent this is "
            "and who the right doctor is. Tell me your symptoms and when they started.")


def treatment_refusal(lang: str) -> str:
    if lang == "ar":
        return ("لا أستطيع اقتراح علاج أو وصفة منزلية؛ هذا قرار الطبيب أو الصيدلي. أستطيع مساعدتك في معرفة درجة "
                "الاستعجال وحجز موعد مع الطبيب المناسب.")
    return ("I can't recommend a treatment or home remedy — that's for your doctor or pharmacist to decide. I can help "
            "you judge the urgency and book the right doctor.")


def guardrail_block_message(lang: str) -> str:
    if lang == "ar":
        return "لا أستطيع المساعدة في هذا الطلب. لنبقَ في ما أستطيع مساعدتك فيه بأمان: الأعراض ودرجة الاستعجال والطبيب المناسب."
    return ("I can't help with that request. Let's keep to what I can safely help with: your symptoms, how urgent they "
            "are, and the right doctor to see.")


# Baseline guardrail topic name → refusal that keeps the user on a safe path.
GUARDRAIL_TOPIC_RESPONSES = {
    "MedicalDiagnosis": diagnosis_refusal,
    "MedicationDosing": dosing_refusal,
    "TreatmentRecommendation": treatment_refusal,
}


def diagnosis_reframe(text: str, lang: str) -> str:
    note = (
        "[Safety note: the user asked for a diagnosis. Do NOT name a disease or condition. Say briefly, in the "
        "user's language, that you can't diagnose, then run the normal flow: triage urgency, check history and "
        "medications, and recommend who to see.]\n"
    )
    return note + ("Symptoms described by the user: " if lang == "en" else "الأعراض التي وصفها المستخدم: ") + text


def assess(text: str, language_hint: str | None = None) -> SafetyVerdict:
    lang = detect_language(text, language_hint)
    if is_emergency(text):
        return SafetyVerdict("emergency", lang, response=emergency_message(lang))
    if is_dosing_request(text):
        return SafetyVerdict("dosing", lang, response=dosing_refusal(lang))
    if is_diagnosis_request(text):
        return SafetyVerdict("diagnosis", lang, rewritten_prompt=diagnosis_reframe(text, lang))
    return SafetyVerdict("ok", lang)
