"""Idempotent seed, safe to run on every boot.

1. Creates the demo clinic (departments, branches, doctors, weekly schedules) only if no branch exists.
2. Tops up appointment slots so every active schedule has open slots for the next SLOT_HORIZON_DAYS.
   Existing slots are never touched, so booked appointments and blocked slots survive restarts.
"""
import asyncio
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import AppointmentSlot, Branch, Department, Doctor, DoctorSchedule
from app.db.session import SessionFactory

SLOT_HORIZON_DAYS = 45


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def create_reference_data(session: AsyncSession) -> bool:
    if await session.scalar(select(Branch.id).limit(1)):
        return False
    cardiology = Department(name="Cardiology", description="Heart and vascular care")
    general = Department(name="General Medicine", description="Primary adult care")
    pediatrics = Department(name="Pediatrics", description="Child and adolescent care")
    session.add_all([cardiology, general, pediatrics])
    central = Branch(name="Arogya Central - Connaught Place",
                     address="12 Barakhamba Road, Connaught Place, New Delhi 110001",
                     phone="+911140001001", timezone="Asia/Kolkata")
    south = Branch(name="Arogya South - Saket",
                   address="18 Press Enclave Road, Saket, New Delhi 110017",
                   phone="+911140001002", timezone="Asia/Kolkata")
    session.add_all([central, south])
    await session.flush()
    sharma = Doctor(full_name="Dr. Ananya Sharma", registration_number="DMC-78241",
                    department_id=cardiology.id, appointment_duration_minutes=30)
    mehta = Doctor(full_name="Dr. Rohan Mehta", registration_number="DMC-64518",
                   department_id=general.id, appointment_duration_minutes=20)
    iyer = Doctor(full_name="Dr. Nandini Iyer", registration_number="DMC-81602",
                  department_id=pediatrics.id, appointment_duration_minutes=30)
    session.add_all([sharma, mehta, iyer])
    await session.flush()
    for weekday in range(6):  # Monday to Saturday
        session.add_all([
            DoctorSchedule(doctor_id=sharma.id, branch_id=central.id, weekday=weekday,
                           start_time=time(9), end_time=time(17), lunch_start=time(13), lunch_end=time(14)),
            DoctorSchedule(doctor_id=mehta.id, branch_id=south.id, weekday=weekday,
                           start_time=time(10), end_time=time(18), lunch_start=time(14), lunch_end=time(14, 40)),
            DoctorSchedule(doctor_id=iyer.id, branch_id=central.id if weekday < 3 else south.id, weekday=weekday,
                           start_time=time(9), end_time=time(16), lunch_start=time(12, 30), lunch_end=time(13, 30)),
        ])
    await session.flush()
    return True


def _day_slots(schedule: DoctorSchedule, duration: int, day: date, tz: ZoneInfo):
    cursor = datetime.combine(day, schedule.start_time, tzinfo=tz)
    end = datetime.combine(day, schedule.end_time, tzinfo=tz)
    step = timedelta(minutes=duration)
    while cursor + step <= end:
        slot_end = cursor + step
        overlaps_lunch = bool(
            schedule.lunch_start and schedule.lunch_end
            and cursor.time() < schedule.lunch_end and slot_end.time() > schedule.lunch_start)
        if not overlaps_lunch:
            yield cursor.astimezone(timezone.utc), slot_end.astimezone(timezone.utc)
        cursor = slot_end


async def top_up_slots(session: AsyncSession, horizon_days: int = SLOT_HORIZON_DAYS) -> int:
    schedules = list(await session.scalars(select(DoctorSchedule).where(DoctorSchedule.active.is_(True))))
    if not schedules:
        return 0
    doctors = {d.id: d for d in await session.scalars(select(Doctor).where(Doctor.active.is_(True)))}
    branches = {b.id: b for b in await session.scalars(select(Branch).where(Branch.active.is_(True)))}
    window_start = datetime.now(timezone.utc)
    window_end = window_start + timedelta(days=horizon_days + 1)
    existing = {(row.doctor_id, row.branch_id, _as_utc(row.starts_at)) for row in (await session.execute(
        select(AppointmentSlot.doctor_id, AppointmentSlot.branch_id, AppointmentSlot.starts_at)
        .where(AppointmentSlot.starts_at >= window_start, AppointmentSlot.starts_at < window_end))).all()}
    added = 0
    for schedule in schedules:
        doctor, branch = doctors.get(schedule.doctor_id), branches.get(schedule.branch_id)
        if not doctor or not branch:
            continue
        tz = ZoneInfo(branch.timezone)
        today = datetime.now(tz).date()
        for offset in range(1, horizon_days + 1):
            day = today + timedelta(days=offset)
            if day.weekday() != schedule.weekday:
                continue
            for starts_at, ends_at in _day_slots(schedule, doctor.appointment_duration_minutes, day, tz):
                key = (doctor.id, branch.id, starts_at)
                if key in existing:
                    continue
                session.add(AppointmentSlot(doctor_id=doctor.id, branch_id=branch.id,
                                            starts_at=starts_at, ends_at=ends_at))
                existing.add(key)
                added += 1
    await session.flush()
    return added


async def seed() -> None:
    async with SessionFactory() as session, session.begin():
        created = await create_reference_data(session)
        added = await top_up_slots(session)
    print(f"Seed: reference data {'created' if created else 'already present'}; "
          f"{added} new slots added for the next {SLOT_HORIZON_DAYS} days")


if __name__ == "__main__":
    asyncio.run(seed())
