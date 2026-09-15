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

for repo in nmem-sym nmem-viz nmem-identity nmem-sym-sensor nmem-sandbox; do
  if [ ! -d "$CONTEXT/$repo" ]; then
    echo "ERROR: expected sibling repo '$repo' under $CONTEXT." >&2
    echo "       Check out all nmem-* repos side-by-side, or build where the monorepo lives." >&2
    exit 1
  fi
done

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

# nmem-studio-aio = the ONE-LINER trial image (Postgres+studio+viz in one container; `docker run`).
# nmem-studio = the slim studio for docker-compose (production/hive; separate pg + viz services).
# nmem-identity + nmem-sandbox back the optional identity / perception compose profiles — published
# so `docker compose --profile … pull` finds them.
_build nmem-studio-aio "$HERE/docker/studio/Dockerfile.aio"      "$CONTEXT"
_build nmem-studio     "$HERE/docker/studio/Dockerfile"          "$CONTEXT"
_build nmem-viz        "$CONTEXT/nmem-viz/Dockerfile"            "$CONTEXT/nmem-viz"
_build nmem-identity   "$HERE/docker/studio/Dockerfile.identity" "$CONTEXT"
_build nmem-sandbox    "$CONTEXT/nmem-sandbox/Dockerfile"        "$CONTEXT/nmem-sandbox"

echo "done: nmem-studio-aio + nmem-studio + nmem-viz + nmem-identity + nmem-sandbox :$VERSION @ $REGISTRY${PUSH:+  (pushed :$VERSION and :latest)}"
