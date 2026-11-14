import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import as_utc
from app.core.config import Settings
from app.core.errors import Conflict, IdentityRequired, NotFound
from app.db.base import Appointment, AppointmentStatus
from app.repositories import ClinicRepository
from app.schemas import AvailabilityQuery, BookingRequest, CancellationRequest, RescheduleRequest


class SchedulingService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.repo = ClinicRepository(session)
        self.settings = settings

    async def availability(self, query: AvailabilityQuery):
        now = datetime.now(timezone.utc) + timedelta(minutes=self.settings.booking_buffer_minutes)
        start = max(query.from_time.astimezone(timezone.utc), now)
        end = query.to_time.astimezone(timezone.utc)
        if end <= start:
            return []
        return await self.repo.available_slots(start, end, query.doctor_ids,
                                               query.branch_ids, query.department_id, query.limit)

    async def book(self, data: BookingRequest) -> Appointment:
        patient = await self.repo.patient(data.patient_id)
        if not patient:
            raise NotFound("Patient not found")
        if patient.full_name.casefold() != " ".join(data.patient_full_name.split()).casefold():
            raise IdentityRequired("The patient's full name must match before booking")
        existing = await self.repo.pms_log(data.idempotency_key, "book")
        # The appointment id is recorded in our own request log, so a retry is recognised even when the
        # external PMS does not echo the id back in its response.
        previous_id = (existing.request_payload or {}).get("appointment_id") if existing else None
        if previous_id:
            appointment = await self.repo.appointment(uuid.UUID(previous_id))
            if appointment:
                return appointment
        slot = await self.repo.slot(data.slot_id, lock=True)
        if not slot:
            raise NotFound("Appointment slot not found")
        if slot.is_booked or slot.blocked_reason:
            raise Conflict("That slot is no longer available")
        if as_utc(slot.starts_at) <= datetime.now(timezone.utc) + timedelta(minutes=self.settings.booking_buffer_minutes):
            raise Conflict("That slot is inside the booking buffer")
        slot.is_booked = True
        appointment = Appointment(patient_id=patient.id, doctor_id=slot.doctor_id,
                                  branch_id=slot.branch_id, slot_id=slot.id, reason=data.reason,
                                  source=data.source)
        await self.repo.add(appointment)
        await self.repo.audit("api", "appointment.booked", appointment)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise Conflict("That slot was booked concurrently") from exc
        return appointment

    async def reschedule(self, appointment_id: uuid.UUID, data: RescheduleRequest) -> Appointment:
        appointment = await self.repo.appointment(appointment_id, lock=True)
        if not appointment:
            raise NotFound("Appointment not found")
        if appointment.status != AppointmentStatus.booked:
            raise Conflict("Only booked appointments can be rescheduled")
        if appointment.slot_id == data.new_slot_id:
            return appointment
        old_slot = await self.repo.slot(appointment.slot_id, lock=True)
        new_slot = await self.repo.slot(data.new_slot_id, lock=True)
        if not new_slot:
            raise NotFound("New slot not found")
        if new_slot.is_booked or new_slot.blocked_reason:
            raise Conflict("The new slot is no longer available")
        if as_utc(new_slot.starts_at) <= datetime.now(timezone.utc) + timedelta(minutes=self.settings.booking_buffer_minutes):
            raise Conflict("The new slot is inside the booking buffer")
        new_slot.is_booked = True
        if old_slot:
            old_slot.is_booked = False
        appointment.slot_id = new_slot.id
        appointment.doctor_id = new_slot.doctor_id
        appointment.branch_id = new_slot.branch_id
        await self.repo.audit("api", "appointment.rescheduled", appointment,
                              {"new_slot_id": str(new_slot.id)})
        await self.session.flush()
        return appointment

    async def cancel(self, appointment_id: uuid.UUID, data: CancellationRequest) -> Appointment:
        appointment = await self.repo.appointment(appointment_id, lock=True)
        if not appointment:
            raise NotFound("Appointment not found")
        if appointment.status == AppointmentStatus.cancelled:
            return appointment
        if appointment.status != AppointmentStatus.booked:
            raise Conflict("This appointment cannot be cancelled")
        slot = await self.repo.slot(appointment.slot_id, lock=True)
        if slot:
            slot.is_booked = False
        appointment.status = AppointmentStatus.cancelled
        await self.repo.audit("api", "appointment.cancelled", appointment, {"reason": data.reason})
        await self.session.flush()
        return appointment
