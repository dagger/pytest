"""OpenTelemetry logging handler for pytest.

Captures Python logging records and forwards them to OpenTelemetry
as log records, associating them with the current trace context.

Uses Dagger's stdio conventions:
- stdio.stream = 1 for stdout
- stdio.stream = 2 for stderr
- stdio.eof = true to mark end of stream
"""

import logging
import time
from typing import Optional

from opentelemetry import context, trace
from opentelemetry._logs import SeverityNumber
from opentelemetry.trace import Span

# LogRecord location changed between OpenTelemetry versions
try:
    from opentelemetry.sdk._logs import LogRecord
except ImportError:
    from opentelemetry.sdk._logs._internal import LogRecord

from pytest_otel.config import get_logger

# Dagger stdio stream constants (matches telemetry/attrs.go)
STDIO_STREAM_STDOUT = 1
STDIO_STREAM_STDERR = 2
STDIO_STREAM_ATTR = "stdio.stream"
STDIO_EOF_ATTR = "stdio.eof"

# Mapping from Python logging levels to OpenTelemetry severity
_SEVERITY_MAP = {
    logging.DEBUG: SeverityNumber.DEBUG,
    logging.INFO: SeverityNumber.INFO,
    logging.WARNING: SeverityNumber.WARN,
    logging.ERROR: SeverityNumber.ERROR,
    logging.CRITICAL: SeverityNumber.FATAL,
}


def _get_severity(level: int) -> SeverityNumber:
    """Map Python log level to OpenTelemetry severity."""
    if level >= logging.CRITICAL:
        return SeverityNumber.FATAL
    if level >= logging.ERROR:
        return SeverityNumber.ERROR
    if level >= logging.WARNING:
        return SeverityNumber.WARN
    if level >= logging.INFO:
        return SeverityNumber.INFO
    return SeverityNumber.DEBUG


class OtelLogHandler(logging.Handler):
    """Logging handler that forwards logs to OpenTelemetry.

    This handler captures Python logging records and:
    1. Adds them as events on the current span (so logs appear in trace view)
    2. Emits them as OpenTelemetry log records for the logs pipeline

    This dual approach ensures logs are visible both when expanding a trace
    and in dedicated log views.
    """

    def __init__(self, level: int = logging.NOTSET) -> None:
        super().__init__(level)
        self._otel_logger: Optional[object] = None

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a log record to OpenTelemetry."""
        try:
            # Format the message
            msg = self.format(record)

            # Build attributes from log record
            attributes: dict = {
                "log.logger": record.name,
                "log.level": record.levelname,
                "log.message": msg,
            }

            if record.pathname:
                attributes["code.filepath"] = record.pathname
            if record.lineno:
                attributes["code.lineno"] = record.lineno
            if record.funcName:
                attributes["code.function"] = record.funcName

            # Add exception info if present
            if record.exc_info and record.exc_info[1]:
                exc = record.exc_info[1]
                attributes["exception.type"] = type(exc).__name__
                attributes["exception.message"] = str(exc)

            # 1. Add as span event (appears when expanding trace)
            span = trace.get_current_span()
            if span.is_recording():
                event_name = f"log.{record.levelname.lower()}"
                span.add_event(event_name, attributes=attributes)

            # 2. Also emit to OpenTelemetry logs pipeline
            try:
                if self._otel_logger is None:
                    self._otel_logger = get_logger()

                self._otel_logger.emit(
                    LogRecord(
                        timestamp=int(record.created * 1e9),  # Convert to nanoseconds
                        observed_timestamp=int(record.created * 1e9),
                        severity_text=record.levelname,
                        severity_number=_get_severity(record.levelno),
                        body=msg,
                        resource=getattr(self._otel_logger, "resource", None),
                        attributes=attributes,
                        context=context.get_current(),
                    )
                )
            except Exception:
                # Log pipeline may not be configured, that's OK
                pass

        except Exception:
            # Don't let logging errors break tests
            self.handleError(record)


def emit_stdio_log(
    body: str, stream: int, span: Optional[Span] = None, eof: bool = False
) -> None:
    """Emit a log record with stdio.stream attribute for Dagger UI.

    This emits OpenTelemetry log records that Dagger UI can render as
    stdout/stderr output. The stdio.stream attribute indicates which
    stream the content belongs to (1=stdout, 2=stderr).

    Args:
        body: The content to emit
        stream: STDIO_STREAM_STDOUT (1) or STDIO_STREAM_STDERR (2)
        span: Optional span to associate the log with. If None, uses current context.
              Passing an explicit span is recommended to ensure logs are associated
              with the correct test span, as context propagation can be unreliable
              during pytest hook execution.
        eof: If True, marks the end of the stream
    """
    from pytest_otel.config import get_logger

    otel_logger = get_logger()

    attributes: dict = {STDIO_STREAM_ATTR: stream}
    if eof:
        attributes[STDIO_EOF_ATTR] = True

    # Create context from span if provided, otherwise use current context.
    # Explicit span passing is more reliable than implicit context propagation
    # because pytest hook execution order can cause context to be different
    # at log emission time.
    if span is not None and span.is_recording():
        # Create a fresh context with just the span, without inheriting
        # potentially stale context state from context.get_current()
        log_context = trace.set_span_in_context(span)
    else:
        log_context = context.get_current()

    try:
        otel_logger.emit(
            LogRecord(
                timestamp=int(time.time() * 1e9),
                observed_timestamp=int(time.time() * 1e9),
                body=body,
                attributes=attributes,
                context=log_context,
            )
        )
    except Exception:
        # Don't let logging errors break tests
        pass
