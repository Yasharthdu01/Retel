import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import (
    Appointment, AppointmentSlot, AuditLog, Branch, CallSession,
    CallStatus, ConversationState, Department, Doctor, DoctorSchedule, Patient, PMSLog,
)


class ClinicRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, entity: Any) -> Any:
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def patient(self, patient_id: uuid.UUID) -> Patient | None:
        return await self.session.get(Patient, patient_id)

    async def patients_by_phone(self, phone: str) -> list[Patient]:
        result = await self.session.scalars(select(Patient).where(Patient.phone == phone).order_by(Patient.full_name))
        return list(result)

    async def appointment(self, appointment_id: uuid.UUID, lock: bool = False) -> Appointment | None:
        query = select(Appointment).where(Appointment.id == appointment_id)
        if lock:
            query = query.with_for_update()
        return await self.session.scalar(query)

    async def slot(self, slot_id: uuid.UUID, lock: bool = False) -> AppointmentSlot | None:
        query = select(AppointmentSlot).where(AppointmentSlot.id == slot_id)
        if lock:
            query = query.with_for_update()
        return await self.session.scalar(query)

    async def available_slots(
        self, from_time: datetime, to_time: datetime, doctor_ids: list[uuid.UUID],
        branch_ids: list[uuid.UUID], department_id: uuid.UUID | None, limit: int,
    ) -> list[AppointmentSlot]:
        filters = [
            AppointmentSlot.starts_at >= from_time,
            AppointmentSlot.starts_at < to_time,
            AppointmentSlot.is_booked.is_(False),
            AppointmentSlot.blocked_reason.is_(None),
        ]
        if doctor_ids:
            filters.append(AppointmentSlot.doctor_id.in_(doctor_ids))
        if branch_ids:
            filters.append(AppointmentSlot.branch_id.in_(branch_ids))
        query: Select[Any] = select(AppointmentSlot).join(Doctor).where(and_(*filters))
        if department_id:
            query = query.where(Doctor.department_id == department_id)
        result = await self.session.scalars(query.order_by(AppointmentSlot.starts_at).limit(limit))
        return list(result)

    async def appointment_history(self, patient_id: uuid.UUID) -> list[Appointment]:
        result = await self.session.scalars(
            select(Appointment).where(Appointment.patient_id == patient_id).order_by(Appointment.created_at.desc())
        )
        return list(result)

    async def call_by_retell_id(self, call_id: str) -> CallSession | None:
        return await self.session.scalar(select(CallSession).where(CallSession.retell_call_id == call_id))

    async def recoverable_call(self, phone: str) -> CallSession | None:
        return await self.session.scalar(
            select(CallSession).where(
                CallSession.phone == phone,
                or_(CallSession.status == CallStatus.disconnected, CallSession.status == CallStatus.active),
            ).order_by(CallSession.updated_at.desc()).limit(1)
        )

    async def state(self, session_id: uuid.UUID, lock: bool = False) -> ConversationState | None:
        query = select(ConversationState).where(ConversationState.call_session_id == session_id)
        if lock:
            query = query.with_for_update()
        return await self.session.scalar(query)

    async def pms_log(self, key: str, operation: str) -> PMSLog | None:
        return await self.session.scalar(select(PMSLog).where(PMSLog.idempotency_key == key, PMSLog.operation == operation))

    async def patient_by_phone_and_name(self, phone: str, full_name: str) -> Patient | None:
        return await self.session.scalar(
            select(Patient).where(Patient.phone == phone, Patient.full_name == full_name).limit(1)
        )

    async def directory(self) -> dict[str, list[Any]]:
        """Small reference tables; read in full so tool results can carry human-readable names."""
        branches = list(await self.session.scalars(
            select(Branch).where(Branch.active.is_(True)).order_by(Branch.name)))
        departments = list(await self.session.scalars(select(Department).order_by(Department.name)))
        doctors = list(await self.session.scalars(
            select(Doctor).where(Doctor.active.is_(True)).order_by(Doctor.full_name)))
        schedules = list(await self.session.scalars(
            select(DoctorSchedule).where(DoctorSchedule.active.is_(True))
            .order_by(DoctorSchedule.doctor_id, DoctorSchedule.weekday)))
        return {"branches": branches, "departments": departments, "doctors": doctors,
                "schedules": schedules}

    async def slots_by_ids(self, slot_ids: list[uuid.UUID]) -> dict[uuid.UUID, AppointmentSlot]:
        if not slot_ids:
            return {}
        result = await self.session.scalars(select(AppointmentSlot).where(AppointmentSlot.id.in_(slot_ids)))
        return {slot.id: slot for slot in result}

    async def audit(self, actor: str, action: str, entity: Any, details: dict[str, Any] | None = None) -> None:
        await self.add(AuditLog(actor=actor, action=action, entity_type=type(entity).__name__,
                                entity_id=str(entity.id), details=details or {}))
