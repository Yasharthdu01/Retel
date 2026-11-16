import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PatientCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=160)
    phone: str = Field(pattern=r"^\+?[1-9]\d{7,14}$")
    date_of_birth: date | None = None
    preferred_language: str = Field(default="en-IN", max_length=16)

    @field_validator("full_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return " ".join(value.split()).title()


class PatientRead(PatientCreate, ORMModel):
    id: uuid.UUID
    created_at: datetime


class BranchCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    address: str = Field(min_length=5)
    timezone: str = "Asia/Kolkata"
    phone: str = Field(pattern=r"^\+?[1-9]\d{7,14}$")


class BranchRead(BranchCreate, ORMModel):
    id: uuid.UUID
    active: bool


class DoctorCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=160)
    registration_number: str = Field(min_length=3, max_length=64)
    department_id: uuid.UUID
    appointment_duration_minutes: int = Field(default=30, ge=10, le=180)


class DoctorRead(DoctorCreate, ORMModel):
    id: uuid.UUID
    active: bool


class AvailabilityQuery(BaseModel):
    doctor_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    branch_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    department_id: uuid.UUID | None = None
    from_time: datetime
    to_time: datetime
    limit: int = Field(default=10, ge=1, le=50)

    @field_validator("from_time", "to_time")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timezone offset is required")
        return value


class SlotRead(ORMModel):
    id: uuid.UUID
    doctor_id: uuid.UUID
    branch_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime


class BookingRequest(BaseModel):
    patient_id: uuid.UUID
    patient_full_name: str = Field(min_length=2, max_length=160)
    slot_id: uuid.UUID
    reason: str | None = Field(default=None, max_length=1000)
    source: Literal["voice", "web", "staff"] = "voice"
    idempotency_key: str = Field(min_length=8, max_length=128)


class RescheduleRequest(BaseModel):
    new_slot_id: uuid.UUID
    idempotency_key: str = Field(min_length=8, max_length=128)


class CancellationRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=128)


class AppointmentRead(ORMModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    doctor_id: uuid.UUID
    branch_id: uuid.UUID
    slot_id: uuid.UUID
    status: str
    reason: str | None
    pms_reference: str | None


class ConversationPatch(BaseModel):
    intent: str | None = Field(default=None, max_length=32)
    collected: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    last_message: str | None = Field(default=None, max_length=4000)
    expected_version: int = Field(ge=1)


class EHRRequest(BaseModel):
    appointment_id: uuid.UUID
    payload: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class RetellWebhook(BaseModel):
    event: str
    call: dict[str, Any]
    tool_call: ToolCall | None = None


class RetellFunctionRequest(BaseModel):
    name: str
    call: dict[str, Any] = Field(default_factory=dict)
    args: dict[str, Any] = Field(default_factory=dict)
