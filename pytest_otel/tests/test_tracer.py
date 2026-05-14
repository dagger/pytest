"""Tests for the tracer module."""

from unittest.mock import Mock

from opentelemetry.trace import StatusCode

from pytest_otel.tracer import SpanContextManager, TestNode as OtelTestNode


class TestParseNodeid:
    """Tests for nodeid parsing."""

    def test_module_only(self):
        """Test parsing a module-only nodeid."""
        ctx = SpanContextManager()
        module, cls, func = ctx._parse_nodeid("tests/test_foo.py")

        assert module == "tests/test_foo.py"
        assert cls is None
        assert func is None

    def test_module_function(self):
        """Test parsing a module::function nodeid."""
        ctx = SpanContextManager()
        module, cls, func = ctx._parse_nodeid("tests/test_foo.py::test_bar")

        assert module == "tests/test_foo.py"
        assert cls is None
        assert func == "test_bar"

    def test_module_class_function(self):
        """Test parsing a module::class::function nodeid."""
        ctx = SpanContextManager()
        module, cls, func = ctx._parse_nodeid("tests/test_foo.py::TestClass::test_method")

        assert module == "tests/test_foo.py"
        assert cls == "TestClass"
        assert func == "test_method"

    def test_parametrized_test(self):
        """Test parsing a parametrized test nodeid."""
        ctx = SpanContextManager()
        module, cls, func = ctx._parse_nodeid("tests/test_foo.py::test_bar[param1-param2]")

        assert module == "tests/test_foo.py"
        assert cls is None
        assert func == "test_bar[param1-param2]"

    def test_nested_class(self):
        """Test parsing a nested class nodeid."""
        ctx = SpanContextManager()
        module, cls, func = ctx._parse_nodeid(
            "tests/test_foo.py::TestOuter::TestInner::test_method"
        )

        assert module == "tests/test_foo.py"
        assert cls == "TestOuter"
        assert func == "TestInner::test_method"


class TestSemconvMappings:
    """Tests for OTel test semconv value mappings."""

    def test_case_result_status_mapping(self):
        """Verify pytest outcomes map to semconv test case result statuses."""
        from opentelemetry.semconv._incubating.attributes.test_attributes import (
            TestCaseResultStatusValues,
        )

        ctx = SpanContextManager()

        assert ctx._case_result_status("passed") == TestCaseResultStatusValues.PASS.value
        assert ctx._case_result_status("failed") == TestCaseResultStatusValues.FAIL.value
        assert ctx._case_result_status("error") == TestCaseResultStatusValues.FAIL.value
        assert ctx._case_result_status("skipped") == "skipped"

    def test_suite_run_status_mapping(self):
        """Verify pytest exit statuses map to semconv test suite run statuses."""
        from opentelemetry.semconv._incubating.attributes.test_attributes import (
            TestSuiteRunStatusValues,
        )

        ctx = SpanContextManager()

        assert ctx._suite_run_status(0) == TestSuiteRunStatusValues.SUCCESS.value
        assert ctx._suite_run_status(1) == TestSuiteRunStatusValues.FAILURE.value
        assert ctx._suite_run_status(2) == TestSuiteRunStatusValues.ABORTED.value
        assert ctx._suite_run_status(5) == TestSuiteRunStatusValues.SKIPPED.value


class TestStatusDescriptions:
    """Tests for low-cardinality span status descriptions."""

    def test_session_failure_status_is_static(self):
        """Verify session failures do not include dynamic exit status text."""
        ctx = SpanContextManager()
        span = Mock()
        ctx._session_node = OtelTestNode(
            nodeid="session",
            name="pytest session",
            kind="session",
            span=span,
        )

        ctx.end_session(1)

        status = span.set_status.call_args.args[0]
        assert status.status_code == StatusCode.ERROR
        assert status.description == "test session failed"

    def test_test_failure_status_is_static(self):
        """Verify test failures do not include dynamic assertion text."""
        ctx = SpanContextManager()
        span = Mock()
        item = Mock()
        item.nodeid = "tests/test_foo.py::test_bar"
        ctx._tests[item.nodeid] = OtelTestNode(
            nodeid=item.nodeid,
            name="test_bar",
            kind="function",
            span=span,
        )

        ctx.end_test(item, "failed")

        status = span.set_status.call_args.args[0]
        assert status.status_code == StatusCode.ERROR
        assert status.description == "test failed"


class TestAttributeNames:
    """Tests for attribute name constants."""

    def test_attribute_names(self):
        """Verify attribute names for Dagger UI and OTel test semconv integration."""
        from pytest_otel import tracer

        from opentelemetry.semconv._incubating.attributes import test_attributes

        assert tracer.ATTR_UI_BOUNDARY == "dagger.io/ui.boundary"
        assert not hasattr(tracer, "ATTR_UI_REVEAL")
        assert not hasattr(tracer, "ATTR_PYTEST_NODEID")
        assert not hasattr(tracer, "ATTR_PYTEST_MODULE")
        assert not hasattr(tracer, "ATTR_PYTEST_CLASS")
        assert not hasattr(tracer, "ATTR_PYTEST_FUNCTION")
        assert not hasattr(tracer, "ATTR_PYTEST_OUTCOME")
        assert tracer.TEST_CASE_NAME == test_attributes.TEST_CASE_NAME
        assert tracer.TEST_CASE_RESULT_STATUS == test_attributes.TEST_CASE_RESULT_STATUS
        assert tracer.TEST_SUITE_NAME == test_attributes.TEST_SUITE_NAME
        assert tracer.TEST_SUITE_RUN_STATUS == test_attributes.TEST_SUITE_RUN_STATUS
