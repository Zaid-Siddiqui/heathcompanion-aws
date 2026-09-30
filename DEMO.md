# Demo script — Health Companion (5 minutes)

**Pitch line:** Aisha, 58, hypertensive, Arabic-speaking. One conversation saves an unnecessary ER visit and gets
her to the right specialist with a booked slot and a doctor-ready summary — without ever diagnosing or dosing.

Patient id is carried by the app (`DEFAULT_PATIENT_ID=PAT-01` for the demo); don't say "I'm patient PAT-01" in the
prompt (the baseline guardrail's prompt-attack filter dislikes identity claims).

## 1. The slide scenario (English, ~60 s)
> I've had this headache for 3 days and my vision gets blurry. Is it serious? Which doctor should I see?

Expect: **URGENT** · cites `triage-guidelines.md` · hypertension raised priority · amlodipine flag "for your doctor
to confirm" · Dr. Nadia Farouk, neurology, Central Clinic, covered by PlanA, 25 Aug · offers to book.

## 2. Book + summary (~45 s)
> Yes, book it with Dr. Nadia Farouk and prepare the visit summary for her.

Expect: booking id, date/time, insurance ✅, bilingual summary, S3 path, "before your appointment, mention…" reminder.
Show the side effects if asked: appointment on the patient item in DynamoDB, `.ics` + `.md` in S3.

## 3. Guardrail moment — emergency (Arabic, ~20 s)
> عندي ألم في الصدر وخدر في الذراع اليسرى

Expect: instant Arabic **998** message; no tools, no model call (say so — it's deterministic).

## 4. Guardrail moment — dosing (~15 s)
> How many mg of paracetamol should I take for this headache?

Expect: refusal + pharmacist/doctor pointer + offer to review medications.

## 5. Guardrail moment — diagnosis (~30 s)
> I have a sore throat and fever for two days. Do I have strep throat? What is my diagnosis?
(switch patient to PAT-02 / David if the UI allows)

Expect: "I can't diagnose" **but** triage continues — urgent (diabetes raises infection urgency), penicillin allergy
surfaced, GP suggested.

## 6. Monitoring (~30 s)
> My blood pressure this morning was 152 over 94 and my pulse 88. Should I worry?

Expect: reading logged, **urgent**, "contact your doctor today", hypertension context, trend on repeat readings.

## 7. Memory (~20 s, new session)
> my headache is back

Expect: recalls the earlier headache/vision episode and preferred language from AgentCore Memory.

## Talking points
- Strands agent on AgentCore Runtime; 7 tools as one Lambda behind AgentCore Gateway (MCP, IAM auth, SigV4);
  AgentCore Memory (user-preference + semantic); Bedrock Knowledge Base for guidelines; baseline Bedrock Guardrail.
- Safety is layered: deterministic bilingual checks → baseline guardrail on input → prompt/tool design. We probed
  the guardrail and found its output filter blocks the *compliant* phrasing — so we apply it on input (README).
- Regional: 998/999, Arabic-first, PHI stays in-region (`me-central-1` in production).
- Roadmap: report upload via Textract (lab reports, not X-ray interpretation), nearest-clinic via Location Service,
  proactive reminders via SNS, rehab check-ins.
