Declared attachment context
===========================

The authenticated governance gateway exposes `attachment.read` and
`attachment.present` through its existing call API. Each takes a File ID from
the current work packet. `attachment.read` accepts an optional byte `offset`;
`attachment.present` accepts an optional one-based `page` and character
`text_offset`. The fixed native reader has no URL or path argument.

The current Project, task revision, role, assignment, lease, native control
intent and loaded ERP guards are checked through the gateway's canonical
authority function before and after the native byte read, and again after
presentation. Existing operation handlers retain their keyword contract; only
explicitly registered handlers receive the server-only authority callback.

Native reads reject undeclared/public/cross-resource Files, credential names,
symlinks, hard links, oversized files and registered-hash mismatches. The Core
checks exact binary length, canonical base64 and SHA256 independently. The
maximum source size is 2,000,000 bytes and the maximum byte chunk is 196,608.

Presentation runs a fixed parser in a bubblewrap namespace with no network,
private input files, an isolated temporary filesystem, CPU/time/output limits
and the existing delegated 512MiB/64-process job budget. UTF-8 and PDF text is
paged at 12,000 characters. PDFs are limited to 200 pages and encrypted PDFs
are rejected. PNG/JPEG input is limited to 12 million pixels. The derived JPEG
preview is at most 1280px per side and 98,304 bytes; source and preview hashes
are distinct. The authenticated MCP edge emits an actual image content block
only for the matching completed File-ID response and verified preview bytes.

Attachment content is data, including any instructions it contains. It does
not alter server-owned authority. The owner's unrelated account tools remain
outside this gateway boundary.

The native reverse-channel runtime must be deployed as a new version with the
fixed attachment RPC and IT-plan/implementation-receipt RPC allowlists. The
previous private runtime, vendor files, seed provenance and receipts remain
preserved. Source tests and an isolated parser check are separate from a live
provider/ERP acceptance run.
