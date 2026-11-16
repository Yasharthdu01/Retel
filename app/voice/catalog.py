"""Turns database rows into facts the voice agent can say out loud.

The agent still passes IDs back to tools, but every result also carries doctor, department and
branch names plus a branch-local time string, so the read-back never needs an ID or a UTC time.
"""
import uuid
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import as_utc
from app.db.base import Appointment, AppointmentSlot
from app.repositories import ClinicRepository

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]



def speakable_time(value: datetime, tz_name: str) -> str:
    local = as_utc(value).astimezone(ZoneInfo(tz_name))
    hour = local.strftime("%I").lstrip("0")
    return f"{local:%A} {local.day} {local:%B %Y}, {hour}:{local:%M} {local:%p}"


class Catalog:
    def __init__(self, data: dict[str, list[Any]], default_tz: str):
        self.default_tz = default_tz
        self.branches = {b.id: b for b in data["branches"]}
        self.departments = {d.id: d for d in data["departments"]}
        self.doctors = {d.id: d for d in data["doctors"]}
        self.schedules = data["schedules"]

    @classmethod
    async def load(cls, session: AsyncSession, default_tz: str) -> "Catalog":
        return cls(await ClinicRepository(session).directory(), default_tz)

    def _tz(self, branch_id: uuid.UUID) -> str:
        branch = self.branches.get(branch_id)
        return branch.timezone if branch else self.default_tz

    def _names(self, doctor_id: uuid.UUID, branch_id: uuid.UUID) -> dict[str, Any]:
        doctor = self.doctors.get(doctor_id)
        department = self.departments.get(doctor.department_id) if doctor else None
        branch = self.branches.get(branch_id)
        return {"doctor_name": doctor.full_name if doctor else "Unknown doctor",
                "department": department.name if department else None,
                "branch_name": branch.name if branch else "Unknown branch",
                "branch_address": branch.address if branch else None}

    def slot(self, slot: AppointmentSlot) -> dict[str, Any]:
        tz = self._tz(slot.branch_id)
        start = as_utc(slot.starts_at).astimezone(ZoneInfo(tz))
        return {"slot_id": str(slot.id), "doctor_id": str(slot.doctor_id), "branch_id": str(slot.branch_id),
                **self._names(slot.doctor_id, slot.branch_id),
                "local_start": start.isoformat(), "say_time": speakable_time(slot.starts_at, tz),
                "duration_minutes": int((as_utc(slot.ends_at) - as_utc(slot.starts_at)).total_seconds() // 60)}

    def appointment(self, appointment: Appointment, slot: AppointmentSlot | None) -> dict[str, Any]:
        tz = self._tz(appointment.branch_id)
        result: dict[str, Any] = {"appointment_id": str(appointment.id), "status": appointment.status.value,
                                  **self._names(appointment.doctor_id, appointment.branch_id),
                                  "reason": appointment.reason, "pms_reference": appointment.pms_reference}
        if slot:
            result["say_time"] = speakable_time(slot.starts_at, tz)
            result["is_upcoming"] = as_utc(slot.starts_at) > datetime.now(timezone.utc)
        return result

    def directory(self) -> dict[str, Any]:
        days_by_doctor: dict[uuid.UUID, list[str]] = {}
        for schedule in self.schedules:
            branch = self.branches.get(schedule.branch_id)
            days_by_doctor.setdefault(schedule.doctor_id, []).append(
                f"{WEEKDAYS[schedule.weekday]} {schedule.start_time:%H:%M}-{schedule.end_time:%H:%M}"
                f" at {branch.name if branch else 'unknown branch'}")
        return {
            "branches": [{"branch_id": str(b.id), "name": b.name, "address": b.address, "phone": b.phone}
                         for b in self.branches.values()],
            "departments": [
                {"department_id": str(dep.id), "name": dep.name, "description": dep.description,
                 "doctors": [{"doctor_id": str(doc.id), "name": doc.full_name,
                              "appointment_minutes": doc.appointment_duration_minutes,
                              "weekly_hours": days_by_doctor.get(doc.id, [])}
                             for doc in self.doctors.values() if doc.department_id == dep.id]}
                for dep in self.departments.values()],
        }
