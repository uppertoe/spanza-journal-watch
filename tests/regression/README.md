# Regression baseline tests

This test suite protects public route rendering and newsletter output while refactoring.
It now also includes backend/auth guard behavior for project-owned routes.
It also includes backend workflow checks for CSV upload/header edit/process, newsletter send queueing, and stats math.

## Baseline generation (one-time, repeatable)

Use the current local DB content to generate:

- anonymized fixtures
- normalized HTML snapshots
- a snapshot manifest

Run inside the Django container:

`python manage.py generate_regression_baseline`

Equivalent docker command from project root:

`docker compose -f local.yml run --rm django python manage.py generate_regression_baseline`

Generated files:

- `spanza_journal_watch/fixtures/regression_baseline.json`
- `tests/regression/snapshots/*.html`
- `tests/regression/snapshots/manifest.json`

## Running regression tests

`pytest tests/regression -q`

Equivalent docker command from project root:

`docker compose -f local.yml exec -T django pytest tests/regression -q`

Current baseline suite: 26 passing tests.

Route-to-test mapping:

- `tests/regression/ROUTE_COVERAGE.md`

## Browser auth probe

For login/session regressions that only show up in a real browser, use:

`NODE_PATH=/tmp/codex-playwright/node_modules npm run login:probe`

Details and environment variables:

- `tests/regression/browser/README.md`

## Notes

- Only project-owned routes are covered in this phase.
- Backend/auth routes are intentionally deferred.
- Fixtures are anonymized (`@example.test`) and token fields are stabilized.
