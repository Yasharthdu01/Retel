import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from sqlalchemy import select, text

from app.api.deps import SessionDep, SettingsDep
from app.db.base import Branch, Doctor, Patient
from app.repositories import ClinicRepository
from app.schemas import (
    AppointmentRead, AvailabilityQuery, BookingRequest, BranchCreate, BranchRead,
    CancellationRequest, DoctorCreate, DoctorRead, PatientCreate, PatientRead,
    RescheduleRequest, SlotRead,
)
from app.services.pms import PMSService
from app.services.scheduling import SchedulingService

router = APIRouter()


@router.get("/health")
async def health(session: SessionDep) -> dict:
    await session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok", "time": datetime.now(timezone.utc)}


@router.post("/patients", response_model=PatientRead, status_code=201)
async def create_patient(data: PatientCreate, session: SessionDep):
    async with session.begin():
        return await ClinicRepository(session).add(Patient(**data.model_dump()))


@router.get("/patients/{patient_id}", response_model=PatientRead)
async def get_patient(patient_id: uuid.UUID, session: SessionDep):
    from app.core.errors import NotFound
    patient = await ClinicRepository(session).patient(patient_id)
    if not patient:
        raise NotFound("Patient not found")
    return patient


@router.get("/patients/lookup/phone")
async def returning_patient_lookup(session: SessionDep, phone: str = Query(pattern=r"^\+?[1-9]\d{7,14}$")):
    repo = ClinicRepository(session)
    patients = await repo.patients_by_phone(phone)
    return [{"patient": PatientRead.model_validate(p),
             "appointments": [AppointmentRead.model_validate(a) for a in await repo.appointment_history(p.id)]}
            for p in patients]


@router.post("/branches", response_model=BranchRead, status_code=201)
async def create_branch(data: BranchCreate, session: SessionDep):
    async with session.begin():
        return await ClinicRepository(session).add(Branch(**data.model_dump()))


@router.get("/branches", response_model=list[BranchRead])
async def list_branches(session: SessionDep):
    return list(await session.scalars(select(Branch).where(Branch.active.is_(True)).order_by(Branch.name)))


@router.post("/doctors", response_model=DoctorRead, status_code=201)
async def create_doctor(data: DoctorCreate, session: SessionDep):
    async with session.begin():
        return await ClinicRepository(session).add(Doctor(**data.model_dump()))


@router.get("/doctors", response_model=list[DoctorRead])
async def list_doctors(session: SessionDep, department_id: uuid.UUID | None = None):
    query = select(Doctor).where(Doctor.active.is_(True))
    if department_id:
        query = query.where(Doctor.department_id == department_id)
    return list(await session.scalars(query.order_by(Doctor.full_name)))


@router.post("/availability", response_model=list[SlotRead])
async def availability(data: AvailabilityQuery, session: SessionDep, settings: SettingsDep):
    return await SchedulingService(session, settings).availability(data)


@router.post("/appointments", response_model=AppointmentRead, status_code=201)
async def book(data: BookingRequest, session: SessionDep, settings: SettingsDep):
    async with session.begin():
        appointment = await SchedulingService(session, settings).book(data)
        result = await PMSService(session, settings).sync("book", data.idempotency_key,
                                                          appointment.id, data.model_dump(mode="json"))
        appointment.pms_reference = result.get("reference")
        return appointment


@router.post("/appointments/{appointment_id}/reschedule", response_model=AppointmentRead)
async def reschedule(appointment_id: uuid.UUID, data: RescheduleRequest,
                     session: SessionDep, settings: SettingsDep):
    async with session.begin():
        appointment = await SchedulingService(session, settings).reschedule(appointment_id, data)
        await PMSService(session, settings).sync("reschedule", data.idempotency_key,
                                                 appointment.id, data.model_dump(mode="json"))
        return appointment


@router.post("/appointments/{appointment_id}/cancel", response_model=AppointmentRead)
async def cancel(appointment_id: uuid.UUID, data: CancellationRequest,
                 session: SessionDep, settings: SettingsDep):
    async with session.begin():
        appointment = await SchedulingService(session, settings).cancel(appointment_id, data)
        await PMSService(session, settings).sync("cancel", data.idempotency_key,
                                                 appointment.id, data.model_dump(mode="json"))
        return appointment
