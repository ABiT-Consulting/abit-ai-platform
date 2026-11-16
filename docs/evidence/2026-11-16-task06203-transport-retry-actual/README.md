# Activated existing-relay transport Retry cohort

These are the exact seven installed modules, controller and runtime manifest
used for the Core72-to-Core74 activation on 2026-11-16. Every module was read
back from the installed runtime and checked against the retained runtime74
receipt. The manifest was reconstructed by restoring the actual deployed
recovery pin; its bytes hash exactly to the original intent pin.

- Actual deployed manifest: `e3658fcf76e0eb5adaa1973e9e736d2a1b88b5fa70cd1d1f6ac5b31afaf2ce42`.
- Actual deployed recovery: `d22c912a756ef6f07034f0c0a50a44f94b9b28b795bb368c80e5e536ba298afd`.
- Actual controller: `c1ccb8b324a7eebee8ab8e8d7122a3464624525dce3f645d71a8396e458e916b`.
- Runtime74 manifest: `f00730943631357f0cbc84b669c370668f319b2f7927e0a4a69cc7fdd1d797da`.

The similarly named development package was edited after activation. Its
`db1f20b9b03696be153dcd1ae186c2bf04fdcd97ce1a425be94c58b782742b35`
manifest and `31db8180e7963cd9e047a82db2139a688d95da74262b232a7d0f0b1936541fa3`
recovery are unactivated and are deliberately excluded from this release evidence.
Do not execute this historical controller again; its original operation receipt
already exists.

The fix records an exact, row-local failed outgoing too-long request as a negative
receipt. An explicit authenticated Native Task Retry may create one fresh
generation only after that proof, zero original gateway work, exact original
identity/fence checks and Native acknowledgement. It never resends an unknown
original. Installation itself sent no model request and made no ERP or
configuration change; Main and QA each stopped and started once, while original
journals and leases were preserved.

The public readback receipts contain explicit allowlists only. They omit prompts,
packets, capabilities, cookies, session/account details and request/response
bodies. They show the accepted retry sent one 2841-character prompt through the
existing ABiT Inhouse ChatGPT project, read its exact frozen context, and published
one PM result as Blocked. The original and retry remain two distinct historical
records, each dispatched once; no concurrent Task invocation remains. The PM
identified an unconfigured repository profile. Developer and QA completion are
not claimed.

Canonical source changes in this PR apply only the installed before-to-after
hunks to the PR15 source baseline. They preserve other canonical/runtime
customizations rather than replacing either whole application tree.
