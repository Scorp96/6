# V4 browser engine adapter

## Goal
Adapt the existing Windows MCP or Chrome Use actor driver to the V4 `ChatGptGuiEngine` contract without treating an unparsed snapshot as a captured response.

## Steps
- [x] Add failing tests for auth blocking, URL identity, structured parser capture, and parser-free ambiguity.
- [x] Implement an async-driver adapter with explicit injected auth probe and response parser.
- [x] Document how to inject the adapter into `ChatGptGuiEngine` and keep legacy drivers behind the seam.
- [x] Run focused, full V4, full bridge, compilation, and diff checks.
- [x] Commit and push.

## Boundary
No browser is launched by these tests or by the adapter constructor. A real canary still requires explicit user authorization and a live logged-in session.

