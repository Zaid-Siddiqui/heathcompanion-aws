"""Tool-level tests. AWS calls are stubbed; the logic under test is local."""

import handler as h


PATIENTS = {
    "PAT-01": {"patient_id": "PAT-01", "name": "Aisha Rahman", "language": "ar", "history": "hypertension",
               "medications": "amlodipine", "allergies": "none", "insurance": "PlanA"},
    "PAT-02": {"patient_id": "PAT-02", "name": "David Chen", "language": "en", "history": "type-2 diabetes",
               "medications": "metformin", "allergies": "penicillin", "insurance": "PlanB"},
}
PROVIDERS = [
    {"provider_id": "PRV-01", "name": "Dr. Nadia Farouk", "specialty": "neurology", "location": "Central Clinic",
     "insurance": "PlanA,PlanB", "next_available": "2026-08-25"},
    {"provider_id": "PRV-02", "name": "Dr. John Reeves", "specialty": "cardiology", "location": "Riverside Medical",
     "insurance": "PlanA", "next_available": "2026-08-26"},
    {"provider_id": "PRV-04", "name": "Dr. Mark Liu", "specialty": "general", "location": "Community Health",
     "insurance": "PlanA,PlanB", "next_available": "2026-08-24"},
]


class FakeTable:
    def __init__(self, items):
        self.items = items
        self.updates = []

    def get_item(self, Key):
        pid = list(Key.values())[0]
        row = self.items.get(pid) if isinstance(self.items, dict) else next((i for i in self.items if i["provider_id"] == pid), None)
        return {"Item": row} if row else {}

    def scan(self):
        return {"Items": list(self.items)}

    def update_item(self, **kwargs):
        self.updates.append(kwargs)


class FakeDDB:
    def __init__(self):
        self.tables = {"workshop-health-patients": FakeTable(PATIENTS), "workshop-health-providers": FakeTable(PROVIDERS)}

    def Table(self, name):
        return self.tables[name]


def _stub(monkeypatch):
    ddb = FakeDDB()
    monkeypatch.setattr(h, "_ddb", ddb)
    monkeypatch.setattr(h, "param", lambda name: {"patients-table": "workshop-health-patients",
                                                   "providers-table": "workshop-health-providers",
                                                   "knowledge-base-id": "KB"}[name])
    monkeypatch.setattr(h, "retrieve_guidelines", lambda q, k=3: [{"source": "triage-guidelines.md", "score": 0.7, "text": "stub"}])
    monkeypatch.setattr(h, "translate", lambda text, target: text)
    return ddb


# ---- triage -----------------------------------------------------------------

def test_triage_emergency_short_circuits_before_any_lookup(monkeypatch):
    # No stubs on purpose: an emergency must return before touching SSM/DynamoDB/KB.
    out = h.triage_symptoms("chest pain and left arm numb", patient_id="PAT-01")
    assert out["urgency"] == "emergency"
    assert out["emergency_number"] == "998"
    assert out["specialty"] is None


def test_triage_arabic_emergency():
    out = h.triage_symptoms("عندي ألم في الصدر وضيق في التنفس")
    assert out["urgency"] == "emergency"
    assert out["language"] == "ar"
    assert "998" in out["action"]


def test_triage_headache_blurry_vision_is_urgent_neurology(monkeypatch):
    _stub(monkeypatch)
    out = h.triage_symptoms("I've had this headache for 3 days and my vision gets blurry", patient_id="PAT-01")
    assert out["urgency"] == "urgent"
    assert out["specialty"] == "neurology"
    assert out["citation"]["source"] == "triage-guidelines.md"


def test_triage_history_raises_urgency(monkeypatch):
    _stub(monkeypatch)
    out = h.triage_symptoms("some mild chest discomfort when walking", patient_id="PAT-01")  # hypertension on file
    assert out["urgency"] == "urgent"
    assert any("hypertension" in m for m in out["history_modifiers"])


def test_triage_self_care(monkeypatch):
    _stub(monkeypatch)
    out = h.triage_symptoms("mild runny nose and sneezing since yesterday")
    assert out["urgency"] == "self-care"
    assert out["specialty"] == "general"


# ---- history / medications ---------------------------------------------------

def test_patient_history(monkeypatch):
    _stub(monkeypatch)
    out = h.get_patient_history("PAT-02")
    assert out["conditions"] == ["type-2 diabetes"]
    assert out["allergies"] == ["penicillin"]
    assert h.get_patient_history("PAT-99")["found"] is False


def test_check_medications_flags_without_dosing(monkeypatch):
    _stub(monkeypatch)
    out = h.check_medications("PAT-01", "dizzy when standing", additional_medications="lisinopril")
    assert any("blood pressure" in f.lower() for f in out["flags"])
    assert any("dizziness" in f.lower() for f in out["flags"])
    assert "mg" not in " ".join(out["flags"]).lower()
    assert "pharmacist" in out["disclaimer"].lower()


def test_check_medications_allergy_conflict(monkeypatch):
    _stub(monkeypatch)
    out = h.check_medications("PAT-02", "sore throat", additional_medications="penicillin")
    assert any("allergy" in f.lower() for f in out["flags"])


# ---- specialist / booking / summary -----------------------------------------

def test_find_specialist_orders_by_coverage_then_date(monkeypatch):
    _stub(monkeypatch)
    out = h.find_specialist("neurology", insurance="PlanA")
    assert out["providers"][0]["provider_id"] == "PRV-01"
    assert "covered by PlanA" in out["providers"][0]["why"]
    assert out["fell_back_to_general"] is False


def test_find_specialist_falls_back_to_general(monkeypatch):
    _stub(monkeypatch)
    out = h.find_specialist("dermatology", insurance="PlanB")
    assert out["fell_back_to_general"] is True
    assert out["providers"][0]["specialty"] == "general"


def test_book_appointment_records_on_patient_and_writes_invite(monkeypatch):
    ddb = _stub(monkeypatch)
    captured = {}
    monkeypatch.setattr(h._s3, "put_object", lambda **kw: captured.update(kw))
    monkeypatch.setattr(h, "_default_summaries_bucket", lambda: "bucket")
    out = h.book_appointment("PAT-01", "PRV-01", reason="headache with blurry vision", language="en")
    assert out["booked"] is True
    assert out["appointment"]["date"] == "2026-08-25"
    assert out["insurance_covered"] is True
    assert "mention" in out["reminder"]
    assert ddb.tables["workshop-health-patients"].updates, "booking must be persisted"
    assert out["calendar_invite"].startswith("s3://bucket/appointments/PAT-01/")
    assert b"BEGIN:VEVENT" in captured["Body"] and b"Asia/Dubai" in captured["Body"]


def test_book_appointment_survives_invite_failure(monkeypatch):
    _stub(monkeypatch)
    monkeypatch.setattr(h, "_default_summaries_bucket", lambda: (_ for _ in ()).throw(RuntimeError("no bucket")))
    out = h.book_appointment("PAT-01", "PRV-04", language="ar")
    assert out["booked"] is True
    assert out["calendar_invite"] is None
    assert out["confirmation"].startswith("تم الحجز")


def test_visit_summary_is_bilingual_and_has_no_diagnosis(monkeypatch):
    _stub(monkeypatch)
    captured = {}
    monkeypatch.setattr(h._s3, "put_object", lambda **kw: captured.update(kw))
    monkeypatch.setattr(h, "_default_summaries_bucket", lambda: "bucket")
    out = h.create_visit_summary("PAT-01", "headache for 3 days, blurry vision", "urgent", specialty="neurology",
                                 interaction_flags="none", language="en")
    assert out["s3_uri"].startswith("s3://bucket/summaries/PAT-01/")
    assert "ملخص الزيارة" in out["summary"] and "Visit summary" in out["summary"]
    assert "diagnos" not in out["summary"].lower().replace("no diagnosis", "")
    assert captured["ContentType"].startswith("text/markdown")


# ---- vitals -------------------------------------------------------------------

def test_vitals_normal(monkeypatch):
    ddb = _stub(monkeypatch)
    out = h.record_vitals("PAT-01", systolic=118, diastolic=76, heart_rate=68, language="en")
    assert out["severity"] == "normal"
    assert out["trend"] is None
    assert ddb.tables["workshop-health-patients"].updates


def test_vitals_high_bp_with_hypertension_is_urgent(monkeypatch):
    _stub(monkeypatch)
    out = h.record_vitals("PAT-01", systolic=152, diastolic=94)
    assert out["severity"] == "urgent"
    assert "hypertension" in out["history_context"]
    assert "today" in out["action"]


def test_vitals_crisis_is_emergency_998(monkeypatch):
    _stub(monkeypatch)
    out = h.record_vitals("PAT-01", systolic=185, diastolic=122, language="ar")
    assert out["severity"] == "emergency"
    assert "998" in out["action"]


def test_vitals_glucose_and_trend(monkeypatch):
    _stub(monkeypatch)
    PATIENTS["PAT-02"]["vitals"] = [{"recorded_at": "2026-09-29T08:00:00+00:00", "glucose_mmol": 7.5}]
    try:
        out = h.record_vitals("PAT-02", glucose_mmol=12.0)
        assert out["severity"] == "follow-up"
        assert out["trend"]["change"]["glucose_mmol"] == 4.5
        assert "diabetes" in out["history_context"]
        assert h.record_vitals("PAT-02", glucose_mmol=2.5)["severity"] == "emergency"
    finally:
        PATIENTS["PAT-02"].pop("vitals", None)


# ---- lambda routing -----------------------------------------------------------

class Ctx:
    class client_context:
        custom = {"bedrockAgentCoreToolName": "health-lambda-tools___get_patient_history"}


def test_handler_routes_by_gateway_tool_name(monkeypatch):
    _stub(monkeypatch)
    out = h.handler({"patient_id": "PAT-01"}, Ctx())
    assert out["name"] == "Aisha Rahman"


def test_handler_unknown_tool():
    class C:
        client_context = None
    out = h.handler({"tool_name": "nope"}, C())
    assert "unknown tool" in out["error"]
