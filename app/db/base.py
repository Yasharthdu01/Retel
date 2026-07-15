import enum
import uuid
from datetime import date, datetime, time, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, ForeignKey, Index, Integer, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON}


class UUIDMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AppointmentStatus(str, enum.Enum):
    booked = "booked"
    cancelled = "cancelled"
    completed = "completed"
    no_show = "no_show"


class CallStatus(str, enum.Enum):
    active = "active"
    disconnected = "disconnected"
    completed = "completed"
    escalated = "escalated"


class PMSStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    failed = "failed"


class Branch(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "branches"
    name: Mapped[str] = mapped_column(String(120), unique=True)
    address: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    phone: Mapped[str] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Department(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "departments"
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str | None] = mapped_column(Text)


class Doctor(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "doctors"
    full_name: Mapped[str] = mapped_column(String(160), index=True)
    registration_number: Mapped[str] = mapped_column(String(64), unique=True)
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("departments.id"), index=True)
    appointment_duration_minutes: Mapped[int] = mapped_column(Integer, default=30)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    department: Mapped[Department] = relationship()


class DoctorSchedule(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "doctor_schedules"
    __table_args__ = (UniqueConstraint("doctor_id", "branch_id", "weekday", name="uq_schedule_day"),)
    doctor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doctors.id"), index=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), index=True)
    weekday: Mapped[int] = mapped_column(Integer)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    lunch_start: Mapped[time | None] = mapped_column(Time)
    lunch_end: Mapped[time | None] = mapped_column(Time)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class AppointmentSlot(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "appointment_slots"
    __table_args__ = (
        UniqueConstraint("doctor_id", "branch_id", "starts_at", name="uq_slot_doctor_branch_start"),
        Index("ix_slot_availability", "branch_id", "doctor_id", "starts_at", "is_booked"),
    )
    doctor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doctors.id"), index=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_booked: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked_reason: Mapped[str | None] = mapped_column(String(200))


class Patient(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "patients"
    __table_args__ = (Index("ix_patient_phone_name", "phone", "full_name"),)
    full_name: Mapped[str] = mapped_column(String(160), index=True)
    phone: Mapped[str] = mapped_column(String(32), index=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    preferred_language: Mapped[str] = mapped_column(String(16), default="en-IN")


class Appointment(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "appointments"
    __table_args__ = (Index("ix_appointment_patient_status", "patient_id", "status"),)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), index=True)
    doctor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doctors.id"), index=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), index=True)
    slot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("appointment_slots.id"), unique=True)
    status: Mapped[AppointmentStatus] = mapped_column(Enum(AppointmentStatus), default=AppointmentStatus.booked)
    reason: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), default="voice")
    pms_reference: Mapped[str | None] = mapped_column(String(100))
    patient: Mapped[Patient] = relationship()
    slot: Mapped[AppointmentSlot] = relationship()


class CallSession(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "call_sessions"
    retell_call_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    phone: Mapped[str] = mapped_column(String(32), index=True)
    patient_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("patients.id"))
    status: Mapped[CallStatus] = mapped_column(Enum(CallStatus), default=CallStatus.active)
    language: Mapped[str] = mapped_column(String(16), default="en-IN")
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConversationState(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "conversation_states"
    call_session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("call_sessions.id"), unique=True)
    intent: Mapped[str | None] = mapped_column(String(32))
    collected: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    missing_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    last_message: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)


class CallbackHistory(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "callback_history"
    previous_call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("call_sessions.id"), index=True)
    resumed_call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("call_sessions.id"), index=True)
    recovered_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class PMSLog(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "pms_logs"
    __table_args__ = (UniqueConstraint("idempotency_key", "operation", name="uq_pms_idempotency_operation"),)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    operation: Mapped[str] = mapped_column(String(32))
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[PMSStatus] = mapped_column(Enum(PMSStatus), default=PMSStatus.pending)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base, UUIDMixin):
    __tablename__ = "audit_logs"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class FollowUpTask(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "follow_up_tasks"
    call_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("call_sessions.id"), index=True)
    patient_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("patients.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
