"""Tests for the pytest plugin hooks."""

from unittest.mock import Mock


class TestPytestConfigure:
    """Tests for pytest_configure hook."""

    def test_configure_disables_with_no_otel_flag(self, reset_telemetry):
        """Test that --no-otel flag disables telemetry."""
        from pytest_otel import plugin

        config = Mock()
        config.option.no_otel = True

        # Reset plugin state
        plugin._enabled = False

        plugin.pytest_configure(config)

        assert not plugin._enabled

    def test_configure_initializes_telemetry(
        self, reset_telemetry, mock_otlp_exporters
    ):
        """Test that configure initializes telemetry."""
        from pytest_otel import plugin

        config = Mock()
        config.option.no_otel = False

        # Reset plugin state
        plugin._enabled = False

        try:
            plugin.pytest_configure(config)

            assert plugin._enabled
            assert plugin._log_handler is not None
        finally:
            # Clean up to prevent hanging
            plugin.pytest_unconfigure(config)


class TestPytestUnconfigure:
    """Tests for pytest_unconfigure hook."""

    def test_unconfigure_when_not_enabled(self, reset_telemetry):
        """Test unconfigure does nothing when not enabled."""
        from pytest_otel import plugin

        config = Mock()
        plugin._enabled = False
        plugin._log_handler = None

        # Should not raise
        plugin.pytest_unconfigure(config)

        assert not plugin._enabled


class TestCaptureTestOutput:
    """Tests for _capture_test_output function."""

    def test_capture_stdout(self, monkeypatch):
        """Test that stdout is captured."""
        from pytest_otel import plugin
        from pytest_otel.logging_handler import STDIO_STREAM_STDOUT

        mock_item = Mock()
        mock_item.nodeid = "tests/test_example.py::test_function"
        mock_span = Mock()
        emitted = []

        monkeypatch.setattr(plugin.tracer, "get_test_span", lambda item: mock_span)
        monkeypatch.setattr(
            "pytest_otel.logging_handler.emit_stdio_log",
            lambda body, stream, span=None: emitted.append((body, stream, span)),
        )

        report = Mock()
        report.when = "call"
        report.failed = False
        report.capstdout = "test stdout"
        report.capstderr = None
        report.longrepr = None

        plugin._capture_test_output(mock_item, report)

        assert emitted == [("test stdout", STDIO_STREAM_STDOUT, mock_span)]

    def test_capture_stderr(self, monkeypatch):
        """Test that stderr is captured."""
        from pytest_otel import plugin
        from pytest_otel.logging_handler import STDIO_STREAM_STDERR

        mock_item = Mock()
        mock_item.nodeid = "tests/test_example.py::test_function"
        mock_span = Mock()
        emitted = []

        monkeypatch.setattr(plugin.tracer, "get_test_span", lambda item: mock_span)
        monkeypatch.setattr(
            "pytest_otel.logging_handler.emit_stdio_log",
            lambda body, stream, span=None: emitted.append((body, stream, span)),
        )

        report = Mock()
        report.when = "call"
        report.failed = False
        report.capstdout = None
        report.capstderr = "test stderr"
        report.longrepr = None

        plugin._capture_test_output(mock_item, report)

        assert emitted == [("test stderr", STDIO_STREAM_STDERR, mock_span)]

    def test_capture_failure_details(self, monkeypatch):
        """Test that assertion details are captured as stderr logs."""
        from pytest_otel import plugin
        from pytest_otel.logging_handler import STDIO_STREAM_STDERR

        mock_item = Mock()
        mock_item.nodeid = "tests/test_example.py::test_function"
        mock_span = Mock()
        emitted = []

        monkeypatch.setattr(plugin.tracer, "get_test_span", lambda item: mock_span)
        monkeypatch.setattr(
            "pytest_otel.logging_handler.emit_stdio_log",
            lambda body, stream, span=None: emitted.append((body, stream, span)),
        )

        report = Mock()
        report.when = "call"
        report.failed = True
        report.capstdout = None
        report.capstderr = None
        report.longrepr = "assert 1 == 2"

        plugin._capture_test_output(mock_item, report)

        assert emitted == [("assert 1 == 2", STDIO_STREAM_STDERR, mock_span)]
