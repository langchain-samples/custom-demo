# vendor

## `langchain-langgraph-sdk-mcp-apps.tgz`

**Temporary.** `npm pack` of `libs/sdk` on langchain-ai/langgraphjs#2864, which
adds `experimental_useMCPApps` and `experimental_MCPApp` to
`@langchain/langgraph-sdk/react`. Identical to the published 1.11.1 otherwise.

It is here so this branch installs and its tests run before that PR lands. When
it releases, delete this directory and point `@langchain/langgraph-sdk` at the
version on npm. Nothing else changes: the imports are already the ones the
released package will serve.
