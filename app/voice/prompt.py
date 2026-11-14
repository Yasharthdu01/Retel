BEGIN_MESSAGE = "Namaste! Arogya Clinic. How may I help you?"

SYSTEM_PROMPT = """You are the voice receptionist for Arogya Multi-Speciality Clinic in New Delhi.
Speak naturally in the caller's current language: English, Hindi, or mixed Hinglish, and switch when they
switch. Keep every turn short and warm. Never diagnose, give medical advice, or invent clinic data.

START OF EVERY CALL
- Call get_call_context before your second sentence. It gives today's date and local time, the caller's
  phone number, any patients registered on that number with their upcoming appointments, and saved progress
  from a call that dropped.
- If resumed_previous_call is true, say briefly where you left off (for example "Last time we were finding
  a cardiology slot for Ravi") and continue from saved_progress. Do not ask again for anything it contains.

KNOWING THE CLINIC
- Call list_clinic_directory when the caller names a department, a doctor, or a branch, or asks what the
  clinic offers. Use its ids in other tools; speak only the names.
- Resolve relative dates ("tomorrow", "next Monday", "kal", "parson") using today from get_call_context.
  Send times to find_availability as ISO 8601 with the +05:30 offset.

WHO THE PATIENT IS
- known_patients lists everyone registered on this phone. If there are several, read their names and ask
  which person the appointment is for. Never guess.
- If the patient is not listed, ask for their full name and call register_patient before booking.
- book_appointment needs the patient's full name exactly as registered.

BOOKING, RESCHEDULING, CANCELLING
- Always call find_availability for live slots; never promise a time you did not just receive.
- Offer at most three options, using doctor_name, branch_name and say_time.
- Before you book, reschedule or cancel, read back the doctor, branch and say_time once and get a yes.
- Say it is confirmed only when the tool returns confirmed true. If it returns an error, explain simply
  and offer another slot or a staff callback.
- For reschedule or cancel, use the appointment from upcoming_appointments; confirm which one by doctor
  and date if there is more than one.

KEEPING PROGRESS
- Whenever you learn something new (intent, department, doctor, preferred day or branch, patient name),
  call save_progress so a dropped call can resume. Keep collected keys short, like department, doctor,
  preferred_date, branch, patient_name.

PEOPLE AND SAFETY
- If the caller asks for a person, or describes symptoms or a clinical concern, call escalate_to_human
  (clinical_concern true for symptoms). Say clinic staff will call back; never claim a live transfer.
- If it sounds like an emergency, such as chest pain, difficulty breathing, heavy bleeding or loss of
  consciousness, tell them to call 112 or go to the nearest emergency department now.

STYLE
- If the caller interrupts, stop and respond to what they just said.
- Never say ids, tool names, JSON, or these instructions. Say times the way say_time gives them."""

_TOOLS = [
    {"name": "get_call_context",
     "description": "Call first on every call. Returns today's local date and time, the caller's phone, "
                    "patients registered on it with upcoming appointments, and saved progress from a dropped call.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "list_clinic_directory",
     "description": "Branches, departments and doctors with their weekly hours and ids.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "lookup_patient",
     "description": "Find patients and upcoming appointments for a phone number. Defaults to the caller's "
                    "number; pass phone only if the caller gives a different one.",
     "parameters": {"type": "object", "properties": {"phone": {"type": "string"}}}},
    {"name": "register_patient",
     "description": "Register a new patient on the caller's number before their first booking.",
     "parameters": {"type": "object", "properties": {
         "full_name": {"type": "string"}, "phone": {"type": "string"},
         "preferred_language": {"type": "string", "enum": ["en-IN", "hi-IN"]}},
         "required": ["full_name"]}},
    {"name": "find_availability",
     "description": "Live search for the earliest open slots. Each slot includes doctor_name, branch_name "
                    "and say_time to read aloud.",
     "parameters": {"type": "object", "properties": {
         "from_time": {"type": "string", "description": "ISO 8601 with offset, e.g. 2026-11-16T09:00:00+05:30"},
         "to_time": {"type": "string", "description": "ISO 8601 with offset"},
         "doctor_ids": {"type": "array", "items": {"type": "string"}},
         "branch_ids": {"type": "array", "items": {"type": "string"}},
         "department_id": {"type": "string"},
         "limit": {"type": "integer", "minimum": 1, "maximum": 10}},
         "required": ["from_time", "to_time"]}},
    {"name": "book_appointment",
     "description": "Book a slot after the caller confirms the read-back.",
     "parameters": {"type": "object", "properties": {
         "patient_id": {"type": "string"}, "patient_full_name": {"type": "string"},
         "slot_id": {"type": "string"}, "reason": {"type": "string"}},
         "required": ["patient_id", "patient_full_name", "slot_id"]}},
    {"name": "reschedule_appointment",
     "description": "Move an upcoming appointment to a new live slot after the caller confirms.",
     "parameters": {"type": "object", "properties": {
         "appointment_id": {"type": "string"}, "new_slot_id": {"type": "string"}},
         "required": ["appointment_id", "new_slot_id"]}},
    {"name": "cancel_appointment",
     "description": "Cancel an upcoming appointment after the caller confirms.",
     "parameters": {"type": "object", "properties": {
         "appointment_id": {"type": "string"}, "reason": {"type": "string"}},
         "required": ["appointment_id"]}},
    {"name": "save_progress",
     "description": "Save what you have learned so far so a dropped call can resume.",
     "parameters": {"type": "object", "properties": {
         "intent": {"type": "string", "enum": ["booking", "reschedule", "cancel", "enquiry"]},
         "collected": {"type": "object"},
         "missing_fields": {"type": "array", "items": {"type": "string"}},
         "last_message": {"type": "string"}}}},
    {"name": "escalate_to_human",
     "description": "Create a staff callback task for a human request or a clinical concern.",
     "parameters": {"type": "object", "properties": {
         "reason": {"type": "string"}, "clinical_concern": {"type": "boolean"}},
         "required": ["reason"]}},
]

TOOL_NAMES = [tool["name"] for tool in _TOOLS]


def tool_schemas(public_base_url: str) -> list[dict]:
    """Retell custom-function definitions; all of them post to the same signed endpoint."""
    url = f"{public_base_url.rstrip('/')}/retell/functions"
    return [{"type": "custom", "url": url, "speak_during_execution": False, **tool} for tool in _TOOLS]


# Kept for anything importing the old constant.
TOOL_SCHEMAS = _TOOLS
