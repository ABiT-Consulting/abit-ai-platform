# Retained original parser proof

At17:01:13 UTC, the exact6,341-byte original visible AGOV line was extracted from retained observation881b0fce. Its SHA820b5b96f93d308bff73f8f632bcc2873932a395047f2d19113b07108c45986d matches the protected original-reply proof and prior diagnostic; its original root/reply identities and visible prefix/suffix also match. Python rejects that line with JSONDecodeError at position2699. Node JSON.parse independently rejects it with SyntaxError. The live ddf85 feed parser catches a failed JSON.parse and does not select that line.

This proves a concrete rejection condition for the retained original. It does not prove the entire rendered DOM, the only historical rejection condition, or current browser state. The later6,767-byte correction b7c29aa3e2d7fc11a504b508770b272eea784a0a7a0d46a7dd43b73f24ef38ad parses as JSON but belongs to a distinct later root/reply; it is not an original provider receipt.

The process completed with observed exit0. It read only four pinned files and ran a local Node parser using stdin. No browser/relay/ERP/Core endpoint, database, model request, response edit, registry change or live product mutation occurred. No raw text is included here. The pre-existing postpublication validation remains unchanged.

The reproduction script requires read access to the exact protected retained inputs. It is a parser diagnostic, not an observation or persistence tool.
