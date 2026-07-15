import logging
from contextvars import ContextVar

from pythonjsonlogger.json import JsonFormatter

request_id_context: ContextVar[str] = ContextVar("request_id", default="")


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_context.get()
        return True


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(ContextFilter())
    handler.setFormatter(JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s %(request_id)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
