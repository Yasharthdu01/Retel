import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

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
        previous = await self.repo.recoverable_call(phone)
        recover = bool(previous and datetime.now(timezone.utc) - previous.updated_at <= self.RECOVERY_WINDOW)
        call = await self.repo.add(CallSession(retell_call_id=retell_call_id, phone=phone,
                                               status=CallStatus.active, language=language,
                                               patient_id=previous.patient_id if recover else None))
        previous_state = await self.repo.state(previous.id) if recover and previous else None
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

    async def disconnect(self, call_id: str) -> None:
        call = await self.repo.call_by_retell_id(call_id)
        if call:
            call.status = CallStatus.disconnected
            call.ended_at = datetime.now(timezone.utc)

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
