"""Drives /retell/functions exactly as Retell does: signed body, {name, call, args}."""
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.db.base import AppointmentSlot, CallSession, CallStatus, FollowUpTask
from app.db.session import get_session
from app.main import app
from app.services.pms import PMSService
from scripts.seed import create_reference_data, top_up_slots

SECRET = "test-secret-key"
CALLER = "+919811112222"


def signed(body: dict) -> tuple[bytes, dict]:
    raw = json.dumps(body).encode()
    ts = str(int(time.time() * 1000))
    digest = hmac.new(SECRET.encode(), raw + ts.encode(), hashlib.sha256).hexdigest()
    return raw, {"x-retell-signature": f"v={ts},d={digest}", "content-type": "application/json"}


@pytest.fixture
async def client(session, monkeypatch):
    async with session.begin():
        await create_reference_data(session)
        await top_up_slots(session, horizon_days=10)

    async def confirmed(self, operation, key, payload):
        return {"confirmed": True, "operation": operation, "reference": "PMS-TEST"}
    monkeypatch.setattr(PMSService, "_send", confirmed)

    async def override():
        yield session
    app.dependency_overrides[get_session] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
        yield value
    app.dependency_overrides.clear()


async def tool(client, name, args=None, call_id="call-1"):
    raw, headers = signed({"name": name, "call": {"call_id": call_id, "from_number": CALLER},
                           "args": args or {}})
    response = await client.post("/retell/functions", content=raw, headers=headers)
    assert response.status_code == 200, response.text  # tool problems must never surface as HTTP errors
    return response.json()["result"]


async def event(client, name, call_id):
    raw, headers = signed({"event": name, "call": {"call_id": call_id, "from_number": CALLER}})
    assert (await client.post("/retell/webhook", content=raw, headers=headers)).status_code == 200


def next_weekday_window():
    tz = ZoneInfo("Asia/Kolkata")
    day = datetime.now(tz).date() + timedelta(days=2)
    while day.weekday() == 6:
        day += timedelta(days=1)
    return (datetime.combine(day, datetime.min.time(), tz).replace(hour=9).isoformat(),
            datetime.combine(day, datetime.min.time(), tz).replace(hour=18).isoformat())


async def test_new_caller_books_with_speakable_results(client, session):
    await event(client, "call_started", "call-1")
    context = await tool(client, "get_call_context")
    assert context["caller_phone"] == CALLER and context["known_patients"] == []
    assert context["today"] and context["resumed_previous_call"] is False

    directory = await tool(client, "list_clinic_directory")
    cardiology = next(d for d in directory["departments"] if d["name"] == "Cardiology")
    assert cardiology["doctors"][0]["name"] == "Dr. Ananya Sharma"

    patient = await tool(client, "register_patient", {"full_name": "ravi  kumar"})
    assert patient["created"] and patient["full_name"] == "Ravi Kumar"
    again = await tool(client, "register_patient", {"full_name": "Ravi Kumar"})
    assert not again["created"] and again["patient_id"] == patient["patient_id"]

    start, end = next_weekday_window()
    slots = (await tool(client, "find_availability", {"from_time": start, "to_time": end,
                                                      "department_id": cardiology["department_id"]}))["slots"]
    assert slots and slots[0]["doctor_name"] == "Dr. Ananya Sharma"
    assert "Arogya Central" in slots[0]["branch_name"] and "AM" in slots[0]["say_time"]
    assert slots[0]["local_start"].endswith("+05:30")

    booked = await tool(client, "book_appointment", {"patient_id": patient["patient_id"],
                                                     "patient_full_name": "Ravi Kumar",
                                                     "slot_id": slots[0]["slot_id"]})
    assert "error" not in booked, booked
    assert booked["confirmed"] and booked["pms_reference"] == "PMS-TEST"
    assert booked["say_time"] == slots[0]["say_time"]

    retried = await tool(client, "book_appointment", {"patient_id": patient["patient_id"],
                                                      "patient_full_name": "Ravi Kumar",
                                                      "slot_id": slots[0]["slot_id"]})
    assert retried["appointment_id"] == booked["appointment_id"]  # Retell retry is idempotent

    context = await tool(client, "get_call_context", call_id="call-2")
    upcoming = context["known_patients"][0]["upcoming_appointments"]
    assert upcoming[0]["doctor_name"] == "Dr. Ananya Sharma"
    assert context["resumed_previous_call"] is False  # finished bookings are not "resumed"

    cancelled = await tool(client, "cancel_appointment", {"appointment_id": upcoming[0]["appointment_id"]},
                           call_id="call-2")
    assert cancelled["confirmed"] and cancelled["status"] == "cancelled"


async def test_failed_pms_confirmation_rolls_back_the_booking(client, session, monkeypatch):
    async def down(self, operation, key, payload):
        raise RuntimeError("PMS unreachable")
    monkeypatch.setattr(PMSService, "_send", down)
    patient = await tool(client, "register_patient", {"full_name": "Asha Singh"})
    start, end = next_weekday_window()
    slot = (await tool(client, "find_availability", {"from_time": start, "to_time": end}))["slots"][0]
    result = await tool(client, "book_appointment", {"patient_id": patient["patient_id"],
                                                     "patient_full_name": "Asha Singh",
                                                     "slot_id": slot["slot_id"]})
    assert result["error"]["code"] == "conflict"
    row = await session.get(AppointmentSlot, __import__("uuid").UUID(slot["slot_id"]))
    await session.refresh(row)
    assert row.is_booked is False  # previously this slot stayed booked with no appointment confirmed
    await session.rollback()


async def test_bad_arguments_return_errors_not_500s(client):
    assert (await tool(client, "cancel_appointment", {"appointment_id": "the cardiology one"}))["error"]["code"] \
        == "invalid_arguments"
    assert (await tool(client, "find_availability", {"from_time": "tomorrow"}))["error"]["code"] \
        == "invalid_arguments"
    assert (await tool(client, "book_appointment", {}))["error"]["code"] == "invalid_arguments"
    assert (await tool(client, "no_such_tool"))["error"]["code"] == "not_found"


async def test_escalation_uses_the_retell_call_id(client, session):
    result = await tool(client, "escalate_to_human", {"reason": "Chest pain since morning",
                                                      "clinical_concern": True}, call_id="call-9")
    assert result["follow_up_created"] and result["priority"] == "urgent"
    assert await session.scalar(select(func.count()).select_from(FollowUpTask)) == 1
    await session.rollback()


async def test_dropped_call_resumes_saved_progress(client, session):
    await event(client, "call_started", "drop-1")
    await tool(client, "save_progress", {"intent": "booking", "collected": {"department": "Pediatrics",
                                                                            "patient_name": "Kabir"},
                                         "missing_fields": ["preferred_date"]}, call_id="drop-1")
    await event(client, "call_ended", "drop-1")
    await event(client, "call_started", "drop-2")
    context = await tool(client, "get_call_context", call_id="drop-2")
    assert context["resumed_previous_call"] is True
    assert context["saved_progress"]["collected"]["department"] == "Pediatrics"
    assert context["saved_progress"]["missing_fields"] == ["preferred_date"]


async def test_call_without_unfinished_request_is_not_resumed(client, session):
    await event(client, "call_started", "hello-1")
    await event(client, "call_ended", "hello-1")
    call = await session.scalar(select(CallSession).where(CallSession.retell_call_id == "hello-1"))
    assert call.status == CallStatus.completed
    await session.rollback()


async def test_slot_top_up_is_idempotent(session):
    async with session.begin():
        await create_reference_data(session)
        first = await top_up_slots(session, horizon_days=7)
        second = await top_up_slots(session, horizon_days=7)
        third = await top_up_slots(session, horizon_days=9)
    assert first > 0 and second == 0 and third > 0


async def test_resumed_request_is_not_resumed_twice(client, session):
    await event(client, "call_started", "twice-1")
    await tool(client, "save_progress", {"intent": "booking", "collected": {"department": "Cardiology"}},
               call_id="twice-1")
    await event(client, "call_ended", "twice-1")
    await event(client, "call_started", "twice-2")
    assert (await tool(client, "get_call_context", call_id="twice-2"))["resumed_previous_call"] is True
    await tool(client, "escalate_to_human", {"reason": "wants to speak to reception"}, call_id="twice-2")
    await event(client, "call_ended", "twice-2")
    await event(client, "call_started", "twice-3")
    assert (await tool(client, "get_call_context", call_id="twice-3"))["resumed_previous_call"] is False
