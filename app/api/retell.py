import json
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import ValidationError

from app.api.deps import SessionDep, SettingsDep
from app.core.security import verify_timestamped_retell_signature
from app.schemas import RetellFunctionRequest, RetellWebhook
from app.services.conversation import ConversationService
from app.voice.dispatcher import ToolFailure, dispatch_tool
from app.voice.prompt import BEGIN_MESSAGE, SYSTEM_PROMPT, tool_schemas

router = APIRouter(prefix="/retell", tags=["retell"])
logger = logging.getLogger(__name__)


def require_signature(raw: bytes, signature: str | None, secret: str) -> None:
    if not verify_timestamped_retell_signature(raw, signature, secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


def parse(raw: bytes, model):
    try:
        return model.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(status_code=400, detail="Malformed Retell payload") from exc


async def run_tool(name: str, args: dict[str, Any], call: dict[str, Any], session, settings) -> dict[str, Any]:
    """One transaction per tool call. A ToolFailure rolls everything back, then is reported as data."""
    try:
        async with session.begin():
            return await dispatch_tool(name, args, session, settings, call)
    except ToolFailure as failure:
        logger.info("voice_tool_rejected", extra={"tool": name, "code": failure.payload["error"]["code"]})
        return failure.payload


@router.get("/llm-config")
async def llm_config(settings: SettingsDep):
    """Prompt and tool definitions to paste into (or provision onto) the Retell LLM."""
    return {"model": settings.openai_model, "general_prompt": SYSTEM_PROMPT,
            "general_tools": tool_schemas(settings.public_base_url), "begin_message": BEGIN_MESSAGE}


@router.post("/webhook")
async def webhook(request: Request, session: SessionDep, settings: SettingsDep,
                  x_retell_signature: str | None = Header(default=None)):
    raw = await request.body()
    require_signature(raw, x_retell_signature, settings.retell_webhook_secret)
    event = parse(raw, RetellWebhook)
    call_id = str(event.call.get("call_id", ""))
    phone = str(event.call.get("from_number", ""))
    if event.event == "tool_call" and event.tool_call:
        return {"result": await run_tool(event.tool_call.name, dict(event.tool_call.arguments),
                                         event.call, session, settings)}
    async with session.begin():
        conversations = ConversationService(session)
        if event.event in {"call_started", "call_inbound"} and call_id:
            call, _state, recovered = await conversations.start(call_id, phone,
                                                                 event.call.get("language", "en-IN"))
            return {"received": True, "session_id": str(call.id), "recovered": recovered}
        if event.event in {"call_ended", "call_disconnected"} and call_id:
            await conversations.disconnect(call_id)
            return {"received": True}
        return {"received": True, "ignored": True}


@router.post("/functions")
async def custom_function(request: Request, session: SessionDep, settings: SettingsDep,
                          x_retell_signature: str | None = Header(default=None)):
    raw = await request.body()
    require_signature(raw, x_retell_signature, settings.retell_webhook_secret)
    function = parse(raw, RetellFunctionRequest)
    return {"result": await run_tool(function.name, dict(function.args), function.call, session, settings)}
