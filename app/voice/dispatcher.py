import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import DomainError, NotFound
from app.db.base import Patient
from app.repositories import ClinicRepository
from app.schemas import (
    AvailabilityQuery, BookingRequest, CancellationRequest, PatientCreate, RescheduleRequest,
)
from app.services.conversation import ConversationService
from app.services.pms import PMSService
from app.services.scheduling import SchedulingService
from app.voice.catalog import Catalog, speakable_time

logger = logging.getLogger(__name__)


class ToolFailure(Exception):
    """Raised so the caller's transaction rolls back; `payload` is still returned to Retell as data.

    Returning an error dict from inside `session.begin()` would commit whatever the tool had already
    written (for example a booked slot whose PMS confirmation failed). Raising guarantees rollback.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.payload = {"error": {"code": code, "message": message}}


def _uuid(args: dict[str, Any], key: str) -> uuid.UUID:
    value = args.get(key)
    if not value:
        raise ValueError(f"{key} is required")
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise ValueError(f"{key} must be an id returned by a previous tool, not a description") from exc


def _key(args: dict[str, Any], call_id: str, operation: str, target: str) -> str:
    # The model rarely produces good idempotency keys. Deriving one from the call makes Retell's
    # retries of the same function call safe while still letting the model supply its own.
    supplied = args.get("idempotency_key")
    if supplied and len(str(supplied)) >= 8:
        return str(supplied)
    return f"{call_id or 'no-call'}:{operation}:{target}"[:128]


async def _patients(repo: ClinicRepository, catalog: Catalog, phone: str) -> list[dict[str, Any]]:
    result = []
    for patient in await repo.patients_by_phone(phone):
        history = await repo.appointment_history(patient.id)
        slots = await repo.slots_by_ids([a.slot_id for a in history])
        appointments = [catalog.appointment(a, slots.get(a.slot_id)) for a in history]
        result.append({"patient_id": str(patient.id), "full_name": patient.full_name,
                       "preferred_language": patient.preferred_language,
                       "upcoming_appointments": [a for a in appointments
                                                 if a["status"] == "booked" and a.get("is_upcoming")],
                       "past_appointment_count": sum(1 for a in appointments if not a.get("is_upcoming"))})
    return result


async def dispatch_tool(name: str, args: dict[str, Any], session: AsyncSession, settings: Settings,
                        call: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        return await _dispatch(name, dict(args or {}), session, settings, call or {})
    except ToolFailure:
        raise
    except DomainError as exc:
        raise ToolFailure(exc.code, exc.message) from exc
    except ValidationError as exc:
        details = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:5])
        raise ToolFailure("invalid_arguments", details) from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise ToolFailure("invalid_arguments", str(exc)) from exc
    except Exception as exc:  # never let a tool call surface as an HTTP 500 to Retell
        logger.exception("voice_tool_failed", extra={"tool": name})
        raise ToolFailure("internal_error", "The clinic system had a problem; offer a staff callback") from exc


async def _dispatch(name: str, args: dict[str, Any], session: AsyncSession, settings: Settings,
                    call: dict[str, Any]) -> dict[str, Any]:
    call_id = str(call.get("call_id") or "")
    caller = str(call.get("from_number") or "")
    repo = ClinicRepository(session)
    conversations = ConversationService(session)
    catalog = await Catalog.load(session, settings.clinic_timezone)

    if name == "get_call_context":
        _call, state, recovered = await conversations.ensure(call_id, caller)
        now = datetime.now(timezone.utc)
        local = now.astimezone(ZoneInfo(settings.clinic_timezone))
        return {"now_local": local.isoformat(), "today": speakable_time(now, settings.clinic_timezone).split(",")[0],
                "timezone": settings.clinic_timezone, "caller_phone": caller or None,
                "known_patients": await _patients(repo, catalog, caller) if caller else [],
                "resumed_previous_call": recovered,
                "saved_progress": {"intent": state.intent, "collected": state.collected,
                                   "missing_fields": state.missing_fields,
                                   "last_message": state.last_message} if recovered else None}

    if name == "list_clinic_directory":
        return catalog.directory()

    if name == "lookup_patient":
        phone = str(args.get("phone") or caller)
        if not phone:
            raise ValueError("phone is required when the caller number is unknown")
        patients = await _patients(repo, catalog, phone)
        return {"matches": patients, "requires_name_disambiguation": len(patients) > 1}

    if name == "register_patient":
        data = PatientCreate(full_name=args.get("full_name", ""), phone=args.get("phone") or caller,
                             date_of_birth=args.get("date_of_birth"),
                             preferred_language=args.get("preferred_language") or "en-IN")
        patient = await repo.patient_by_phone_and_name(data.phone, data.full_name)
        created = patient is None
        if created:
            patient = await repo.add(Patient(**data.model_dump()))
            await repo.audit("voice", "patient.registered", patient)
        if call_id:
            call_session, _, _ = await conversations.ensure(call_id, caller)
            call_session.patient_id = patient.id
        return {"patient_id": str(patient.id), "full_name": patient.full_name, "created": created}

    if name == "find_availability":
        query = AvailabilityQuery(**{k: v for k, v in args.items() if v not in (None, "", [])})
        slots = await SchedulingService(session, settings).availability(query)
        return {"slots": [catalog.slot(s) for s in slots], "live_query": True,
                "note": None if slots else "No open slots in that window; try a wider range or another branch."}

    if name == "book_appointment":
        slot_id = _uuid(args, "slot_id")
        data = BookingRequest(patient_id=_uuid(args, "patient_id"),
                              patient_full_name=args.get("patient_full_name", ""), slot_id=slot_id,
                              reason=args.get("reason"), source="voice",
                              idempotency_key=_key(args, call_id, "book", str(slot_id)))
        appointment = await SchedulingService(session, settings).book(data)
        pms = await PMSService(session, settings).sync("book", data.idempotency_key, appointment.id,
                                                        data.model_dump(mode="json"))
        appointment.pms_reference = pms.get("reference")
        slot = (await repo.slots_by_ids([appointment.slot_id])).get(appointment.slot_id)
        result = {"confirmed": True, **catalog.appointment(appointment, slot)}
        if call_id:
            await conversations.complete(call_id, caller, {"booked": result["appointment_id"]})
        return result

    if name == "reschedule_appointment":
        appointment_id = _uuid(args, "appointment_id")
        new_slot_id = _uuid(args, "new_slot_id")
        data = RescheduleRequest(new_slot_id=new_slot_id,
                                 idempotency_key=_key(args, call_id, "reschedule", f"{appointment_id}:{new_slot_id}"))
        appointment = await SchedulingService(session, settings).reschedule(appointment_id, data)
        await PMSService(session, settings).sync("reschedule", data.idempotency_key, appointment.id,
                                                 data.model_dump(mode="json"))
        slot = (await repo.slots_by_ids([appointment.slot_id])).get(appointment.slot_id)
        result = {"confirmed": True, **catalog.appointment(appointment, slot)}
        if call_id:
            await conversations.complete(call_id, caller, {"rescheduled": result["appointment_id"]})
        return result

    if name == "cancel_appointment":
        appointment_id = _uuid(args, "appointment_id")
        data = CancellationRequest(reason=args.get("reason") or "Cancelled by caller",
                                   idempotency_key=_key(args, call_id, "cancel", str(appointment_id)))
        appointment = await SchedulingService(session, settings).cancel(appointment_id, data)
        await PMSService(session, settings).sync("cancel", data.idempotency_key, appointment.id,
                                                 data.model_dump(mode="json"))
        slot = (await repo.slots_by_ids([appointment.slot_id])).get(appointment.slot_id)
        result = {"confirmed": True, **catalog.appointment(appointment, slot)}
        if call_id:
            await conversations.complete(call_id, caller, {"cancelled": result["appointment_id"]})
        return result

    if name == "save_progress":
        state = await conversations.save_progress(
            call_id, caller, args.get("intent"), dict(args.get("collected") or {}),
            args.get("missing_fields"), args.get("last_message"))
        return {"saved": True, "version": state.version}

    if name == "escalate_to_human":
        reason = str(args.get("reason") or "Caller asked for clinic staff")
        clinical = bool(args.get("clinical_concern", False))
        if args.get("session_id"):  # REST/legacy callers that already know the internal session id
            task = await conversations.escalate(_uuid(args, "session_id"), reason, clinical)
        else:
            task = await conversations.escalate_call(call_id, caller, reason, clinical)
        return {"follow_up_created": True, "task_id": str(task.id),
                "priority": "urgent" if clinical else "normal",
                "message": "Clinic staff will call back; this is not a live transfer."}

    raise NotFound(f"Unknown tool: {name}")
