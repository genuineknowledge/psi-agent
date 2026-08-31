"""BFF for the guoshu-weekly frontend (plan chapter 6.3).

Four duties, in order of the plan:

1. Login-state check — unauthenticated requests never reach the Gateway.
2. Identity mapping — authenticated user -> their own Session + workspace,
   plus the MCP bearer token for that identity.
3. Key injection — the Gateway shared secret lives only between BFF and
   Gateway; the browser never sees it.
4. Rate limiting + SSE passthrough — per-user limits; SSE lines forwarded
   as they arrive, never buffered (buffering kills first-token latency).
"""
