# Compact transport on the existing abit.ai application

The three pinned Core leaves and the existing verified ChatGPT Project default were activated on 16 November 2026. The recorded result is `CORE_COMPACT_RELAY_LIVE_ORIGINAL_FENCES_RETAINED`. Main and QA each stopped and started once; all 72 managed runtime files were verified, and the normal admission gate returned to OPEN50. The sole configuration addition selects the existing owner-verified ChatGPT Project. No model send, ERP write or database restore occurred during this release.

The retained source receipt SHA256 is `2b0bee5c86c408023d1dca6d660259ca073f1773a70877d898c25f4c17179f18`. See `activation.actual.json` for the non-secret receipt. Unknown provider, governance, AutomationRun and admission records retained their recorded digests.

The complete packet and original handoff remain server-held and are returned by the existing scoped `context.read` API. The relay receives a compact immutable identity reference. A subsequent single-leaf compatibility release restores the exact existing Node preamble and its newline boundary; see `../2026-11-16-task-link-prefix-compatibility/` for that final executor export. This directory preserves the first activation's original three-file manifest.

The held original task is not automatically replayed by either release. A technical failed-send observation is separate from task completion, and Developer/QA completion is not claimed here.

Validation on the final canonical source: 18 bound-dispatch/context tests passed. `ABIT_PYTHON=/srv/abit-dev-linux/abit-ai/venv/bin/python npm run api:verify` passed, including source coverage, API documentation/export tests, frontend checks and zero Svelte diagnostics.
