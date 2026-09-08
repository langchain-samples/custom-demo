"""The deep agent itself: what runs on every chat turn.

`agent` builds it (context schema, middleware, backends), `prompt` assembles what
the model reads, `tools/` is the catalogue it may call, `mcp_servers` adds the
remote ones, `widgets` is the agent-to-frontend contract, and `mocking` lets a
dataset row stand in for any tool.

Nothing here imports from `provisioning/`: building a demo depends on the
runtime, never the other way round.
"""
