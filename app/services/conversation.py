import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import as_utc
from app.core.errors import Conflict, NotFound
from app.db.base import CallbackHistory, CallSession, CallStatus, ConversationState, FollowUpTask
from app.repositories import ClinicRepository
from app.schemas import ConversationPatch


class ConversationService:
    RECOVERY_WINDOW = timedelta(hours=24)

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ClinicRepository(session)

    async def start(self, retell_call_id: str, phone: str, language: str = "en-IN") -> tuple[CallSession, ConversationState, bool]:
        existing = await self.repo.call_by_retell_id(retell_call_id)
        if existing:
            state = await self.repo.state(existing.id)
            return existing, state or await self.repo.add(ConversationState(call_session_id=existing.id)), False
        previous = await self.repo.recoverable_call(phone) if phone else None
        previous_state = await self.repo.state(previous.id) if previous else None
        recover = bool(
            previous and previous_state
            and datetime.now(timezone.utc) - as_utc(previous.updated_at) <= self.RECOVERY_WINDOW
            and previous_state.intent and not (previous_state.collected or {}).get("completed"))
        if not recover:
            previous_state = None
        call = await self.repo.add(CallSession(retell_call_id=retell_call_id, phone=phone,
                                               status=CallStatus.active, language=language,
                                               patient_id=previous.patient_id if recover else None))
        if recover:
            # The unfinished request now lives on the new call; the old one must never be resumed again.
            previous.status = CallStatus.completed
        state = await self.repo.add(ConversationState(
            call_session_id=call.id,
            intent=previous_state.intent if previous_state else None,
            collected=dict(previous_state.collected) if previous_state else {},
            missing_fields=list(previous_state.missing_fields) if previous_state else [],
            last_message=previous_state.last_message if previous_state else None,
        ))
        if previous and previous_state and recover:
            await self.repo.add(CallbackHistory(previous_call_id=previous.id, resumed_call_id=call.id,
                                                recovered_state=previous_state.collected))
        return call, state, recover

    async def patch(self, session_id: uuid.UUID, data: ConversationPatch) -> ConversationState:
        state = await self.repo.state(session_id, lock=True)
        if not state:
            raise NotFound("Conversation state not found")
        if state.version != data.expected_version:
            raise Conflict("Conversation state changed; reload before updating")
        if data.intent is not None:
            state.intent = data.intent
        state.collected = {**state.collected, **data.collected}
        state.missing_fields = data.missing_fields
        if data.last_message is not None:
            state.last_message = data.last_message
        state.version += 1
        await self.session.flush()
        return state

    async def ensure(self, retell_call_id: str, phone: str,
                     language: str = "en-IN") -> tuple[CallSession, ConversationState, bool]:
        """Return the session for a Retell call, creating it if the call_started webhook never arrived."""
        if not retell_call_id:
            raise NotFound("Retell call_id missing from the request")
        call = await self.repo.call_by_retell_id(retell_call_id)
        if not call:
            return await self.start(retell_call_id, phone, language)
        state = await self.repo.state(call.id)
        if not state:
            state = await self.repo.add(ConversationState(call_session_id=call.id))
        recovered = bool(state.intent or state.collected) and not state.collected.get("completed")
        return call, state, recovered

    async def save_progress(self, retell_call_id: str, phone: str, intent: str | None,
                            collected: dict[str, Any], missing_fields: list[str] | None,
                            last_message: str | None) -> ConversationState:
        """Voice-path state write. One live call is the only writer, so no version check is needed."""
        _call, state, _ = await self.ensure(retell_call_id, phone)
        if intent:
            state.intent = intent[:32]
        state.collected = {**(state.collected or {}), **collected}
        if missing_fields is not None:
            state.missing_fields = missing_fields
        if last_message:
            state.last_message = last_message[:4000]
        state.version += 1
        await self.session.flush()
        return state

    async def complete(self, retell_call_id: str, phone: str, outcome: dict[str, Any]) -> None:
        """Mark the call's task as done so a callback does not resume an already-finished request."""
        call, state, _ = await self.ensure(retell_call_id, phone)
        state.collected = {**(state.collected or {}), "completed": True, "outcome": outcome}
        state.missing_fields = []
        state.version += 1
        call.status = CallStatus.completed
        await self.session.flush()

    async def disconnect(self, call_id: str) -> None:
        call = await self.repo.call_by_retell_id(call_id)
        if not call:
            return
        call.ended_at = datetime.now(timezone.utc)
        if call.status in (CallStatus.completed, CallStatus.escalated):
            return
        state = await self.repo.state(call.id)
        unfinished = bool(state and state.intent and not (state.collected or {}).get("completed"))
        # Only a call that hung up mid-request is resumable; anything else is simply finished.
        call.status = CallStatus.disconnected if unfinished else CallStatus.completed

    async def escalate_call(self, retell_call_id: str, phone: str, reason: str,
                            clinical: bool = False) -> FollowUpTask:
        call, _, _ = await self.ensure(retell_call_id, phone)
        return await self.escalate(call.id, reason, clinical)

    async def escalate(self, session_id: uuid.UUID, reason: str, clinical: bool = False) -> FollowUpTask:
        call = await self.session.get(CallSession, session_id)
        if not call:
            raise NotFound("Call session not found")
        call.status = CallStatus.escalated
        task = await self.repo.add(FollowUpTask(call_session_id=call.id, patient_id=call.patient_id,
                                                reason=reason, priority="urgent" if clinical else "normal"))
        await self.repo.audit("voice", "human_follow_up.created", task,
                              {"clinical_concern": clinical})
        return task
