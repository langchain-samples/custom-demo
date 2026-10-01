#!/bin/sh
# Build the SPA into $SPA_DIR inside the deployment image, then remove Node and the
# sources so neither ships. langgraph.json runs this from `dockerfile_lines`, which
# land before the Python package is added, so the SPA has its own copy at /spa-src.
set -eu

NODE_VERSION=22.12.0
case "$(uname -m)" in
  x86_64) NODE_ARCH=x64 ;;
  aarch64 | arm64) NODE_ARCH=arm64 ;;
  *) echo "build-in-image.sh: no Node build for $(uname -m)" >&2; exit 1 ;;
esac

NODE_DIR=/tmp/node
mkdir -p "$NODE_DIR"
# The base image has Python but no curl or wget.
python3 -c 'import shutil, sys, urllib.request; shutil.copyfileobj(urllib.request.urlopen(sys.argv[1]), sys.stdout.buffer)' \
  "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.gz" \
  | tar -xz -C "$NODE_DIR" --strip-components=1
export PATH="$NODE_DIR/bin:$PATH"

cd /spa-src
npm ci --no-audit --no-fund
npx vite build --base=/ui/ --outDir "$SPA_DIR" --emptyOutDir

cd /
rm -rf /spa-src "$NODE_DIR" /root/.npm
