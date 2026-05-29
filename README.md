# Pytest Dagger Toolchain

A toolchain for testing Python application with automatic OpenTelemetry tracing

This toolchain automatically injects a `pytest_otel` library for test tracing visibility in Dagger TUI and Dagger Cloud. No configuration required.

**Usage:**

On a python project, using pytest as the test runner:

- initialize a dagger module if not already done: `dagger init .`
- install the `pytest` toolchain: `dagger toolchain install github.com/dagger/pytest`
- run tests: `dagger check pytest:test`. It will:
  - creates an Alpine based container with `uv` (a uv cache volume is set)
  - prepares Python for the requested version via `uv`, failing fast if it can't be resolved
  - injects the `pytest_otel` dependency for automatic test tracing (before your source is added, so it stays cached)
  - installs your project dependencies (automatically via `uv run` for `pyproject.toml` projects, or from `requirements.txt`)
  - export captured stdout, stderr, and Python logging records as OTel logs
  - run `pytest`

By default the toolchain builds an Alpine + `uv` container. You can instead provide your own container (with Python and `uv` installed); the toolchain reuses its existing virtual environment at `/opt/venv` rather than recreating it.

Example, using `fastly/fastly-cli`:

```console
$ git clone github.com/fastly/fastly-cli

$ cd fastly-cli

$ # Initialize an empty dagger module
$ dagger init

Initialized module fastapi-cli in ~/dev/src/github.com/fastapi/fastapi-cli

$ # Add the pytest toolchain
$ dagger toolchain install github.com/dagger/pytest

toolchain installed

$ # Run tests, with tracing enabled
$ dagger check
✔ pytest:test 17.4s ⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣧⣿⣿⣿⣿⣿⣿⣿⡆⡄⣿⣿⡄ OK
```

![Dagger Cloud test output](dagger-cloud.png)
