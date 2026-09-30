import pytest

from safety import assess, detect_language, is_diagnosis_request, is_dosing_request, is_emergency


@pytest.mark.parametrize("text", [
    "I have chest pain and my left arm is numb",
    "sudden worst headache of my life",
    "my father's face is drooping and his speech is slurred",
    "I can't breathe properly",
    "heavy bleeding that won't stop",
    "عندي ألم في الصدر وخدر في الذراع اليسرى",
    "ضيق في التنفس شديد",
    "فقدان مفاجئ للبصر",
    "أغمي عليه ولا يستجيب",
])
def test_emergency_detected(text):
    assert is_emergency(text)
    v = assess(text)
    assert v.kind == "emergency"
    assert "998" in v.response


@pytest.mark.parametrize("text", [
    "I've had this headache for 3 days and my vision gets blurry. Is it serious? Which doctor?",
    "mild runny nose since yesterday",
    "عندي صداع منذ ثلاثة أيام ورؤيتي مشوشة",
])
def test_non_emergency_passes_through(text):
    v = assess(text)
    assert v.kind in ("ok", "diagnosis")
    assert not is_emergency(text)


@pytest.mark.parametrize("text", [
    "How many mg of paracetamol should I take?",
    "what dose of amlodipine is right for me",
    "can I double my metformin dose?",
    "كم ملغ من الباراسيتامول آخذ؟",
    "كم الجرعة المناسبة لي من الميتفورمين",
])
def test_dosing_refused(text):
    assert is_dosing_request(text)
    v = assess(text)
    assert v.kind == "dosing"
    assert "pharmacist" in v.response.lower() or "الصيدلي" in v.response
    assert "mg" not in v.response.lower()


@pytest.mark.parametrize("text", [
    "What disease do I have?",
    "Do I have meningitis?",
    "Is this a stroke?",
    "ما هو مرضي؟",
    "هل عندي سكري؟",
])
def test_diagnosis_reframed_not_answered(text):
    assert is_diagnosis_request(text)
    v = assess(text)
    assert v.kind == "diagnosis"
    assert v.response is None
    assert "Do NOT name a disease" in v.rewritten_prompt
    assert text in v.rewritten_prompt


def test_emergency_beats_dosing_and_diagnosis():
    v = assess("Is this a heart attack? I have chest pain, how many mg of aspirin should I take?")
    assert v.kind == "emergency"


def test_language_detection():
    assert detect_language("hello") == "en"
    assert detect_language("مرحبا") == "ar"
    assert detect_language("hello", hint="ar") == "ar"
    assert assess("عندي ألم في الصدر").response.startswith("🚨 هذه")
