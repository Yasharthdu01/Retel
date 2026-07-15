import uuid

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.schemas import ConversationPatch
from app.services.conversation import ConversationService

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("/{session_id}/state")
async def state(session_id: uuid.UUID, session: SessionDep):
    from app.core.errors import NotFound
    result = await ConversationService(session).repo.state(session_id)
    if not result:
        raise NotFound("Conversation state not found")
    return result


@router.patch("/{session_id}/state")
async def patch_state(session_id: uuid.UUID, data: ConversationPatch, session: SessionDep):
    async with session.begin():
        return await ConversationService(session).patch(session_id, data)


@router.get("/callbacks/{phone}")
async def callback_lookup(phone: str, session: SessionDep):
    call = await ConversationService(session).repo.recoverable_call(phone)
    if not call:
        return {"recoverable": False}
    state = await ConversationService(session).repo.state(call.id)
    return {"recoverable": True, "previous_call_id": str(call.id), "state": state}
