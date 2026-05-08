import logging
import sys
import time


def test_routes_stdout_and_stderr_to_first_test():
    print("[otel-log-routing:first] stdout from first test")
    print("[otel-log-routing:first] stderr from first test", file=sys.stderr)

    assert True


def test_routes_delayed_output_to_second_test():
    print("[otel-log-routing:second] stdout before sleep")
    time.sleep(0.025)
    print("[otel-log-routing:second] stderr after sleep", file=sys.stderr)

    assert 1 + 1 == 2


class TestNestedSuite:
    def test_keeps_nested_test_logs_under_nested_test(self):
        print("[otel-log-routing:nested] stdout from nested test")

        assert "nested" in "nested test"


def test_routes_logging_records_to_current_test():
    logging.warning("[otel-log-routing:logging] warning from logging")

    assert True
