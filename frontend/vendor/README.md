# vendor

## `langchain-langgraph-sdk-877b940.tgz`

**Temporary.** `npm pack` of `libs/sdk` on langchain-ai/langgraphjs#2864, which
adds `experimental_useMCPApps` and `experimental_MCPApp` to
`@langchain/langgraph-sdk/react`. Identical to the published 1.11.1 otherwise.

**The filename carries the SDK commit, and must change whenever it is repacked.**
npm keys a `file:` dependency by its path: repack in place and a warm
`node_modules` reports "up to date" and builds against the previous contents.

A build cache that restores `node_modules` defeats that too: `npm install` reports
"up to date" against a changed path and type checks against the old build. Every
build here uses `npm ci`, which removes `node_modules` first (CI, and
`frontend/scripts/build-in-image.sh` for the deployment image).

It is here so this branch installs and its tests run before that PR lands. When
it releases, delete this directory and point `@langchain/langgraph-sdk` at the
version on npm. Nothing else changes: the imports are already the ones the
released package will serve.
