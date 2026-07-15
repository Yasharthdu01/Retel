import asyncio
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db.base import AppointmentSlot, Branch, Department, Doctor, DoctorSchedule
from app.db.session import SessionFactory


async def seed() -> None:
    async with SessionFactory() as session, session.begin():
        if await session.scalar(select(Branch.id).limit(1)):
            print("Seed skipped: clinic data already exists")
            return
        cardiology = Department(name="Cardiology", description="Heart and vascular care")
        general = Department(name="General Medicine", description="Primary adult care")
        pediatrics = Department(name="Pediatrics", description="Child and adolescent care")
        session.add_all([cardiology, general, pediatrics])
        await session.flush()
        central = Branch(name="Arogya Central - Connaught Place",
                         address="12 Barakhamba Road, Connaught Place, New Delhi 110001",
                         phone="+911140001001", timezone="Asia/Kolkata")
        south = Branch(name="Arogya South - Saket",
                       address="18 Press Enclave Road, Saket, New Delhi 110017",
                       phone="+911140001002", timezone="Asia/Kolkata")
        session.add_all([central, south])
        await session.flush()
        doctors = [
            Doctor(full_name="Dr. Ananya Sharma", registration_number="DMC-78241",
                   department_id=cardiology.id, appointment_duration_minutes=30),
            Doctor(full_name="Dr. Rohan Mehta", registration_number="DMC-64518",
                   department_id=general.id, appointment_duration_minutes=20),
            Doctor(full_name="Dr. Nandini Iyer", registration_number="DMC-81602",
                   department_id=pediatrics.id, appointment_duration_minutes=30),
        ]
        session.add_all(doctors)
        await session.flush()
        schedules = []
        for weekday in range(6):
            schedules.extend([
                DoctorSchedule(doctor_id=doctors[0].id, branch_id=central.id, weekday=weekday,
                               start_time=time(9), end_time=time(17), lunch_start=time(13), lunch_end=time(14)),
                DoctorSchedule(doctor_id=doctors[1].id, branch_id=south.id, weekday=weekday,
                               start_time=time(10), end_time=time(18), lunch_start=time(14), lunch_end=time(14, 40)),
                DoctorSchedule(doctor_id=doctors[2].id,
                               branch_id=central.id if weekday < 3 else south.id, weekday=weekday,
                               start_time=time(9), end_time=time(16), lunch_start=time(12, 30), lunch_end=time(13, 30)),
            ])
        session.add_all(schedules)
        await session.flush()
        tz = ZoneInfo("Asia/Kolkata")
        today = datetime.now(tz).date()
        for day_offset in range(1, 46):
            day = today + timedelta(days=day_offset)
            for schedule in schedules:
                if day.weekday() != schedule.weekday:
                    continue
                doctor = next(d for d in doctors if d.id == schedule.doctor_id)
                cursor = datetime.combine(day, schedule.start_time, tzinfo=tz)
                end = datetime.combine(day, schedule.end_time, tzinfo=tz)
                while cursor + timedelta(minutes=doctor.appointment_duration_minutes) <= end:
                    cursor_time = cursor.timetz().replace(tzinfo=None)
                    in_lunch = bool(schedule.lunch_start and schedule.lunch_end and
                                    schedule.lunch_start <= cursor_time < schedule.lunch_end)
                    slot_end = cursor + timedelta(minutes=doctor.appointment_duration_minutes)
                    crosses_lunch = bool(schedule.lunch_start and schedule.lunch_end and
                                         cursor_time < schedule.lunch_start < slot_end.timetz().replace(tzinfo=None))
                    if not in_lunch and not crosses_lunch:
                        session.add(AppointmentSlot(doctor_id=doctor.id, branch_id=schedule.branch_id,
                                                    starts_at=cursor.astimezone(timezone.utc),
                                                    ends_at=slot_end.astimezone(timezone.utc)))
                    cursor = slot_end
        print("Seeded two branches, three doctors, schedules, and 45 days of live slots")


if __name__ == "__main__":
    asyncio.run(seed())
