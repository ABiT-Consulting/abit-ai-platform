# Fixed Node preamble compatibility activation

The final one-leaf executor export was activated on 16 November 2026 with source SHA256 `454d43e886d433b157e37a9fb533e466d843147362e34b717125aeac23da6bf8` and manifest SHA256 `6923d7241f0c0793510445f6437b9d132f8b68d0868f37b1083acf3016eca315`.

The recorded result is `CORE_TASK_LINK_PREFIX_LIVE_ORIGINAL_FENCES_RETAINED`. Main and QA each stopped and started once during this separate compatibility activation. Node remained unchanged, all 72 runtime files were verified, the existing ERP channel reconnected, and admission returned to OPEN52. The 24 active and 30 unresolved admission records were retained. No model send, ERP write, configuration write or database restore occurred.

See `activation.actual.json` for the non-secret receipt. Its retained source SHA256 is `ab5baaa67fd6ab1d1d97de824c5ec21a61728d4dfc573f3d724f83e5d1f7a51e`.

The frozen transport checks and the actual Python-to-Node feed parser regression passed. This release preserves the one uncertain original request; it does not replay that request or claim PM, Developer or QA completion.
