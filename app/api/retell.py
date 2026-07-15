import json

from fastapi import APIRouter, Header, Request

from app.api.deps import SessionDep, SettingsDep
from app.core.errors import DomainError
from app.core.security import verify_timestamped_retell_signature
from app.schemas import RetellFunctionRequest, RetellWebhook
from app.services.conversation import ConversationService
from app.voice.dispatcher import dispatch_tool
from app.voice.prompt import SYSTEM_PROMPT, TOOL_SCHEMAS

router = APIRouter(prefix="/retell", tags=["retell"])


def require_signature(raw: bytes, signature: str | None, secret: str) -> None:
    if not verify_timestamped_retell_signature(raw, signature, secret):
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


@router.get("/llm-config")
async def llm_config(settings: SettingsDep):
    return {"model": settings.openai_model, "general_prompt": SYSTEM_PROMPT,
            "general_tools": TOOL_SCHEMAS, "begin_message": "Namaste! Arogya Clinic. How may I help you?"}


@router.post("/webhook")
async def webhook(request: Request, session: SessionDep, settings: SettingsDep,
                  x_retell_signature: str | None = Header(default=None)):
    raw = await request.body()
    require_signature(raw, x_retell_signature, settings.retell_webhook_secret)
    event = RetellWebhook.model_validate(json.loads(raw))
    call_id = str(event.call.get("call_id", ""))
    phone = str(event.call.get("from_number", ""))
    async with session.begin():
        conversations = ConversationService(session)
        if event.event in {"call_started", "call_inbound"}:
            call, state, recovered = await conversations.start(call_id, phone,
                                                                event.call.get("language", "en-IN"))
            return {"received": True, "session_id": str(call.id), "recovered": recovered,
                    "conversation_state": state.collected}
        if event.event in {"call_ended", "call_disconnected"}:
            await conversations.disconnect(call_id)
            return {"received": True}
        if event.event == "tool_call" and event.tool_call:
            result = await dispatch_tool(event.tool_call.name, dict(event.tool_call.arguments), session, settings)
            return {"result": result}
        return {"received": True, "ignored": True}


@router.post("/functions")
async def custom_function(request: Request, session: SessionDep, settings: SettingsDep,
                          x_retell_signature: str | None = Header(default=None)):
    raw = await request.body()
    require_signature(raw, x_retell_signature, settings.retell_webhook_secret)
    function = RetellFunctionRequest.model_validate(json.loads(raw))
    async with session.begin():
        result = await dispatch_tool(function.name, dict(function.args), session, settings)
    return {"result": result}
