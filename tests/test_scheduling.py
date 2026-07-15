from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import Settings
from app.core.errors import Conflict, IdentityRequired
from app.db.base import AppointmentSlot, Branch, Department, Doctor, Patient
from app.schemas import BookingRequest
from app.services.scheduling import SchedulingService


@pytest.mark.asyncio
async def test_booking_requires_name_and_prevents_double_booking(session):
    department = Department(name="General", description=None)
    branch = Branch(name="Central", address="A valid clinic address", phone="+911140001001")
    session.add_all([department, branch])
    await session.flush()
    doctor = Doctor(full_name="Dr Test", registration_number="REG-1",
                    department_id=department.id, appointment_duration_minutes=30)
    patient = Patient(full_name="Asha Singh", phone="+919999999999")
    session.add_all([doctor, patient])
    await session.flush()
    start = datetime.now(timezone.utc) + timedelta(days=2)
    slot = AppointmentSlot(doctor_id=doctor.id, branch_id=branch.id,
                           starts_at=start, ends_at=start + timedelta(minutes=30))
    session.add(slot)
    await session.flush()
    service = SchedulingService(session, Settings(database_url="sqlite+aiosqlite:///:memory:",
                                                    retell_webhook_secret="test-secret"))
    with pytest.raises(IdentityRequired):
        await service.book(BookingRequest(patient_id=patient.id, patient_full_name="Wrong Person",
                                          slot_id=slot.id, idempotency_key="wrong-name-1"))
    appointment = await service.book(BookingRequest(patient_id=patient.id, patient_full_name="Asha Singh",
                                                     slot_id=slot.id, idempotency_key="booking-key-1"))
    assert appointment.patient_id == patient.id
    with pytest.raises(Conflict):
        await service.book(BookingRequest(patient_id=patient.id, patient_full_name="Asha Singh",
                                          slot_id=slot.id, idempotency_key="booking-key-2"))
