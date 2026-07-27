## 1. SCM Version Source

- [x] 1.1 Configure setuptools-scm as a build-only dependency with a generated evdb version module
- [x] 1.2 Replace the tracked version literal with the generated runtime version import and ignore the
  generated file
- [x] 1.3 Give release check and build jobs complete Git tag history

## 2. Version Contracts

- [x] 2.1 Update package consistency tests for exact releases and SCM-derived development versions
- [x] 2.2 Update release workflow tests to require full-history version discovery
- [x] 2.3 Regenerate the uv lock data for the build configuration

## 3. Verification

- [x] 3.1 Validate OpenSpec artifacts, formatting, lint, unit tests, config tests, and integration collection
- [x] 3.2 Build wheel and source archives and verify they contain and report the resolved version without Git
- [x] 3.3 Build and smoke-test the standalone evdb executable with the resolved version
