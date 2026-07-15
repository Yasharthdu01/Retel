import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import DomainError
from app.repositories import ClinicRepository
from app.schemas import AvailabilityQuery, BookingRequest, CancellationRequest, RescheduleRequest
from app.services.conversation import ConversationService
from app.services.pms import PMSService
from app.services.scheduling import SchedulingService


async def dispatch_tool(name: str, args: dict, session: AsyncSession, settings: Settings) -> dict:
    try:
        if name == "lookup_patient":
            repo = ClinicRepository(session)
            patients = await repo.patients_by_phone(args["phone"])
            return {"matches": [{"id": str(p.id), "full_name": p.full_name,
                                  "preferred_language": p.preferred_language,
                                  "appointments": [{"id": str(a.id), "status": a.status.value,
                                                    "doctor_id": str(a.doctor_id), "branch_id": str(a.branch_id)}
                                                   for a in await repo.appointment_history(p.id)]}
                                 for p in patients], "requires_name_disambiguation": len(patients) > 1}
        if name == "find_availability":
            slots = await SchedulingService(session, settings).availability(AvailabilityQuery(**args))
            return {"slots": [{"id": str(s.id), "doctor_id": str(s.doctor_id),
                               "branch_id": str(s.branch_id), "starts_at": s.starts_at.isoformat(),
                               "ends_at": s.ends_at.isoformat()} for s in slots], "live_query": True}
        if name == "book_appointment":
            data = BookingRequest(**args)
            appointment = await SchedulingService(session, settings).book(data)
            pms = await PMSService(session, settings).sync("book", data.idempotency_key,
                                                            appointment.id, data.model_dump(mode="json"))
            appointment.pms_reference = pms.get("reference")
            return {"confirmed": True, "appointment_id": str(appointment.id), "pms": pms}
        if name == "reschedule_appointment":
            appointment_id = uuid.UUID(args.pop("appointment_id"))
            data = RescheduleRequest(**args)
            appointment = await SchedulingService(session, settings).reschedule(appointment_id, data)
            pms = await PMSService(session, settings).sync("reschedule", data.idempotency_key,
                                                            appointment.id, data.model_dump(mode="json"))
            return {"confirmed": True, "appointment_id": str(appointment.id), "pms": pms}
        if name == "cancel_appointment":
            appointment_id = uuid.UUID(args.pop("appointment_id"))
            data = CancellationRequest(**args)
            appointment = await SchedulingService(session, settings).cancel(appointment_id, data)
            pms = await PMSService(session, settings).sync("cancel", data.idempotency_key,
                                                            appointment.id, data.model_dump(mode="json"))
            return {"confirmed": True, "appointment_id": str(appointment.id), "pms": pms}
        if name == "escalate_to_human":
            task = await ConversationService(session).escalate(uuid.UUID(args["session_id"]),
                                                               args["reason"], args.get("clinical_concern", False))
            return {"follow_up_created": True, "task_id": str(task.id),
                    "message": "Clinic staff will follow up; this is not an immediate transfer."}
        return {"error": {"code": "unknown_tool", "message": f"Unknown tool: {name}"}}
    except DomainError as exc:
        return {"error": {"code": exc.code, "message": exc.message}}
