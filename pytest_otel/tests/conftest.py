"""Test configuration for pytest-otel tests."""

import atexit
import os
from typing import Generator

import pytest


@pytest.fixture
def reset_telemetry() -> Generator[None, None, None]:
    """Reset telemetry state between tests.

    This fixture:
    1. Resets the TelemetryConfig singleton state
    2. Removes any OtelLogHandler to prevent recursive deadlocks
    3. Ensures providers are properly cleared

    This prevents tests from interfering with each other and
    ensures each test starts with a clean slate.
    """
    import logging
    from pytest_otel import config
    from pytest_otel.logging_handler import OtelLogHandler
    from opentelemetry import trace, _logs

    # Remove any OtelLogHandler before the test runs
    root_logger = logging.getLogger()
    handlers_to_remove = [
        h for h in root_logger.handlers if isinstance(h, OtelLogHandler)
    ]
    for handler in handlers_to_remove:
        root_logger.removeHandler(handler)

    # Reset global OpenTelemetry state BEFORE the test
    trace._set_tracer_provider(None, log=False)
    _logs.set_logger_provider(None)

    # Reset the telemetry singleton
    with config._config._lock:
        config._config._is_configured = False
        config._config._tracer_provider = None
        config._config._logger_provider = None

    yield

    # Clean up after the test
    with config._config._lock:
        config._config._is_configured = False
        config._config._tracer_provider = None
        config._config._logger_provider = None

    # Reset global OpenTelemetry state AFTER the test
    trace._set_tracer_provider(None, log=False)
    _logs.set_logger_provider(None)

    # Remove any OtelLogHandler again after the test
    handlers_to_remove = [
        h for h in root_logger.handlers if isinstance(h, OtelLogHandler)
    ]
    for handler in handlers_to_remove:
        root_logger.removeHandler(handler)


@pytest.fixture
def mock_otlp_exporters(monkeypatch) -> Generator[None, None, None]:
    """Mock OTLP exporters to prevent network calls during tests.

    This fixture patches the exporter factory functions to return None,
    preventing any real OTLP connections from being attempted.
    This is critical to prevent tests from hanging during shutdown.

    Usage:
        def test_something(mock_otlp_exporters, reset_telemetry):
            # Test code here - OTLP exporters are mocked
    """

    def mock_get_trace_exporter():
        """Return None instead of creating a real exporter."""
        return None

    def mock_get_log_exporter():
        """Return None instead of creating a real exporter."""
        return None

    def mock_atexit_register(func):
        """Mock atexit.register to prevent shutdown handlers during tests."""
        pass  # Don't register the handler

    def mock_force_flush(timeout_millis=None):
        """Mock force_flush to return immediately without timeouts."""
        return True

    def mock_shutdown():
        """Mock shutdown to return immediately."""
        pass

    # Patch the exporter factory functions in the config module
    monkeypatch.setattr(
        "pytest_otel.config._get_otlp_exporter", mock_get_trace_exporter
    )
    monkeypatch.setattr(
        "pytest_otel.config._get_otlp_log_exporter", mock_get_log_exporter
    )

    # Prevent atexit handler registration to avoid hanging on test completion
    monkeypatch.setattr("pytest_otel.config.atexit.register", mock_atexit_register)

    # Mock the provider force_flush and shutdown methods to prevent timeouts
    monkeypatch.setattr(
        "opentelemetry.sdk.trace.TracerProvider.force_flush", mock_force_flush
    )
    monkeypatch.setattr(
        "opentelemetry.sdk.trace.TracerProvider.shutdown", mock_shutdown
    )
    monkeypatch.setattr(
        "opentelemetry.sdk._logs.LoggerProvider.force_flush", mock_force_flush
    )
    monkeypatch.setattr(
        "opentelemetry.sdk._logs.LoggerProvider.shutdown", mock_shutdown
    )

    # Clear OTLP endpoint environment variables to prevent any network attempts
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", raising=False)

    yield
    # Cleanup happens automatically via monkeypatch


@pytest.fixture
def no_otel_env(monkeypatch) -> Generator[None, None, None]:
    """Clear all OTEL_* environment variables.

    Use this when you want to test behavior with no OTLP configuration.
    """
    # Clear OTEL environment variables
    for var in list(os.environ.keys()):
        if var.startswith("OTEL_"):
            monkeypatch.delenv(var, raising=False)

    yield


@pytest.fixture
def with_traceparent(monkeypatch) -> Generator[str, None, None]:
    """Set a valid TRACEPARENT environment variable.

    Returns the traceparent value for use in assertions.
    """
    traceparent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    monkeypatch.setenv("TRACEPARENT", traceparent)
    yield traceparent
