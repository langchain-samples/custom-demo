# vendor

## `langchain-langgraph-sdk-877b940.tgz`

**Temporary.** `npm pack` of `libs/sdk` on langchain-ai/langgraphjs#2864, which
adds `experimental_useMCPApps` and `experimental_MCPApp` to
`@langchain/langgraph-sdk/react`. Identical to the published 1.11.1 otherwise.

**The filename carries the SDK commit, and must change whenever it is repacked.**
npm keys a `file:` dependency by its path: repack in place and a warm
`node_modules` reports "up to date" and builds against the previous contents.

That alone was not enough for Vercel, which restores `node_modules` from its
build cache and whose `npm install` still reported "up to date in 2s" against
a changed path, type checking against the old build. `../frontend/vercel.json`
pins the install to `npm ci`, which removes `node_modules` first, so no cache
can serve stale contents.

It is here so this branch installs and its tests run before that PR lands. When
it releases, delete this directory and point `@langchain/langgraph-sdk` at the
version on npm. Nothing else changes: the imports are already the ones the
released package will serve.
