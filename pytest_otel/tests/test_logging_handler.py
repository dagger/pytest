"""Tests for the logging handler."""

import logging
from unittest.mock import Mock, MagicMock

import pytest

from pytest_otel.logging_handler import (
    OtelLogHandler,
    emit_stdio_log,
    _get_severity,
    STDIO_STREAM_STDOUT,
    STDIO_STREAM_STDERR,
    STDIO_STREAM_ATTR,
)
from opentelemetry._logs import SeverityNumber


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
        monkeypatch.setattr(
            "pytest_otel.logging_handler.get_logger", lambda: MagicMock()
        )

        handler.emit(record)

        # Verify span event was created
        mock_span.add_event.assert_called_once()
        call_args = mock_span.add_event.call_args
        assert "log.info" in call_args[0]
        assert call_args[1]["attributes"]["log.logger"] == "test.logger"


class TestEmitStdioLog:
    """Tests for emit_stdio_log function."""

    def test_emit_stdout(self, reset_telemetry, mock_otlp_exporters):
        """Test emitting a stdout log."""
        # Just test that the function doesn't raise an exception
        # The actual logger emission is tested in OtelLogHandler tests
        try:
            emit_stdio_log("test output", STDIO_STREAM_STDOUT)
            # If we got here without exception, the test passes
            assert True
        except Exception as e:
            pytest.fail(f"emit_stdio_log raised unexpected exception: {e}")

    def test_emit_stderr(self, reset_telemetry, mock_otlp_exporters):
        """Test emitting a stderr log."""
        # Just test that the function doesn't raise an exception
        try:
            emit_stdio_log("test error", STDIO_STREAM_STDERR)
            # If we got here without exception, the test passes
            assert True
        except Exception as e:
            pytest.fail(f"emit_stdio_log raised unexpected exception: {e}")

    def test_emit_with_eof(self, reset_telemetry, mock_otlp_exporters):
        """Test emitting with EOF marker."""
        # Just test that the function doesn't raise an exception
        try:
            emit_stdio_log("final output", STDIO_STREAM_STDOUT, eof=True)
            # If we got here without exception, the test passes
            assert True
        except Exception as e:
            pytest.fail(f"emit_stdio_log raised unexpected exception: {e}")
