"""Simulate a Retell phone call against a running server, with real signatures, no phone needed.

    python -m scripts.simulate_call                       # http://localhost:8000, secret from .env
    python -m scripts.simulate_call --base https://<host> # against a deployed server

It walks a caller through: first contact, dropped call, callback that resumes, registration,
availability, booking, read-back, escalation, and a 10-caller race for one slot. Every step prints
what the voice agent would receive. Exit code 1 means something a real call depends on is broken.
"""
import argparse
import asyncio
import hashlib
import hmac
import json
import sys
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.core.config import get_settings


class Caller:
    def __init__(self, client: httpx.AsyncClient, base: str, secret: str):
        self.client, self.base, self.secret = client, base.rstrip("/"), secret

    async def _post(self, path: str, body: dict) -> tuple[int, dict]:
        raw = json.dumps(body).encode()
        ts = str(int(time.time() * 1000))
        digest = hmac.new(self.secret.encode(), raw + ts.encode(), hashlib.sha256).hexdigest()
        response = await self.client.post(self.base + path, content=raw, headers={
            "x-retell-signature": f"v={ts},d={digest}", "content-type": "application/json"})
        return response.status_code, (response.json() if response.content else {})

    async def event(self, name: str, call_id: str, phone: str) -> None:
        code, body = await self._post("/retell/webhook", {"event": name,
                                                          "call": {"call_id": call_id, "from_number": phone}})
        check(code == 200, f"webhook {name} -> HTTP {code} {body}")

    async def tool(self, name: str, call_id: str, phone: str, **args) -> dict:
        code, body = await self._post("/retell/functions", {"name": name, "args": args,
                                                            "call": {"call_id": call_id, "from_number": phone}})
        check(code == 200, f"{name} -> HTTP {code} {body}")
        return body["result"]


FAILURES: list[str] = []


def check(ok: bool, message: str) -> None:
    if not ok:
        FAILURES.append(message)
        print(f"  FAIL  {message}")


def window(days_ahead: int, start_hour: int, end_hour: int) -> tuple[str, str]:
    tz = ZoneInfo("Asia/Kolkata")
    day = datetime.now(tz).date() + timedelta(days=days_ahead)
    while day.weekday() == 6:  # the demo clinic is closed on Sundays
        day += timedelta(days=1)
    base = datetime.combine(day, datetime.min.time(), tz)
    return base.replace(hour=start_hour).isoformat(), base.replace(hour=end_hour).isoformat()


async def run(base: str, secret: str) -> None:
    run_id = uuid.uuid4().hex[:6]
    phone = f"+9198{int(time.time()) % 100_000_000:08d}"
    async with httpx.AsyncClient(timeout=30) as client:
        c = Caller(client, base, secret)
        health = await client.get(f"{c.base}/api/v1/health")
        check(health.status_code == 200, f"health -> HTTP {health.status_code}")
        print(f"\nCaller {phone} (run {run_id})")

        first, second = f"sim-{run_id}-1", f"sim-{run_id}-2"
        await c.event("call_started", first, phone)
        context = await c.tool("get_call_context", first, phone)
        print(f"  today is {context.get('today')}; known patients: {len(context.get('known_patients', []))}")
        directory = await c.tool("list_clinic_directory", first, phone)
        pediatrics = next(d for d in directory["departments"] if d["name"] == "Pediatrics")
        await c.tool("save_progress", first, phone, intent="booking",
                     collected={"department": "Pediatrics", "patient_name": f"Kabir {run_id}"},
                     missing_fields=["preferred_date"])
        await c.event("call_ended", first, phone)
        print("  ...call dropped, caller rings back")

        await c.event("call_started", second, phone)
        context = await c.tool("get_call_context", second, phone)
        check(context.get("resumed_previous_call") is True, "callback did not resume the dropped call")
        print(f"  resumed with {context.get('saved_progress', {}).get('collected')}")
        patient = await c.tool("register_patient", second, phone, full_name=f"Kabir {run_id}")
        start, end = window(3, 9, 18)
        slots = (await c.tool("find_availability", second, phone, from_time=start, to_time=end,
                              department_id=pediatrics["department_id"], limit=3)).get("slots", [])
        check(bool(slots), "no pediatrics slots found; did the seed run?")
        for slot in slots:
            print(f"  offer: {slot['doctor_name']} | {slot['branch_name']} | {slot['say_time']}")
        booked = await c.tool("book_appointment", second, phone, patient_id=patient["patient_id"],
                              patient_full_name=patient["full_name"], slot_id=slots[0]["slot_id"])
        check(booked.get("confirmed") is True, f"booking failed: {booked}")
        print(f"  agent says: Booked with {booked.get('doctor_name')} at {booked.get('branch_name')}, "
              f"{booked.get('say_time')} (ref {booked.get('pms_reference')})")
        await c.event("call_ended", second, phone)

        third = f"sim-{run_id}-3"
        context = await c.tool("get_call_context", third, phone)
        check(context.get("resumed_previous_call") is False, "a finished booking was resumed again")
        escalation = await c.tool("escalate_to_human", third, phone, reason="Fever for three days",
                                  clinical_concern=True)
        check(escalation.get("follow_up_created") is True, f"escalation failed: {escalation}")
        bad = await c.tool("cancel_appointment", third, phone, appointment_id="the one on thursday")
        check("error" in bad, "invalid id was accepted")
        print("  escalation created; invalid ids are rejected without a server error")

        start, end = window(4, 9, 13)
        target = (await c.tool("find_availability", third, phone, from_time=start, to_time=end))["slots"][0]
        racers = []
        for i in range(10):
            racer_phone = f"{phone[:-1]}{i}"
            racers.append((racer_phone, await c.tool("register_patient", f"race-{run_id}-{i}", racer_phone,
                                                     full_name=f"Racer {run_id} {chr(65 + i)}")))
        results = await asyncio.gather(*[
            c.tool("book_appointment", f"race-{run_id}-{i}", racer_phone, patient_id=p["patient_id"],
                   patient_full_name=p["full_name"], slot_id=target["slot_id"])
            for i, (racer_phone, p) in enumerate(racers)])
        winners = sum(1 for r in results if r.get("confirmed"))
        check(winners == 1, f"{winners} callers got the same slot")
        print(f"  10 callers raced for one slot: {winners} confirmed, {10 - winners} told it was taken")

    print("\nPASS: every step a real call depends on worked." if not FAILURES
          else f"\n{len(FAILURES)} problem(s) found.")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--secret", default=None, help="defaults to RETELL_WEBHOOK_SECRET from the environment/.env")
    options = parser.parse_args()
    asyncio.run(run(options.base, options.secret or get_settings().retell_webhook_secret))
