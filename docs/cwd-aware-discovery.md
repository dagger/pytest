# CWD-aware Pytest discovery

`projects(ws)` delegates discovery of `pytest.ini`, `pyproject.toml`, `tox.ini`,
and `setup.cfg` to `github.com/dagger/polyfill`. Results are cwd-relative: the
cwd and descendants plus at most one nearest enclosing project.

`testAll` tests only the caller's project and descendants, never a strict
ancestor represented by a `..` path. Explicit `test` calls continue to use
`sourcePath`. The module uses the 1.0 schema because the fixture integration
test requires `Directory.asWorkspace`, absent from the previous v0.21.3 schema.

```console
dagger check -l
dagger call pytest discovery-check
dagger call pytest cwd-scope-check
dagger check pytest:test-all
```

`tests/discovery/ancestor` is a runnable repro tree. `cwd-scope-check` runs from
its configless `work` directory, makes the ancestor test fail and proves it is
skipped, then makes `work/app` fail and proves it is tested.
