import pytest

from app.services.conversation import ConversationService


@pytest.mark.asyncio
async def test_dropped_call_recovers_state(session):
    service = ConversationService(session)
    first, state, recovered = await service.start("call-1", "+919888888888")
    assert not recovered
    state.intent = "booking"
    state.collected = {"department": "cardiology", "patient_name": "Ravi Kumar"}
    await service.disconnect("call-1")
    await session.flush()
    second, new_state, recovered = await service.start("call-2", "+919888888888")
    assert recovered
    assert second.id != first.id
    assert new_state.collected["patient_name"] == "Ravi Kumar"
