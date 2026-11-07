# ABIT product changes

## API maintenance is required for every product change

Every ABIT.AI feature change must keep the supported API and the in-application
API & Developers module current in the same change.

- A new or changed business action must have an appropriate API operation with
  validation, permissions, request and response schemas, and useful errors.
  Pure presentation changes may leave the API unchanged, but still run the checks.
- Use the actual FastAPI routes and Pydantic models as the contract. The developer
  catalogue, OpenAPI document and Postman collection are generated from those
  routes. Do not maintain a separate hard-coded endpoint catalogue.
- Update route descriptions and relevant examples when behavior changes. Keep
  authentication and API-key requirements accurate. Never place credentials,
  private business data or session tokens in schemas or examples.
- Do not hide supported operations with include_in_schema=False to make checks
  pass. Infrastructure or compatibility exceptions must be explicit, narrowly
  scoped and justified in the API coverage policy.
- Preserve API compatibility. Breaking changes require a migration/deprecation
  plan and the corresponding contract and client updates.

## Required checks and release evidence

Run `npm run api:verify` for every change. It validates source coverage, dynamic
catalogue/export regressions, permissions, frontend examples and portal types.
Normal `npm run build` and `npm run build:developers` run the source coverage
gate automatically.

For changes to backend routes, models, authentication, permissions, API exports
or router registration, also run `npm run api:verify:integration`. That launcher
must retain its isolated data/static directories and disabled external services.
Never import the full application against a live environment merely to test it.

Pull requests and releases must pass the API coverage and documentation check.
Do not bypass a failed check or claim a change complete while its API behavior is
missing. Before publishing, validate the registered runtime catalogue, protected
exports and changed feature together using the existing authorization model.

Preserve unrelated source changes and live customizations. Never replace live
data, copy a whole backend over valid live-only changes, or restart a production
service without the established backup and runtime-preservation procedure.
