# Pytest Toolchain — Usage Modes Design

Date: 2026-06-02
Status: Approved
Module: `github.com/dagger/pytest` (dang SDK)

## Problem

The `dagger/pytest` toolchain should serve a spectrum of users, from "install it
and `dagger check` just works" to "I embed this module in my own and bring a
fully-baked container." Today the module only cleanly serves the first case:

- `test(ws)` is hard-coupled to `uv` (`uv run pytest`, `uv pip install ...`), so a
  user who brings a non-uv image cannot use `test`.
- The `pub container: Container = null` field is **dead code** on the current
  `ref-usage` branch — nothing reads it, so a custom base image is silently ignored.
  (It was wired into `base` on `upstream/main`.)
- The bundled `pytest_otel` library is a private `let` (`pytestOtelSource`), so a
  user who wants to integrate it fully by hand has no public handle on it.
- `base` installs `pytest_otel`, and then `test` calls `installPytestOtel(base)`
  again — a redundant double-install.

`installPytestOtel(ctr)` already exists and is tooling-agnostic (auto-detects
`uv` → `pip` → `python -m pip`), which is a good primitive to build on.

## Goals (use cases)

1. **Plain toolchain.** Install the module, run `dagger check`, it works. Only
   knobs: source directory and Python version. Uses the bundled image, installs
   `pytest_otel`, runs pytest.
2. **A bit of customization.** Drop in a custom image (own Python config, uv or
   not) in place of the built-in one. The module still installs `pytest_otel`
   and runs pytest.
3. **Custom / embedded.** The module is embedded in the user's own module and
   `test` still works, with finer control:
   - use the default image;
   - supply a specific image in place of the default;
   - supply an image that already has source + dependencies baked in — the module
     should only inject `pytest_otel` and run, respecting the user's tooling
     (e.g. do not run `uv` if they don't want it);
   - install `pytest_otel` manually — either via `installPytestOtel`, or by
     getting the library as a `Directory` (it is not yet published to PyPI).

## Non-goals

- Publishing `pytest_otel` to PyPI (out of scope; the `Directory` handle is the
  interim answer).
- Supporting package managers beyond `uv` and `pip` now. The `Runner` enum is
  shaped to extend to `POETRY`/`PDM` later, but those are not implemented (YAGNI).
- Auto-detecting the *readiness level* of a container (bare / deps-baked /
  prepared). The level is selected by the user via the entry point they call,
  not inferred.

## Key technical findings (dang capabilities)

Verified against the `vito/dang` compiler source and real `.dang` modules:

- **Runtime branching on container state is supported.** dang can force-evaluate
  an exec and branch on the result, e.g.
  `ctr.withExec(["sh","-c","command -v uv"], expect: ReturnType.ANY).exitCode == 0`
  yields a `Boolean!` usable in `if`. (cf. `ts-sdk.dang` `isSemver`.)
- **dang has real enums** (`enum Runner { ... }`) with type-safe comparison
  (`runner == Runner.UV`). (cf. `test-enum/main.dang`.)
- **String ops** include `.contains`, `==`, `.hasPrefix`, `.split`, `.trim*`.
- **`pub` / `let` functions** can call one another; `let` = private helper.

This makes the "detection phase → dispatch to `testUv`/`testPip`" approach clean
at the dang level rather than buried in a shell script.

## Design

### The three readiness levels

A container can arrive at three levels of readiness. Each maps to a natural entry
point; the module never tries to infer the level.

| Level | Already in the container | Module does | Entry point |
|-------|--------------------------|-------------|-------------|
| **Bare base** | Python tooling only (default alpine, or custom image) | add source → install deps → otel → run | `test` (default) |
| **Deps baked** | Python + deps (lockfile installed, source not yet) | add source → otel → run | `test` with `installDeps: false` |
| **Fully prepared** | Python + source + deps | otel → run | `testUv` / `testPip` directly |

`test` is **workspace-driven**: it builds the container up from a base, using the
`source` the constructor took from the workspace. The `testUv`/`testPip`
primitives are **container-driven**: they take a ready container and assume source
+ deps are present.

### Enum

```
enum Runner {
  AUTO   # detect at runtime: prefer uv, else pip
  UV
  PIP
}
```

### Fields and constructor

A single `new(ws: Workspace!, ...)` constructor sets **every** field, so consumers
and `dagger.json` `customizations` can configure the whole pipeline. `source` is
derived from the workspace; `sourcePath` is a **constructor argument** (not a
stored field) that locates the project within the workspace.

```
new(
  ws: Workspace!,
  sourcePath: String! = "/",
  args: [String!]! = ["-v"],
  pythonVersion: String! = "3.14",
  container: Container = null,
  runner: Runner = Runner.AUTO,
  installDeps: Boolean! = true,
)
```

| Field | Type / default | Notes |
|-------|----------------|-------|
| `source` | `Directory!` (no default) | set in `new` to `ws.directory(sourcePath)`; no `@defaultPath` |
| `args` | `[String!]! = ["-v"]` | extra pytest args |
| `pythonVersion` | `String! = "3.14"` | **only** applies to the default base; ignored when `container` is set |
| `container` | `Container = null` | **re-wired.** When set it *replaces* the default alpine base; Python provisioning (`uv venv -p`) is skipped (the user brought their own Python). |
| `runner` | `Runner = Runner.AUTO` | replaces the `noUv` idea; type-safe and extensible |
| `installDeps` | `Boolean! = true` | when `false`, `test` skips dependency installation (deps-baked level) |

`sourcePath` defaults to `/` (the workspace root), which is correct for an
external consumer whose workspace *is* its project. A consumer that lives **inside
this toolchain's own repo** (the `tests/log_output_toolchain_local` fixture) must
scope into its subdirectory, since the workspace resolves to the repo root —
otherwise pytest silently runs the repo's own `pytest_otel` tests. It does so with
a `dagger.json` customization on the constructor argument:

```json
{ "argument": "sourcePath", "default": "/tests/log_output_toolchain_local" }
```

(The scalar customization key is `default`; `defaultPath` is for `Directory`/`File`
arguments, `defaultAddress` for `Container`.)

### Functions

#### `test(): Void @check`

The OOTB + customization entry point. Takes no arguments — it reads the
constructor-provided `source` (and the other fields). Pipeline:

1. `base` = `container` if set, else the default alpine+uv base provisioned with
   `uv venv -p <pythonVersion>`.
2. add `source` to `/app`, set workdir `/app`.
3. resolve `runner`: if `Runner.AUTO`, probe the container
   (`command -v uv` exit code) → `UV` or `PIP`; otherwise use the field value.
4. if `installDeps`, install project dependencies using the resolved runner
   (see "Dependency installation" below).
5. dispatch to `testUv(ctr)` / `testPip(ctr)` and `.sync`.

#### `testUv(ctr): Container!` / `testPip(ctr): Container!`

Public **run-only primitives**. They assume source + deps are already present in
`ctr` and only:

1. install `pytest_otel` via `installPytestOtel(ctr, runner: UV|PIP)`;
2. run pytest — `uv run pytest <args>` (uv) or `python -m pytest <args>` (pip);

and return the post-run container so callers can compose (export artifacts, etc.)
and choose when to `.sync`. They are not `@check`s (they require a `ctr` arg).

#### `installPytestOtel(ctr, runner: Runner = Runner.AUTO): Container!`

The existing tooling-agnostic primitive, gaining a `runner` override:

- `AUTO` → current behavior: detect `uv` → `pip` → `python -m pip` in a script.
- `UV` / `PIP` → force that tool (so `PIP` works even when `uv` is also present,
  which today's auto-detect would otherwise prefer).

Mounts the bundled `pytest_otel` at `/opt/pytest_otel`, installs it, returns the
container.

#### `pytestOtel: Directory!`

New public handle exposing the bundled `pytest_otel` source `Directory`, for users
who want to integrate the (unpublished) library fully by hand.

### Dependency installation (step 4 of `test`)

Runner-aware, preserving today's behavior for the uv path:

- **uv**: `requirements.txt`-only projects → `uv pip install -r requirements.txt`;
  `pyproject.toml` projects are synced by `uv run` at test time. (Same conditional
  shell snippet as today.)
- **pip**: `requirements.txt` → `pip install -r requirements.txt`; `pyproject.toml`
  → `pip install .`. (Exact form refined during implementation.)

### Base cleanup

- `base` provides **only** Python + tooling. Remove the `pytest_otel` install from
  `base`; otel installation belongs to the run primitives. This also removes the
  current double-install.
- Layer ordering for cache-friendliness (install otel before adding source on the
  default-base path) is an implementation detail to optimize in the plan, not a
  contract.

## Use-case coverage

| Use case | How |
|----------|-----|
| UC1 plain toolchain | `test` with defaults |
| UC2 custom image (drop-in) | `test` + `container` set (+ `runner` if non-uv) |
| UC3a default image, embedded | `test` |
| UC3b specific image | `test` + `container` |
| UC3c image w/ source + deps | `testUv(ctr)` / `testPip(ctr)` directly, or `test` + `installDeps: false` (deps baked, source added by module) |
| UC3d manual otel | `installPytestOtel(ctr, runner)` or the `pytestOtel` directory |

## Error handling

- Default base: `uv venv -p <pythonVersion>` fails fast if the version can't be
  resolved (unchanged).
- `installPytestOtel` with `AUTO` exits non-zero with a clear message if no
  `uv`/`pip`/`python` is found (unchanged). With `UV`/`PIP` forced, it fails if the
  chosen tool is absent — surfacing the user's misconfiguration rather than
  silently falling back.
- `test` with `runner: PIP` (or detected pip) on a container lacking pip will fail
  at the dependency/otel step with the tool's own error; this is intended (no
  silent fallback that contradicts an explicit choice).

## Testing

- Existing fixture under `tests/log_output_toolchain_local/` exercises the default
  path; keep it green.
- Add coverage for:
  - custom `container` (bare base, non-default Python) → `test` runs;
  - `runner: PIP` on a pip-only container;
  - `installDeps: false` against a deps-baked container;
  - `testUv` / `testPip` against a fully-prepared container;
  - `installPytestOtel` with each `Runner` value;
  - `pytestOtel` directory is non-empty / installable.
- `pytest_otel`'s own unit tests under `pytest_otel/tests/` are unaffected.

## Open questions

None outstanding. (`Runner` enum chosen over a `noUv` boolean; `pytestOtel`
directory confirmed wanted.)
