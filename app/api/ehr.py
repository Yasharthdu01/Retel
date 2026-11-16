
from fastapi import APIRouter, Header

from app.api.deps import SessionDep, SettingsDep
from app.schemas import EHRRequest
from app.services.pms import mock_pms_operation

router = APIRouter(prefix="/ehr", tags=["mock-ehr"])


async def operate(operation: str, data: EHRRequest, key: str, session, settings):
    async with session.begin():
        return await mock_pms_operation(session, settings, operation, key,
                                        data.appointment_id, data.payload)


@router.post("/book")
async def book(data: EHRRequest, session: SessionDep, settings: SettingsDep,
               idempotency_key: str = Header(min_length=8)):
    return await operate("book", data, idempotency_key, session, settings)


@router.post("/reschedule")
async def reschedule(data: EHRRequest, session: SessionDep, settings: SettingsDep,
                     idempotency_key: str = Header(min_length=8)):
    return await operate("reschedule", data, idempotency_key, session, settings)


@router.post("/cancel")
async def cancel(data: EHRRequest, session: SessionDep, settings: SettingsDep,
                 idempotency_key: str = Header(min_length=8)):
    return await operate("cancel", data, idempotency_key, session, settings)
