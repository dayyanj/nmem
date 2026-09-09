#!/usr/bin/env bash
# Build + tag (+ optionally push) the nmem appliance images for an OPEN, free pull-and-run
# distribution. Registry-agnostic: defaults to registry.spwig.com, override with $REGISTRY.
#
#   ./docker/publish.sh                     # build + tag  registry.spwig.com/nmem-studio:<version> + :latest
#   ./docker/publish.sh --push              # also `docker push` (run `docker login registry.spwig.com` first)
#   REGISTRY=ghcr.io/spwig ./docker/publish.sh --push     # mirror to another OCI registry
#   VERSION=1.2.3 ./docker/publish.sh       # override the version tag (default: nmem's __version__)
#
# The build context is the apps/ monorepo root, because the studio Dockerfile installs the sibling
# nmem-sym / nmem-act / nmem-exchange from source (they are not on PyPI). So the four repos must sit
# side-by-side under one parent (the normal layout), and nmem-viz alongside them.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"          # the nmem repo root
CONTEXT="$(cd "$HERE/.." && pwd)"                 # apps/ monorepo root (sibling nmem-* reachable)
REGISTRY="${REGISTRY:-registry.spwig.com}"
VERSION="${VERSION:-$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$HERE/src/nmem/_version.py" 2>/dev/null)}"
VERSION="${VERSION:-dev}"
PUSH=""
[ "${1:-}" = "--push" ] && PUSH=1

if [ ! -d "$CONTEXT/nmem-sym" ] || [ ! -d "$CONTEXT/nmem-viz" ]; then
  echo "ERROR: expected sibling repos under $CONTEXT (nmem-sym, nmem-act, nmem-exchange, nmem-viz)." >&2
  echo "       Check out all of them side-by-side, or build where the monorepo lives." >&2
  exit 1
fi

_build () {   # display-name  dockerfile  build-context
  local name="$1" dockerfile="$2" ctx="$3"
  echo "==> $REGISTRY/$name:$VERSION  (context: $ctx)"
  DOCKER_BUILDKIT=1 docker build --build-arg VERSION="$VERSION" \
    -f "$dockerfile" -t "$REGISTRY/$name:$VERSION" -t "$REGISTRY/$name:latest" "$ctx"
  if [ -n "$PUSH" ]; then
    docker push "$REGISTRY/$name:$VERSION"
    docker push "$REGISTRY/$name:latest"
  fi
}

_build nmem-studio "$HERE/docker/studio/Dockerfile" "$CONTEXT"
_build nmem-viz    "$CONTEXT/nmem-viz/Dockerfile"   "$CONTEXT/nmem-viz"

echo "done: nmem-studio:$VERSION + nmem-viz:$VERSION @ $REGISTRY${PUSH:+  (pushed :$VERSION and :latest)}"
