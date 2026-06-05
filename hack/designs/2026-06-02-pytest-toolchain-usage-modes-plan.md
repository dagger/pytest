# Pytest Toolchain — Usage Modes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `dagger/pytest` toolchain serve three usage modes — plain toolchain, custom-image drop-in, and embedded/custom — by adding a `Runner` enum with auto-detection, wiring up the custom `container`, splitting out run-only `testUv`/`testPip` primitives, an `installDeps` knob, and exposing the bundled `pytest_otel` as a `Directory`.

**Architecture:** A single `dang` module (`main.dang`). `test` stays the workspace-driven `@check` entry point that builds a container up from a base and dispatches to tooling-specific run primitives. `testUv`/`testPip` are container-driven, run-only public primitives (assume source + deps present). `installPytestOtel` remains the shared, tooling-agnostic otel installer, gaining a `runner` override. Runner selection happens at the **dang evaluation level** by probing the container's `exitCode`, not inside a shell script.

**Tech Stack:** Dagger `dang` SDK (`.dang`), Dagger engine `v0.21.3`, `dagger` CLI `v0.21.3`, `uv`, `pip`, `pytest`, the bundled `pytest_otel` package.

---

## Testing reality for this module (read first)

`dang` has **no unit-test framework**. The only way to verify a `dang` module is through the `dagger` CLI:

- `dagger functions` — loads + type-checks the module and prints the public surface (functions, args, enums). This is the fast "does it compile and expose the right API" check. Run from the **module root** (`/home/yves/dev/src/github.com/dagger/pytest-worktrees/ref-usage`).
- `dagger check` from inside a **toolchain fixture** dir (`tests/log_output_toolchain_local`) — runs `pytest:test` end-to-end against a real project. This is the behavioral regression gate for the default uv path.
- `dagger call <fn> ...` — invoke a specific function (function names are kebab-cased: `testUv` → `test-uv`, `installPytestOtel` → `install-pytest-otel`, `pytestOtel` → `pytest-otel`).

All `dagger` commands need the engine (Docker) running and may take tens of seconds on first run (image pulls). "Failing test first" in this plan means: **run the verification command, see it fail/lack the new surface, implement, run again, see it pass.**

> **Engine version note:** the module's `dagger.json` is `v0.21.3` but the fixture `tests/log_output_toolchain_local/dagger.json` is `v0.20.6`. If `dagger check` in the fixture errors on an engine-version mismatch, bump the fixture's `engineVersion` to `v0.21.3` as the first action in that task and commit it separately.

---

## File structure

This module is a single file by established convention; keep it that way.

- **Modify:** `main.dang` — all type/enum/function changes (repo root).
- **Modify:** `README.md` — document the new functions, enum, and fields.
- **Modify (maybe):** `tests/log_output_toolchain_local/dagger.json` — only if an engine-version bump is needed.
- **Create:** `tests/byo_pip/` — a small consumer `dang` module that depends on `pytest` and exercises the non-uv / BYO-container paths (`testPip`, `installPytestOtel` with `PIP`, `installDeps: false`). See Task 8 for the constructor caveat.

---

## Target `main.dang` (reference — built up across tasks)

This is the end state. Each task below builds toward it and keeps the module green.

```
pub description = "A toolchain for testing Python applications with automatic OpenTelemetry tracing"

enum Runner {
  AUTO
  UV
  PIP
}

type Pytest {
  let uvBinary: Directory! {
    currentModule.source.directory("images/uv").dockerBuild.rootfs
  }

  let alpine: Container! {
    currentModule.source.directory("images/alpine").dockerBuild
  }

  let pytestOtelSource = currentModule.source.directory("pytest_otel")

  pub source: Directory!

  pub args: [String!]! = ["-v"]

  pub pythonVersion: String! = "3.14"

  """
  Optional: a custom image that replaces the default Alpine+uv base.
  When set, Python provisioning (`uv venv`) is skipped — you bring your own Python.
  `pythonVersion` then only applies to the default base.
  """
  pub container: Container = null

  """
  Which Python tool to use. AUTO probes the container (prefers uv, else pip).
  """
  pub runner: Runner = Runner.AUTO

  """
  Whether `test` installs project dependencies before running. Set false when
  the container already has them baked in.
  """
  pub installDeps: Boolean! = true

  new(
    ws: Workspace!,
    sourcePath: String! = "/",
    args: [String!]! = ["-v"],
    pythonVersion: String! = "3.14",
    container: Container = null,
    runner: Runner = Runner.AUTO,
    installDeps: Boolean! = true,
  ) {
    self.source = ws.directory(sourcePath)
    self.args = args
    self.pythonVersion = pythonVersion
    self.container = container
    self.runner = runner
    self.installDeps = installDeps
    self
  }

  """
  The default Alpine + uv base: Python tooling only (no pytest_otel here).
  """
  let defaultBase: Container! {
    alpine
      .withExec(["apk", "add", "libgcc"])
      .withEnvVariable("PYTHONUNBUFFERED", "1")
      .withEnvVariable("PATH", "/root/.local/bin:/usr/local/bin:$PATH", expand: true)
      .withEnvVariable("UV_LINK_MODE", "copy")
      .withEnvVariable("UV_PROJECT_ENVIRONMENT", "/opt/venv")
      .withEnvVariable("VIRTUAL_ENV", "/opt/venv")
      .withMountedCache("/root/.cache/uv", cacheVolume("pytest-toolchain-uv"))
      .withDirectory("/usr/local/bin", uvBinary, include: ["uv*"])
      .withExec(["uv", "venv", "/opt/venv", "-p", pythonVersion])
  }

  """
  The base used by `test`: the custom container if provided, else the default.
  """
  let base: Container! {
    if (container != null) {
      container
    } else {
      defaultBase
    }
  }

  """
  Test a Python project with pytest and OpenTelemetry tracing.

  Builds a container from the base (custom or default), adds your source,
  optionally installs dependencies, then dispatches to the uv or pip run path
  (auto-detected unless `runner` is set explicitly).
  """
  pub test(): Void @check {
    let prepared = addSourceDir(base, source)
    let chosen = resolveRunner(prepared)
    let result =
      if (chosen == Runner.UV) {
        testUv(installDepsUv(prepared))
      } else {
        testPip(installDepsPip(prepared))
      }
    result.sync
    null
  }

  """
  Resolve the effective runner: when `runner` is AUTO, probe the container
  (prefer uv, else pip); otherwise use the configured value.
  """
  let resolveRunner(ctr: Container!): Runner {
    if (runner == Runner.AUTO) {
      if (ctr.withExec(["sh", "-c", "command -v uv"], expect: ReturnType.ANY).exitCode == 0) {
        Runner.UV
      } else {
        Runner.PIP
      }
    } else {
      runner
    }
  }

  let installDepsUv(ctr: Container!): Container! {
    if (installDeps == true) {
      # requirements.txt-only projects need explicit install; pyproject.toml
      # projects are synced by `uv run` at test time.
      ctr.withExec([
        "sh", "-c",
        "if [ -f requirements.txt ] && [ ! -f pyproject.toml ]; then uv pip install -r requirements.txt; fi",
      ])
    } else {
      ctr
    }
  }

  let installDepsPip(ctr: Container!): Container! {
    if (installDeps == true) {
      ctr.withExec([
        "sh", "-c",
        "if [ -f requirements.txt ]; then pip install -r requirements.txt; elif [ -f pyproject.toml ]; then pip install .; fi",
      ])
    } else {
      ctr
    }
  }

  let addSourceDir(
    ctr: Container!,
    dir: Directory!,
  ): Container! {
    ctr
      .withDirectory("/app", dir)
      .withWorkdir("/app")
  }

  """
  Run pytest with uv on a container that already has source + deps. Installs
  pytest_otel (forced uv), then `uv run pytest`. Returns the post-run container.
  """
  pub testUv(
    ctr: Container!,
  ): Container! {
    installPytestOtel(ctr, runner: Runner.UV)
      .withExec(["uv", "run", "pytest"] + args)
  }

  """
  Run pytest with pip on a container that already has source + deps. Installs
  pytest_otel (forced pip), then `python -m pytest`. Returns the post-run container.
  """
  pub testPip(
    ctr: Container!,
  ): Container! {
    installPytestOtel(ctr, runner: Runner.PIP)
      .withExec(["python", "-m", "pytest"] + args)
  }

  """
  Install pytest_otel into a container you manage yourself.

  `runner` AUTO detects the tool at runtime (uv → pip → python -m pip); UV or PIP
  forces that tool even when others are present. Returns the container with
  pytest_otel installed; run pytest yourself on it.
  """
  pub installPytestOtel(
    ctr: Container!,
    runner: Runner = Runner.AUTO,
  ): Container! {
    let installCmd =
      if (runner == Runner.UV) {
        "uv pip install /opt/pytest_otel"
      } else if (runner == Runner.PIP) {
        "pip install /opt/pytest_otel"
      } else {
        "if command -v uv >/dev/null 2>&1; then uv pip install /opt/pytest_otel; "
          + "elif command -v pip >/dev/null 2>&1; then pip install /opt/pytest_otel; "
          + "elif command -v python3 >/dev/null 2>&1; then python3 -m pip install /opt/pytest_otel; "
          + "elif command -v python >/dev/null 2>&1; then python -m pip install /opt/pytest_otel; "
          + "else echo 'pytest toolchain: could not find uv, pip, or python to install pytest_otel' >&2; exit 1; fi"
      }
    ctr
      .withDirectory("/opt/pytest_otel", pytestOtelSource)
      .withExec(["sh", "-c", "set -e; " + installCmd])
  }

  """
  The bundled pytest_otel library, as a Directory, for fully-manual integration
  (the package is not yet published to PyPI).
  """
  pub pytestOtel: Directory! {
    pytestOtelSource
  }
}
```

---

## Task 0: Establish a green baseline

**Files:** none (verification only)

- [ ] **Step 1: Confirm the module loads and note the current surface**

Run (from repo root):
```bash
dagger functions
```
Expected: lists `test` and `install-pytest-otel`; no `test-uv`/`test-pip`/`pytest-otel`; no `Runner` enum. Save this output for comparison.

- [ ] **Step 2: Confirm the default path is green before any change**

Run:
```bash
cd tests/log_output_toolchain_local && dagger check && cd -
```
Expected: `pytest:test` passes (PASS/OK). If it errors on engine version, bump `tests/log_output_toolchain_local/dagger.json` `"engineVersion"` to `"v0.21.3"`, commit that as its own patch, and re-run until green.

---

## Task 1: Add the `Runner` enum and the `runner` / `installDeps` fields

**Files:**
- Modify: `main.dang` (top-level enum; `Pytest` fields; `new` constructor)

These are additive — defaults preserve current behavior, so the default path stays green.

- [ ] **Step 1: Add the enum above `type Pytest`**

Insert after the `pub description = ...` line:
```
enum Runner {
  AUTO
  UV
  PIP
}
```

- [ ] **Step 2: Add the two fields**

After the existing `pub container: Container = null` line, add:
```
  """
  Which Python tool to use. AUTO probes the container (prefers uv, else pip).
  """
  pub runner: Runner = Runner.AUTO

  """
  Whether `test` installs project dependencies before running. Set false when
  the container already has them baked in.
  """
  pub installDeps: Boolean! = true
```

- [ ] **Step 3: Extend the constructor**

Replace the `new(...) { ... }` block with:
```
  new(
    ws: Workspace!,
    sourcePath: String! = "/",
    args: [String!]! = ["-v"],
    pythonVersion: String! = "3.14",
    container: Container = null,
    runner: Runner = Runner.AUTO,
    installDeps: Boolean! = true,
  ) {
    self.source = ws.directory(sourcePath)
    self.args = args
    self.pythonVersion = pythonVersion
    self.container = container
    self.runner = runner
    self.installDeps = installDeps
    self
  }
```

- [ ] **Step 4: Verify it type-checks and exposes the enum**

Run (repo root):
```bash
dagger functions
```
Expected: still lists `test`; the constructor/`test` now shows `runner` (type `Runner`) and `install-deps` args. No errors.

- [ ] **Step 5: Verify the default path still passes**

Run:
```bash
cd tests/log_output_toolchain_local && dagger check && cd -
```
Expected: PASS (unchanged — `runner` defaults to AUTO, `installDeps` to true, nothing reads them yet).

- [ ] **Step 6: Commit**

```bash
stg new -m "feat(pytest): add Runner enum and runner/installDeps fields" --signoff
git add main.dang
stg refresh
```

---

## Task 2: Add the `runner` override to `installPytestOtel`

**Files:**
- Modify: `main.dang` (`installPytestOtel`)

- [ ] **Step 1: Replace the body of `installPytestOtel`**

Replace the whole `pub installPytestOtel(ctr: Container!): Container! { ... }` function with:
```
  """
  Install pytest_otel into a container you manage yourself.

  `runner` AUTO detects the tool at runtime (uv → pip → python -m pip); UV or PIP
  forces that tool even when others are present. Returns the container with
  pytest_otel installed; run pytest yourself on it.
  """
  pub installPytestOtel(
    ctr: Container!,
    runner: Runner = Runner.AUTO,
  ): Container! {
    let installCmd =
      if (runner == Runner.UV) {
        "uv pip install /opt/pytest_otel"
      } else if (runner == Runner.PIP) {
        "pip install /opt/pytest_otel"
      } else {
        "if command -v uv >/dev/null 2>&1; then uv pip install /opt/pytest_otel; "
          + "elif command -v pip >/dev/null 2>&1; then pip install /opt/pytest_otel; "
          + "elif command -v python3 >/dev/null 2>&1; then python3 -m pip install /opt/pytest_otel; "
          + "elif command -v python >/dev/null 2>&1; then python -m pip install /opt/pytest_otel; "
          + "else echo 'pytest toolchain: could not find uv, pip, or python to install pytest_otel' >&2; exit 1; fi"
      }
    ctr
      .withDirectory("/opt/pytest_otel", pytestOtelSource)
      .withExec(["sh", "-c", "set -e; " + installCmd])
  }
```
This replaces the previous `withNewFile`-based script. AUTO reproduces the old detection order exactly; UV/PIP force a tool.

- [ ] **Step 2: Verify surface + default path**

Run:
```bash
dagger functions
cd tests/log_output_toolchain_local && dagger check && cd -
```
Expected: `install-pytest-otel` now shows a `runner` arg (type `Runner`, default AUTO); `pytest:test` still PASS (it calls `installPytestOtel(base)` → AUTO → detects uv, same as before).

- [ ] **Step 3: Commit**

```bash
stg new -m "feat(pytest): add runner override to installPytestOtel" --signoff
git add main.dang
stg refresh
```

---

## Task 3: Expose `pytest_otel` as a public `Directory`

**Files:**
- Modify: `main.dang` (add `pytestOtel` field)

- [ ] **Step 1: Add the public field**

Add inside `type Pytest` (e.g. just after `installPytestOtel`):
```
  """
  The bundled pytest_otel library, as a Directory, for fully-manual integration
  (the package is not yet published to PyPI).
  """
  pub pytestOtel: Directory! {
    pytestOtelSource
  }
```

- [ ] **Step 2: Verify the directory is exposed and non-empty**

Run (repo root):
```bash
dagger call pytest-otel entries
```
Expected: prints the `pytest_otel` top-level entries (`pyproject.toml`, `src`, `README.md`, `tests`, `uv.lock`). No errors.

- [ ] **Step 3: Commit**

```bash
stg new -m "feat(pytest): expose bundled pytest_otel as a Directory" --signoff
git add main.dang
stg refresh
```

---

## Task 4: Add the run-only primitives `testUv` / `testPip`

**Files:**
- Modify: `main.dang` (add `testUv`, `testPip`)

Additive — nothing calls them yet, so the default path is unaffected.

- [ ] **Step 1: Add both functions**

Add inside `type Pytest` (after `test`, before `addSourceDir`):
```
  """
  Run pytest with uv on a container that already has source + deps. Installs
  pytest_otel (forced uv), then `uv run pytest`. Returns the post-run container.
  """
  pub testUv(
    ctr: Container!,
  ): Container! {
    installPytestOtel(ctr, runner: Runner.UV)
      .withExec(["uv", "run", "pytest"] + args)
  }

  """
  Run pytest with pip on a container that already has source + deps. Installs
  pytest_otel (forced pip), then `python -m pytest`. Returns the post-run container.
  """
  pub testPip(
    ctr: Container!,
  ): Container! {
    installPytestOtel(ctr, runner: Runner.PIP)
      .withExec(["python", "-m", "pytest"] + args)
  }
```

- [ ] **Step 2: Verify the surface**

Run (repo root):
```bash
dagger functions
```
Expected: now lists `test-uv` and `test-pip`, each taking a `ctr` (`Container`) arg and returning `Container`. No type errors.

- [ ] **Step 3: Verify the default path still passes**

Run:
```bash
cd tests/log_output_toolchain_local && dagger check && cd -
```
Expected: PASS (unchanged — `test` doesn't use the primitives yet).

- [ ] **Step 4: Commit**

```bash
stg new -m "feat(pytest): add testUv/testPip run-only primitives" --signoff
git add main.dang
stg refresh
```

---

## Task 5: Re-wire `base` and rewrite `test` to dispatch

**Files:**
- Modify: `main.dang` (`base` split into `defaultBase` + `base`; rewrite `test`; add `resolveRunner`, `installDepsUv`, `installDepsPip`)

This is the integrating task. It connects the custom `container`, runner detection, the `installDeps` knob, and the run primitives — and removes the `pytest_otel` double-install from `base`.

- [ ] **Step 1: Split `base` into `defaultBase` + `base`**

Replace the current `let base: Container! { alpine ... .withExec(["uv", "pip", "install", "/opt/pytest_otel"]) }` block with:
```
  """
  The default Alpine + uv base: Python tooling only (no pytest_otel here).
  """
  let defaultBase: Container! {
    alpine
      .withExec(["apk", "add", "libgcc"])
      .withEnvVariable("PYTHONUNBUFFERED", "1")
      .withEnvVariable("PATH", "/root/.local/bin:/usr/local/bin:$PATH", expand: true)
      .withEnvVariable("UV_LINK_MODE", "copy")
      .withEnvVariable("UV_PROJECT_ENVIRONMENT", "/opt/venv")
      .withEnvVariable("VIRTUAL_ENV", "/opt/venv")
      .withMountedCache("/root/.cache/uv", cacheVolume("pytest-toolchain-uv"))
      .withDirectory("/usr/local/bin", uvBinary, include: ["uv*"])
      .withExec(["uv", "venv", "/opt/venv", "-p", pythonVersion])
  }

  """
  The base used by `test`: the custom container if provided, else the default.
  """
  let base: Container! {
    if (container != null) {
      container
    } else {
      defaultBase
    }
  }
```
Note: the two trailing lines that installed `pytest_otel` into `base` are intentionally **dropped** — otel is now installed by the run primitives (`installPytestOtel`), removing the double-install.

- [ ] **Step 2: Rewrite `test`**

Replace the current `pub test(ws: Workspace!): Void @check { ... }` body with:
```
  pub test(): Void @check {
    let prepared = addSourceDir(base, source)
    let chosen = resolveRunner(prepared)
    let result =
      if (chosen == Runner.UV) {
        testUv(installDepsUv(prepared))
      } else {
        testPip(installDepsPip(prepared))
      }
    result.sync
    null
  }
```

- [ ] **Step 3: Add the three helpers**

Add inside `type Pytest` (e.g. right after `test`):
```
  """
  Resolve the effective runner: when `runner` is AUTO, probe the container
  (prefer uv, else pip); otherwise use the configured value.
  """
  let resolveRunner(ctr: Container!): Runner {
    if (runner == Runner.AUTO) {
      if (ctr.withExec(["sh", "-c", "command -v uv"], expect: ReturnType.ANY).exitCode == 0) {
        Runner.UV
      } else {
        Runner.PIP
      }
    } else {
      runner
    }
  }

  let installDepsUv(ctr: Container!): Container! {
    if (installDeps == true) {
      ctr.withExec([
        "sh", "-c",
        "if [ -f requirements.txt ] && [ ! -f pyproject.toml ]; then uv pip install -r requirements.txt; fi",
      ])
    } else {
      ctr
    }
  }

  let installDepsPip(ctr: Container!): Container! {
    if (installDeps == true) {
      ctr.withExec([
        "sh", "-c",
        "if [ -f requirements.txt ]; then pip install -r requirements.txt; elif [ -f pyproject.toml ]; then pip install .; fi",
      ])
    } else {
      ctr
    }
  }
```

- [ ] **Step 4: Verify the full default path**

Run:
```bash
dagger functions
cd tests/log_output_toolchain_local && dagger check && cd -
```
Expected: no type errors. `pytest:test` PASS. This now exercises the whole new uv path end-to-end: `container == null` ⇒ `defaultBase`, source added, `runner` AUTO ⇒ probe finds `uv` ⇒ `Runner.UV`, `installDepsUv`, `testUv` ⇒ `installPytestOtel(..., UV)` + `uv run pytest`. The test spans must still appear (otel installed via the primitive).

- [ ] **Step 5: Commit**

```bash
stg new -m "feat(pytest): wire custom container and dispatch test to uv/pip" --signoff
git add main.dang
stg refresh
```

---

## Task 6: Update the README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a "Usage modes" section**

After the existing `installPytestOtel` paragraph (the one ending with "...run `pytest` yourself with tracing enabled."), add:
```markdown

## Usage modes

The toolchain serves three levels of control:

1. **Plain toolchain** — install it and run `dagger check`. Your project is taken
   from the workspace automatically; `pythonVersion` (and `sourcePath`, to target a
   subdirectory of the workspace) are the only knobs commonly set. Everything else
   just works against the bundled Alpine + `uv` base.
2. **Custom image (drop-in)** — set `container` to your own image (your Python, your
   tooling, `uv` or not). It replaces the default base; the module still installs
   `pytest_otel` and runs pytest. If your image is not `uv`-based, set `runner` to
   `PIP` (or leave `AUTO` to auto-detect).
3. **Embedded / custom** — call the building blocks directly:
   - `test` — full pipeline (base → source → deps → otel → pytest), with
     `installDeps: false` to skip dependency installation when your image already
     has them.
   - `testUv` / `testPip` — run-only: given a container that already has source +
     dependencies, install `pytest_otel` and run pytest. Use these to skip
     detection entirely.
   - `installPytestOtel(ctr, runner)` — install only `pytest_otel` into your
     container (`AUTO` detects the tool; `UV`/`PIP` force one), then run pytest
     yourself.
   - `pytestOtel` — get the bundled `pytest_otel` library as a `Directory` for
     fully-manual integration (it is not yet published to PyPI).

`runner` is `AUTO` | `UV` | `PIP` (default `AUTO`: prefer `uv`, else `pip`).
```

- [ ] **Step 2: Sanity-check the rendered Markdown**

Run:
```bash
sed -n '/## Usage modes/,/pytestOtel/p' README.md
```
Expected: the new section prints intact.

- [ ] **Step 3: Commit**

```bash
stg new -m "docs(pytest): document usage modes and new functions" --signoff
git add README.md
stg refresh
```

---

## Task 7: Verify the otel-only and directory paths manually

**Files:** none (verification only)

These confirm the agnostic primitive and the directory handle work outside the default flow.

- [ ] **Step 1: `installPytestOtel` AUTO against a plain python image**

Run (repo root):
```bash
dagger call install-pytest-otel \
  --ctr=$(dagger core container from --address=python:3.12-slim) \
  with-exec --args="python","-c","import pytest_otel; print('ok')" \
  stdout
```
If the inline `--ctr=$(...)` form is rejected by your CLI version, instead add a temporary throwaway `@check` in `tests/byo_pip` (Task 8) that does the same and run `dagger check`.
Expected: prints `ok` (pytest_otel importable; AUTO picked pip since the image has no uv).

- [ ] **Step 2: `pytestOtel` directory is installable**

Run:
```bash
dagger call pytest-otel entries
```
Expected: includes `pyproject.toml` and `src`.

> No commit — verification only.

---

## Task 8: Add a consumer fixture for the BYO-container / pip paths

**Files:**
- Create: `tests/byo_pip/dagger.json`
- Create: `tests/byo_pip/main.dang`

**Constructor note (resolved):** the run primitives and `installPytestOtel` don't
use `source`, but the `Pytest` constructor requires `ws: Workspace!`. The concern
was whether a plain (non-toolchain) **dependency** consumer like `byo_pip` could
still construct it. Verified during implementation: `dagger check` against
`tests/byo_pip` passes with `ws` kept **required** — dagger auto-injects the
workspace for dependency consumers too, so no constructor change is needed. The
once-considered `ws: Workspace = null` fallback to `currentModule.source` proved
unnecessary and was not implemented.

- [ ] **Step 1: Create `tests/byo_pip/dagger.json`**

```json
{
  "name": "byo-pip",
  "engineVersion": "v0.21.3",
  "sdk": {
    "source": "dang"
  },
  "dependencies": [
    {
      "name": "pytest",
      "source": "../../"
    }
  ]
}
```

- [ ] **Step 2: Create `tests/byo_pip/main.dang`**

```
type ByoPip {
  """
  A minimal pip-based project container (no uv): python:3.12-slim with a
  requirements.txt and a trivial passing test.
  """
  let project: Container! {
    container
      .from("python:3.12-slim")
      .withNewFile("/app/requirements.txt", "pytest>=7.0.0\n")
      .withNewFile("/app/test_ok.py", "def test_ok():\n    assert True\n")
      .withWorkdir("/app")
  }

  """
  testPip on a ready pip container: deps installed here, then the primitive
  installs pytest_otel (forced pip) and runs `python -m pytest`.
  """
  pub byoPip(): Void @check {
    let ready = project.withExec(["pip", "install", "-r", "requirements.txt"])
    pytest.testPip(ready).sync
    null
  }

  """
  installPytestOtel with PIP forces pip even on a plain python image.
  """
  pub otelPip(): Void @check {
    pytest
      .installPytestOtel(project, runner: Runner.PIP)
      .withExec(["python", "-c", "import pytest_otel"])
      .sync
    null
  }
}
```

- [ ] **Step 3: Verify the consumer module compiles and its checks pass**

Run:
```bash
cd tests/byo_pip && dagger functions && dagger check && cd -
```
Expected: `dagger functions` lists `byo-pip` / `otel-pip`; `dagger check` runs both `@check`s and they PASS. `byoPip` proves the pip run path + `testPip`; `otelPip` proves forced-pip otel install.

> **If `dagger.json` dependency wiring needs adjustment** (e.g. the dependency must
> be added with `dagger install ../../` rather than hand-written JSON), use
> `dagger install` from `tests/byo_pip` and let it write the `dependencies` block,
> then re-run. Reference: the verified dang dependency-modules example uses
> `"dependencies": [{"name": "...", "source": "./..."}]` and calls the dependency
> by its lowercased name (`pytest.testPip(...)`).

- [ ] **Step 4: Commit**

```bash
stg new -m "test(pytest): add BYO-container pip fixture" --signoff
git add tests/byo_pip/dagger.json tests/byo_pip/main.dang
stg refresh
```

---

## Self-Review (completed during planning)

**1. Spec coverage** — every spec section maps to a task:
- Runner enum + fields → Task 1. Re-wired `container` + base split + dispatch → Task 5. `testUv`/`testPip` run-only primitives → Task 4. `installPytestOtel` runner override → Task 2. `pytestOtel: Directory!` → Task 3. `installDeps` knob → Tasks 1 + 5 (`installDepsUv`/`installDepsPip`). Base double-install cleanup → Task 5 (otel dropped from base). Use-case coverage → README Task 6. Testing checklist → Tasks 0, 5, 7, 8.
- Dependency-installation behavior (uv: requirements-only explicit, pyproject via `uv run`; pip: requirements then `pip install .`) → `installDepsUv`/`installDepsPip` in Task 5.
- Error handling (forced runner fails loudly, no silent fallback) → `installPytestOtel` UV/PIP branches in Task 2 (no fallback) and the pip dep step in Task 5.

**2. Placeholder scan** — no TBD/TODO/"handle edge cases"/"similar to". Every code step shows full code; every verification step shows the exact command + expected output. Deferred-only items are explicitly conditional (engine-version bump; `ws`-optional in Task 8), each with concrete instructions.

**3. Type consistency** — `Runner` (AUTO/UV/PIP) used identically everywhere; `resolveRunner: Runner`; `installDepsUv`/`installDepsPip`/`addSourceDir`/`base`/`defaultBase` all `Container!`; `testUv`/`testPip` return `Container!`; `installPytestOtel(ctr, runner=AUTO): Container!` signature matches its callers in `testUv`/`testPip` and the README. `if`-branches return matching types (the dang same-type-branch constraint), verified in `test` (`Container!`), `resolveRunner` (`Runner`), and `installPytestOtel` (`String!`).

## Known risks / open points

- **Programmatic instantiation (`ws`)** — the one genuinely unverified Dagger-platform detail; Task 8's caveat + Step 0 handle it with a regression guard. This is a refinement discovered during planning, not in the approved spec.
- **`uv run` syncs** — on the uv path, `uv run pytest` re-syncs a `pyproject.toml` project even when `installDeps: false`. For a fully air-gapped prepared image, prefer `testPip` or run `pytest` directly. Acceptable per the spec's deferral of "exact dep-install form".
- **Engine-version skew** between module (`v0.21.3`) and the existing fixture (`v0.20.6`) — handled in Task 0.
