"""Pytest plugin hooks for OpenTelemetry instrumentation.

This module provides the pytest hooks that automatically instrument tests
with OpenTelemetry spans. The plugin is auto-discovered via the pytest11
entry point defined in pyproject.toml.
"""

import logging
from typing import Dict, Generator, Optional

import pytest
from _pytest.reports import TestReport

from pytest_otel import config as otel_config
from pytest_otel import tracer
from pytest_otel.logging_handler import OtelLogHandler

logger = logging.getLogger(__name__)

# Track whether plugin is enabled
_enabled: bool = False
_log_handler: Optional[OtelLogHandler] = None

# Track test outcomes by nodeid (populated by pytest_runtest_makereport)
_test_outcomes: Dict[str, str] = {}


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add plugin command-line options."""
    group = parser.getgroup("opentelemetry", "OpenTelemetry instrumentation")
    group.addoption(
        "--no-otel",
        action="store_true",
        default=False,
        help="Disable OpenTelemetry instrumentation",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Initialize OpenTelemetry on pytest startup."""
    global _enabled, _log_handler

    # Check if explicitly disabled
    if config.option.no_otel:
        logger.debug("OpenTelemetry instrumentation disabled via --no-otel")
        return

    # Initialize telemetry
    try:
        otel_config.configure()
        _enabled = True
        logger.debug("OpenTelemetry instrumentation enabled")

        # Set up logging capture
        _log_handler = OtelLogHandler()
        # Add to root logger to capture all test logs
        root_logger = logging.getLogger()
        root_logger.addHandler(_log_handler)

    except Exception as e:
        logger.warning("Failed to initialize OpenTelemetry: %s", e)
        _enabled = False


def pytest_unconfigure(config: pytest.Config) -> None:
    """Cleanup OpenTelemetry on pytest shutdown."""
    global _enabled, _log_handler

    if not _enabled:
        return

    # Remove log handler
    if _log_handler:
        root_logger = logging.getLogger()
        root_logger.removeHandler(_log_handler)
        _log_handler = None

    # Shutdown telemetry (flushes spans)
    try:
        otel_config.shutdown()
        logger.debug("OpenTelemetry instrumentation shut down")
    except Exception as e:
        logger.warning("Error shutting down OpenTelemetry: %s", e)

    _enabled = False


def pytest_sessionstart(session: pytest.Session) -> None:
    """Start session span when pytest session begins."""
    if not _enabled:
        return

    try:
        tracer.start_session(session)
        logger.debug("Started session span")
    except Exception as e:
        logger.warning("Failed to start session span: %s", e)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """End session span when pytest session finishes."""
    if not _enabled:
        return

    try:
        tracer.end_session(exitstatus)
        logger.debug("Ended session span with exit status %d", exitstatus)
    except Exception as e:
        logger.warning("Failed to end session span: %s", e)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_protocol(
    item: pytest.Item, nextitem: Optional[pytest.Item]
) -> Generator[None, object, object]:
    """Wrap entire test execution in a span.

    This hook wraps the full test lifecycle (setup, call, teardown)
    ensuring all phases are captured in a single span.
    """
    if not _enabled:
        return (yield)

    # Clear any previous outcome for this test
    _test_outcomes.pop(item.nodeid, None)

    # Start test span
    try:
        tracer.start_test(item)
    except Exception as e:
        logger.warning("Failed to start test span for %s: %s", item.nodeid, e)
        return (yield)

    # Execute test and capture outcome
    outcome = "passed"
    try:
        result = yield

        # Get outcome from tracked reports (populated by pytest_runtest_makereport)
        outcome = _test_outcomes.get(item.nodeid, "passed")

        return result
    except Exception as e:
        outcome = "error"
        tracer.record_exception(item, e)
        raise
    finally:
        # Clean up tracked outcome
        _test_outcomes.pop(item.nodeid, None)

        # Always end span
        try:
            tracer.end_test(item, outcome)
        except Exception as e:
            logger.warning("Failed to end test span for %s: %s", item.nodeid, e)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo
) -> Generator[None, TestReport, TestReport]:
    """Capture test reports, track outcomes, and record exceptions."""
    report: TestReport = yield

    if not _enabled:
        return report

    # Track outcome - worst outcome wins (failed > skipped > passed)
    # This is called for setup, call, and teardown phases
    current_outcome = _test_outcomes.get(item.nodeid, "passed")

    if report.failed:
        # Any failure in any phase means test failed
        _test_outcomes[item.nodeid] = "failed"
    elif report.skipped and current_outcome == "passed":
        # Skipped only if not already failed
        _test_outcomes[item.nodeid] = "skipped"
    elif report.when == "call" and current_outcome == "passed":
        # Track call phase outcome if no failure yet
        _test_outcomes[item.nodeid] = report.outcome

    # Record exceptions from the call phase
    if call.excinfo and report.when == "call":
        try:
            tracer.record_exception(item, call.excinfo.value)
        except Exception as e:
            logger.warning("Failed to record exception for %s: %s", item.nodeid, e)

    # Capture stdout/stderr and longrepr (failure details) on the span
    try:
        _capture_test_output(item, report)
    except Exception as e:
        logger.warning("Failed to capture test output for %s: %s", item.nodeid, e)

    return report


def _capture_test_output(item: pytest.Item, report: TestReport) -> None:
    """Capture test output as OpenTelemetry log records with stdio.stream attribute.

    This emits log records that Dagger UI can render as stdout/stderr output.
    Uses stdio.stream=1 for stdout and stdio.stream=2 for stderr, matching
    Dagger's conventions from telemetry/attrs.go.

    The test span is looked up explicitly and passed to emit_stdio_log() to ensure
    logs are associated with the correct test span, rather than relying on implicit
    context propagation which can be unreliable during pytest hook execution.
    """
    from pytest_otel.logging_handler import (
        STDIO_STREAM_STDERR,
        STDIO_STREAM_STDOUT,
        emit_stdio_log,
    )

    # Only capture from the call phase (not setup/teardown) unless there's a failure
    if report.when != "call" and not report.failed:
        return

    # Get the test span to associate logs with it explicitly.
    # This is more reliable than context propagation during pytest hooks.
    test_span = tracer.get_test_span(item)
    if test_span is None:
        logger.debug(
            "No active span for test %s, logs may be misattributed", item.nodeid
        )

    # Capture stdout if present
    if hasattr(report, "capstdout") and report.capstdout:
        stdout = report.capstdout[:8192]  # Limit size
        emit_stdio_log(stdout, STDIO_STREAM_STDOUT, span=test_span)

    # Capture stderr if present
    if hasattr(report, "capstderr") and report.capstderr:
        stderr = report.capstderr[:8192]  # Limit size
        emit_stdio_log(stderr, STDIO_STREAM_STDERR, span=test_span)

    # Capture failure details (longrepr) - emit to stderr stream
    # This contains the traceback and assertion details
    if report.failed and report.longrepr:
        longrepr_str = str(report.longrepr)[:16384]  # Limit size
        emit_stdio_log(longrepr_str, STDIO_STREAM_STDERR, span=test_span)


