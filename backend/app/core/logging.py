"""JSON logs with an allow-list of fields and PII masking (SEC-08, SC-64).

Answer text, transcripts, resume content, code, prompts and contact details must never reach app logs.
Call sites pass structured fields via `extra={"fields": {...}}`; anything outside ALLOWED_FIELDS is dropped.
"""
import json
import logging
import re
import sys
from datetime import UTC, datetime

ALLOWED_FIELDS = frozenset({
    "request_id", "user_id", "role", "company_id", "application_id", "posting_id", "question_id", "job_id",
    "job_kind", "lane", "status", "status_code", "method", "path", "latency_ms", "event", "error_code",
    "attempt", "run_no", "model", "count", "seq", "reason", "duration_ms",
    # ai gateway (SEC-08: counts and timings only, never prompt or output text)
    "task", "trace_id", "prompt_tokens", "completion_tokens", "queue_ms", "llm_calls", "preempted",
})
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_PHONE = re.compile(r"01[016789]-?\d{3,4}-?\d{4}")


def mask(text: str) -> str:
    return _PHONE.sub("[phone]", _EMAIL.sub("[email]", text))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": mask(record.getMessage()),
        }
        fields = getattr(record, "fields", None) or {}
        out.update({k: v for k, v in fields.items() if k in ALLOWED_FIELDS})
        if record.exc_info:
            # Exception type only: messages can embed request data.
            out["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(out, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Library loggers that can echo request bodies or SQL parameters stay quiet.
    for noisy in ("sqlalchemy.engine", "aiortc", "aioice", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"fields": {"event": event, **fields}})
