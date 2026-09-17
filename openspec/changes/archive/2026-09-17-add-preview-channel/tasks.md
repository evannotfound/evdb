## 1. Version and release validation

- [x] 1.1 Restrict SCM describe to version tags and test rolling-tag exclusion.
- [x] 1.2 Extend release checks for commit-qualified preview assets and exact SCM version matching.

## 2. Preview publication and installation

- [x] 2.1 Add native main-push builds, checksums, attestations, and serialized rolling publication with older-run protection.
- [x] 2.2 Add exclusive installer `--preview` selection using a validated manifest snapshot and preserve stable selection.
- [x] 2.3 Document preview opt-in, exact positional versions, and returning to stable in README; exclude preview from stable changelog comparisons.

## 3. Verification

- [x] 3.1 Run focused installer, release, publication ordering, and SCM regression tests and formatting checks.
- [x] 3.2 Validate OpenSpec artifacts and record any checks requiring GitHub runners.

Validation: 54 focused tests passed across `test_install.py`, `test_release.py`, and `test_preview.py`.
Ruff lint and formatting passed for the changed Python files; `git diff --check` passed. A disposable
Git repository with setuptools-scm 9.2.2 confirmed `1.2.4.dev1+g…` is unchanged by a `preview` tag and
an exact `v1.2.4` tag resolves to `1.2.4`. Strict OpenSpec validation passed. Publication tests execute
the workflow's actual shell with a local fake GitHub release, including late older runs, interrupted
manifest replacement, initial draft publication, and reruns of the same commit. Actual native builds,
GitHub upload semantics, and attestations require the first main-push workflow run.
