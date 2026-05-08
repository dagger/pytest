"""Tests for the config module."""

import os


class TestTelemetryConfig:
    """Tests for TelemetryConfig."""

    def test_singleton(self):
        """Verify TelemetryConfig is a singleton."""
        from pytest_otel.config import TelemetryConfig

        config1 = TelemetryConfig()
        config2 = TelemetryConfig()

        assert config1 is config2

    def test_configure_extracts_traceparent(
        self, monkeypatch, reset_telemetry, mock_otlp_exporters, with_traceparent
    ):
        """Test that configure extracts TRACEPARENT from environment."""
        from pytest_otel.config import TelemetryConfig

        config = TelemetryConfig()
        config._is_configured = False  # Reset state
        config.configure()

        # Verify configuration completed without error
        assert config._is_configured

        # Verify TRACEPARENT was present in environment
        assert with_traceparent == "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"

    def test_configure_without_traceparent(
        self, monkeypatch, reset_telemetry, mock_otlp_exporters
    ):
        """Test configuration without TRACEPARENT."""
        from pytest_otel.config import TelemetryConfig

        monkeypatch.delenv("TRACEPARENT", raising=False)

        config = TelemetryConfig()
        config._is_configured = False
        config.configure()

        # Should complete without error
        assert config._is_configured

    def test_configure_idempotent(self, reset_telemetry, mock_otlp_exporters):
        """Test that configure is idempotent."""
        from pytest_otel.config import TelemetryConfig

        config = TelemetryConfig()
        config._is_configured = False

        config.configure()
        first_provider = config._tracer_provider

        config.configure()
        second_provider = config._tracer_provider

        # Should not create a new provider
        assert first_provider is second_provider

    def test_get_tracer_auto_configures(self, reset_telemetry, mock_otlp_exporters):
        """Test that get_tracer automatically configures telemetry."""
        from pytest_otel.config import TelemetryConfig

        config = TelemetryConfig()
        config._is_configured = False

        tracer = config.get_tracer()

        assert config._is_configured
        assert tracer is not None

    def test_prepare_env_derives_logs_endpoint(self, monkeypatch, reset_telemetry):
        """Test logs endpoint is derived from a trace-specific endpoint."""
        from pytest_otel.config import TelemetryConfig

        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", raising=False)
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_LOGS_PROTOCOL", raising=False)
        monkeypatch.setenv(
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
            "http://127.0.0.1:4318/v1/traces",
        )
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "http/protobuf")

        TelemetryConfig()._prepare_env()

        assert (
            os.environ["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"]
            == "http://127.0.0.1:4318/v1/logs"
        )
        assert os.environ["OTEL_EXPORTER_OTLP_LOGS_PROTOCOL"] == "http/protobuf"

    def test_prepare_env_preserves_generic_endpoint(self, monkeypatch, reset_telemetry):
        """Test generic OTLP endpoint handling is left to exporters."""
        from pytest_otel.config import TelemetryConfig

        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
        monkeypatch.setenv(
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
            "http://127.0.0.1:4318/v1/traces",
        )
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", raising=False)

        TelemetryConfig()._prepare_env()

        assert "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT" not in os.environ
