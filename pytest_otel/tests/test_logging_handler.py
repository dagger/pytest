"""Tests for the logging handler."""

import logging
from unittest.mock import Mock, MagicMock

from pytest_otel.logging_handler import (
    OtelLogHandler,
    emit_stdio_log,
    _get_severity,
    _get_stdio_stream,
    STDIO_STREAM_STDOUT,
    STDIO_STREAM_STDERR,
    STDIO_STREAM_ATTR,
)
from opentelemetry._logs import SeverityNumber
from opentelemetry.sdk.trace import TracerProvider


class TestGetSeverity:
    """Tests for _get_severity function."""

    def test_debug_level(self):
        """Test DEBUG severity mapping."""
        severity = _get_severity(logging.DEBUG)
        assert severity == SeverityNumber.DEBUG

    def test_info_level(self):
        """Test INFO severity mapping."""
        severity = _get_severity(logging.INFO)
        assert severity == SeverityNumber.INFO

    def test_warning_level(self):
        """Test WARNING severity mapping."""
        severity = _get_severity(logging.WARNING)
        assert severity == SeverityNumber.WARN

    def test_error_level(self):
        """Test ERROR severity mapping."""
        severity = _get_severity(logging.ERROR)
        assert severity == SeverityNumber.ERROR

    def test_critical_level(self):
        """Test CRITICAL/FATAL severity mapping."""
        severity = _get_severity(logging.CRITICAL)
        assert severity == SeverityNumber.FATAL


class TestGetStdioStream:
    """Tests for _get_stdio_stream function."""

    def test_debug_and_info_use_stdout(self):
        """Test lower-severity logs map to stdout."""
        assert _get_stdio_stream(logging.DEBUG) == STDIO_STREAM_STDOUT
        assert _get_stdio_stream(logging.INFO) == STDIO_STREAM_STDOUT

    def test_warning_and_above_use_stderr(self):
        """Test warnings and errors map to stderr."""
        assert _get_stdio_stream(logging.WARNING) == STDIO_STREAM_STDERR
        assert _get_stdio_stream(logging.ERROR) == STDIO_STREAM_STDERR
        assert _get_stdio_stream(logging.CRITICAL) == STDIO_STREAM_STDERR


class TestOtelLogHandler:
    """Tests for OtelLogHandler class."""

    def test_handler_initialization(self):
        """Test OtelLogHandler can be initialized."""
        handler = OtelLogHandler()
        assert handler is not None
        assert handler.level == logging.NOTSET

    def test_handler_with_custom_level(self):
        """Test OtelLogHandler with custom level."""
        handler = OtelLogHandler(level=logging.INFO)
        assert handler.level == logging.INFO

    def test_emit_creates_attributes(self, monkeypatch):
        """Test that emit() creates log attributes."""
        handler = OtelLogHandler()

        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="/test/path.py",
            lineno=42,
            msg="Test message",
            args=(),
            exc_info=None,
            func="test_func",
            sinfo=None,
        )

        # Mock the span to capture events
        mock_span = MagicMock()
        mock_span.is_recording.return_value = True

        monkeypatch.setattr(
            "pytest_otel.logging_handler.trace.get_current_span", lambda: mock_span
        )

        # Mock the otel logger
        mock_logger = MagicMock()
        monkeypatch.setattr(
            "pytest_otel.logging_handler.get_logger", lambda: mock_logger
        )

        handler.emit(record)

        # Verify span event was created
        mock_span.add_event.assert_called_once()
        call_args = mock_span.add_event.call_args
        assert "log.info" in call_args[0]
        assert call_args[1]["attributes"]["log.logger"] == "test.logger"
        assert call_args[1]["attributes"][STDIO_STREAM_ATTR] == STDIO_STREAM_STDOUT

        # Verify an OpenTelemetry log record was emitted with stdio attributes
        mock_logger.emit.assert_called_once()
        emitted = mock_logger.emit.call_args.args[0]
        assert emitted.body == "Test message\n"
        assert emitted.attributes["log.message"] == "Test message"
        assert emitted.attributes[STDIO_STREAM_ATTR] == STDIO_STREAM_STDOUT


class TestEmitStdioLog:
    """Tests for emit_stdio_log function."""

    def test_emit_stdout(self, monkeypatch):
        """Test emitting a stdout log."""
        mock_logger = Mock()
        monkeypatch.setattr(
            "pytest_otel.logging_handler.get_logger", lambda: mock_logger
        )

        emit_stdio_log("test output", STDIO_STREAM_STDOUT)

        mock_logger.emit.assert_called_once()
        emitted = mock_logger.emit.call_args.args[0]
        assert emitted.body == "test output"
        assert emitted.attributes[STDIO_STREAM_ATTR] == STDIO_STREAM_STDOUT

    def test_emit_stderr(self, monkeypatch):
        """Test emitting a stderr log."""
        mock_logger = Mock()
        monkeypatch.setattr(
            "pytest_otel.logging_handler.get_logger", lambda: mock_logger
        )

        emit_stdio_log("test error", STDIO_STREAM_STDERR)

        mock_logger.emit.assert_called_once()
        emitted = mock_logger.emit.call_args.args[0]
        assert emitted.body == "test error"
        assert emitted.attributes[STDIO_STREAM_ATTR] == STDIO_STREAM_STDERR

    def test_emit_with_eof(self, monkeypatch):
        """Test emitting with EOF marker."""
        mock_logger = Mock()
        monkeypatch.setattr(
            "pytest_otel.logging_handler.get_logger", lambda: mock_logger
        )

        emit_stdio_log("final output", STDIO_STREAM_STDOUT, eof=True)

        emitted = mock_logger.emit.call_args.args[0]
        assert emitted.attributes[STDIO_STREAM_ATTR] == STDIO_STREAM_STDOUT
        assert emitted.attributes["stdio.eof"] is True

    def test_emit_with_explicit_span(self, monkeypatch):
        """Test emitting with an explicit span context."""
        span = TracerProvider().get_tracer("test").start_span("test")
        span_context = span.get_span_context()
        mock_logger = Mock()
        monkeypatch.setattr(
            "pytest_otel.logging_handler.get_logger", lambda: mock_logger
        )

        try:
            emit_stdio_log("test output", STDIO_STREAM_STDOUT, span=span)
        finally:
            span.end()

        emitted = mock_logger.emit.call_args.args[0]
        assert emitted.trace_id == span_context.trace_id
        assert emitted.span_id == span_context.span_id
