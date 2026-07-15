import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import clinic, conversations, ehr, retell
from app.core.config import get_settings
from app.core.errors import DomainError
from app.core.logging import configure_logging, request_id_context

settings = get_settings()
configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Arogya Clinic Voice Receptionist", version="1.0.0", lifespan=lifespan)
app.include_router(clinic.router, prefix="/api/v1", tags=["clinic"])
app.include_router(conversations.router, prefix="/api/v1")
app.include_router(ehr.router)
app.include_router(retell.router)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))[:128]
    token = request_id_context.set(request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - started) * 1000:.2f}"
        return response
    finally:
        request_id_context.reset(token)


@app.exception_handler(DomainError)
async def domain_error_handler(_request: Request, exc: DomainError):
    return JSONResponse(status_code=exc.status_code,
                        content={"error": {"code": exc.code, "message": exc.message}})
