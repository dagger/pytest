# CWD-aware Pytest discovery

`projects(ws)` finds `pytest.ini`, `pyproject.toml`, `tox.ini`, and `setup.cfg`
roots with `Workspace.findRoots`. Results are cwd-relative: the cwd and
descendants plus at most one nearest enclosing project. The descendant walk
prunes `.venv`, `site-packages`, and `node_modules`, so a dependency's own
config is not picked up as a project of yours.

`testAll` tests only the caller's project and descendants, never a strict
ancestor represented by a `..` path. Explicit `test` calls continue to use
`sourcePath`. The module requires the 1.0 schema: `Workspace.findRoots` is
unavailable before `v1.0.0-beta.10`, and the fixture integration tests need
`Directory.asWorkspace`, absent from the previous v0.21.3 schema.

```console
dagger check -l
dagger call pytest discovery-check
dagger call pytest cwd-scope-check
dagger check pytest:test-all
```

`tests/discovery/ancestor` is a runnable repro tree. `cwd-scope-check` runs from
its configless `work` directory, makes the ancestor test fail and proves it is
skipped, then makes `work/app` fail and proves it is tested.
