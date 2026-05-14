"""Span management and test context tracking for pytest instrumentation."""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from opentelemetry import context, trace
from opentelemetry.semconv._incubating.attributes.test_attributes import (
    TEST_CASE_NAME as TEST_CASE_NAME,
    TEST_CASE_RESULT_STATUS as TEST_CASE_RESULT_STATUS,
    TEST_SUITE_NAME as TEST_SUITE_NAME,
    TEST_SUITE_RUN_STATUS as TEST_SUITE_RUN_STATUS,
    TestCaseResultStatusValues,
    TestSuiteRunStatusValues,
)
from opentelemetry.trace import Span, Status, StatusCode

from pytest_otel.config import get_tracer

if TYPE_CHECKING:
    import pytest

logger = logging.getLogger(__name__)

# Attribute names for Dagger UI integration
ATTR_UI_BOUNDARY = "dagger.io/ui.boundary"


@dataclass
class TestNode:
    """Represents a test node in the hierarchy."""

    nodeid: str
    name: str
    kind: str  # "session", "module", "class", "function"
    span: Optional[Span] = None
    token: Optional[object] = None  # context token for cleanup


@dataclass
class SpanContextManager:
    """Manages hierarchical test span contexts.

    Tracks the test execution hierarchy and ensures spans are properly
    nested and cleaned up. Uses a dict for O(1) lookup by nodeid.
    """

    _tests: dict[str, TestNode] = field(default_factory=dict)
    _session_node: Optional[TestNode] = None

    def start_session(self, session: "pytest.Session") -> Optional[Span]:
        """Start a span for the entire pytest session.

        When running inside Dagger (TRACEPARENT is set), we skip creating
        a session span and let test spans be direct children of the parent.
        This preserves the meaningful parent span name (e.g., "test with python 3.13").
        """
        # Check if we already have a parent context from TRACEPARENT
        current_span = trace.get_current_span()
        if current_span.get_span_context().is_valid:
            # Parent exists (e.g., from Dagger), skip session span
            logger.debug("Parent span exists, skipping pytest session span")
            return None

        # No parent - create a session span as the root
        tracer = get_tracer()

        span = tracer.start_span(
            "pytest session",
            attributes={
                ATTR_UI_BOUNDARY: True,
                TEST_SUITE_NAME: self._session_suite_name(session),
                TEST_SUITE_RUN_STATUS: TestSuiteRunStatusValues.IN_PROGRESS.value,
            },
        )

        token = None
        try:
            # Make this span the active context
            token = context.attach(trace.set_span_in_context(span))

            self._session_node = TestNode(
                nodeid="session",
                name="pytest session",
                kind="session",
                span=span,
                token=token,
            )

            return span
        except Exception:
            # Cleanup on failure
            if token is not None:
                context.detach(token)
            span.end()
            raise

    def end_session(self, exitstatus: int) -> None:
        """End the session span with appropriate status."""
        if not self._session_node or not self._session_node.span:
            return

        span = self._session_node.span

        suite_status = self._suite_run_status(exitstatus)
        span.set_attribute(TEST_SUITE_RUN_STATUS, suite_status)

        # Set status based on exit code
        if exitstatus == 0:
            span.set_status(Status(StatusCode.OK))
        else:
            span.set_status(Status(StatusCode.ERROR, "test session failed"))

        span.end()

        # Detach context
        if self._session_node.token:
            context.detach(self._session_node.token)

        self._session_node = None

    def start_test(self, item: "pytest.Item") -> Span:
        """Start a span for a test item."""
        tracer = get_tracer()

        # Build attributes for Dagger UI and OpenTelemetry test semantic conventions.
        attributes = {
            ATTR_UI_BOUNDARY: True,
            TEST_CASE_NAME: item.nodeid,
        }

        suite_name = self._test_suite_name(item.nodeid)
        if suite_name:
            attributes[TEST_SUITE_NAME] = suite_name

        span = tracer.start_span(item.name, attributes=attributes)

        token = None
        try:
            # Make this span the active context
            token = context.attach(trace.set_span_in_context(span))

            node = TestNode(
                nodeid=item.nodeid,
                name=item.name,
                kind="function",
                span=span,
                token=token,
            )
            self._tests[item.nodeid] = node

            return span
        except Exception:
            # Cleanup on failure to prevent context leaks
            if token is not None:
                context.detach(token)
            span.end()
            raise

    def end_test(self, item: "pytest.Item", outcome: str) -> None:
        """End a test span with the given outcome."""
        # Get and remove the node from dict (O(1) lookup)
        node = self._tests.pop(item.nodeid, None)

        if not node:
            logger.warning("Test node not found for %s, span may have leaked", item.nodeid)
            return

        if not node.span:
            return

        span = node.span

        # Record outcome using OpenTelemetry test semantic conventions.
        span.set_attribute(TEST_CASE_RESULT_STATUS, self._case_result_status(outcome))

        # Set span status based on outcome
        if outcome == "passed":
            span.set_status(Status(StatusCode.OK))
        elif outcome in ("failed", "error"):
            span.set_status(Status(StatusCode.ERROR, "test failed"))
        # "skipped" keeps UNSET status (neither OK nor ERROR)

        span.end()

        # Detach context
        if node.token:
            context.detach(node.token)

    def record_exception(self, item: "pytest.Item", exc: BaseException) -> None:
        """Record an exception on the current test span."""
        node = self._tests.get(item.nodeid)
        if node and node.span:
            node.span.record_exception(exc)

    def get_test_span(self, item: "pytest.Item") -> Optional[Span]:
        """Get the active span for a test item.

        Returns None if the test has no active span (e.g., not started or already ended).
        This is useful for associating logs with the correct test span.
        """
        node = self._tests.get(item.nodeid)
        return node.span if node else None

    def _session_suite_name(self, session: "pytest.Session") -> str:
        """Return a semantic-convention test suite name for the pytest session."""
        rootpath = getattr(session.config, "rootpath", None)
        if rootpath:
            return str(rootpath)
        return "pytest"

    def _test_suite_name(self, nodeid: str) -> Optional[str]:
        """Return a semantic-convention test suite name for a pytest nodeid."""
        module, cls, _ = self._parse_nodeid(nodeid)
        if not module:
            return None
        if cls:
            return f"{module}::{cls}"
        return module

    def _case_result_status(self, outcome: str) -> str:
        """Map pytest outcomes to OTel test.case.result.status values."""
        if outcome == "passed":
            return TestCaseResultStatusValues.PASS.value
        if outcome in ("failed", "error"):
            return TestCaseResultStatusValues.FAIL.value
        if outcome == "skipped":
            # The semconv package only defines pass/fail well-known values; custom values are allowed.
            return "skipped"
        return outcome

    def _suite_run_status(self, exitstatus: int) -> str:
        """Map pytest exit statuses to OTel test.suite.run.status values."""
        if exitstatus == 0:
            return TestSuiteRunStatusValues.SUCCESS.value
        if exitstatus == 2:
            return TestSuiteRunStatusValues.ABORTED.value
        if exitstatus == 5:
            return TestSuiteRunStatusValues.SKIPPED.value
        return TestSuiteRunStatusValues.FAILURE.value

    def _parse_nodeid(self, nodeid: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Parse pytest nodeid into (module, class, function).

        Examples:
            "tests/test_foo.py::test_func" -> ("tests/test_foo.py", None, "test_func")
            "tests/test_foo.py::TestClass::test_method" -> ("tests/test_foo.py", "TestClass", "test_method")
            "tests/test_foo.py::TestClass::test_method[param]" -> ("tests/test_foo.py", "TestClass", "test_method[param]")
        """
        parts = nodeid.split("::")

        if len(parts) == 1:
            # Just a module path
            return parts[0], None, None

        module = parts[0]

        if len(parts) == 2:
            # module::function
            return module, None, parts[1]

        if len(parts) >= 3:
            # module::class::function (possibly with more nesting)
            cls = parts[1]
            func = "::".join(parts[2:])  # Handle nested classes
            return module, cls, func

        return module, None, None


# Global context manager instance
_context_manager = SpanContextManager()


def start_session(session: "pytest.Session") -> Optional[Span]:
    """Start the session span."""
    return _context_manager.start_session(session)


def end_session(exitstatus: int) -> None:
    """End the session span."""
    _context_manager.end_session(exitstatus)


def start_test(item: "pytest.Item") -> Span:
    """Start a test span."""
    return _context_manager.start_test(item)


def end_test(item: "pytest.Item", outcome: str) -> None:
    """End a test span."""
    _context_manager.end_test(item, outcome)


def record_exception(item: "pytest.Item", exc: BaseException) -> None:
    """Record an exception on the test span."""
    _context_manager.record_exception(item, exc)


def get_test_span(item: "pytest.Item") -> Optional[Span]:
    """Get the active span for a test item.

    This is useful for explicitly associating logs with the correct test span,
    rather than relying on context propagation which can be unreliable
    during pytest hook execution.
    """
    return _context_manager.get_test_span(item)
