import logging
import uuid
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import Settings
from app.core.errors import Conflict
from app.db.base import PMSLog, PMSStatus
from app.repositories import ClinicRepository

logger = logging.getLogger(__name__)


class PMSService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings
        self.repo = ClinicRepository(session)

    @retry(retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
           stop=stop_after_attempt(3), wait=wait_exponential(min=0.2, max=2), reraise=True)
    async def _send(self, operation: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.settings.pms_base_url, timeout=5) as client:
            response = await client.post(f"/ehr/{operation}", json=payload,
                                         headers={"Idempotency-Key": key})
            response.raise_for_status()
            return response.json()

    async def sync(self, operation: str, key: str, appointment_id: uuid.UUID,
                   payload: dict[str, Any] | None = None) -> dict[str, Any]:
        current = await self.repo.pms_log(key, operation)
        if current and current.status == PMSStatus.confirmed:
            return current.response_payload or {}
        log = current or PMSLog(idempotency_key=key, operation=operation,
                                request_payload={"appointment_id": str(appointment_id), **(payload or {})})
        if not current:
            await self.repo.add(log)
        log.attempts += 1
        try:
            result = await self._send(operation, key, log.request_payload)
            log.response_payload = result
            log.status = PMSStatus.confirmed
            log.error = None
            return result
        except Exception as exc:
            log.status = PMSStatus.failed
            log.error = str(exc)[:2000]
            logger.exception("pms_sync_failed", extra={"operation": operation})
            raise Conflict("The clinic system could not confirm the change; no confirmation was given") from exc


async def mock_pms_operation(session: AsyncSession, settings: Settings, operation: str,
                             key: str, appointment_id: uuid.UUID,
                             payload: dict[str, Any]) -> dict[str, Any]:
    import random
    repo = ClinicRepository(session)
    current = await repo.pms_log(key, f"mock_{operation}")
    if current and current.response_payload:
        return current.response_payload
    if random.random() < settings.pms_failure_rate:
        raise Conflict("Simulated PMS failure")
    result = {"confirmed": True, "operation": operation,
              "appointment_id": str(appointment_id), "reference": f"PMS-{str(appointment_id)[:8].upper()}"}
    await repo.add(PMSLog(idempotency_key=key, operation=f"mock_{operation}",
                          request_payload=payload, response_payload=result,
                          status=PMSStatus.confirmed, attempts=1))
    return result
