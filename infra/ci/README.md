# infra/ci/

GitHub Actions only reads workflow files from `.github/workflows/` at the repo root - that's where the actual workflows live (starting with `.github/workflows/ingestion-tests.yml`, added once `ingestion/` had real code and tests). `infra/ci/` stays as the place for anything CI-related that isn't itself a workflow file: shared scripts, composite actions, or notes a workflow references, if that need comes up. Nothing here yet for that reason, not because CI doesn't exist.
