#!/usr/bin/env bash
# Builds the GetOnBoard image on the shared Docker daemon, imports it into the cluster and upserts its definition
# (visualizer ADR 0020). Idempotent: the tag is a hash of the inputs, so a rerun with no change builds from cache and
# imports nothing. Needs the platform up (deploy/up.sh builds the runtime image and starts the cluster).
#   VISUALIZER_ACTOR=<email> scrapers/getonbrd/build.sh
# Manual and never scripted: the sink and its approval (`admin.sh sink-add scraping-jobs ...`, `sink-approve ...`).
set -euo pipefail

cd "$(dirname "$0")"
readonly BACKEND=../..
readonly PLATFORM=../../../../visualizer/platform
readonly RUNTIME_IMAGE=visualizer/runtime-scrapling:sdk-0.2  # the SDK series pyproject.toml pins
readonly IMAGE_REPO=scraping-jobs/getonbrd
readonly NAME=getonbrd
readonly LAST_IMAGE_FILE=.last-image  # untracked: the tag to retire after the next successful upsert
: "${VISUALIZER_ACTOR:?set VISUALIZER_ACTOR to the operator email (the audit actor)}"

# The uv image is pinned in one place, the platform's up.sh.
UV_IMAGE="${UV_IMAGE:-$(sed -n 's/^readonly UV_IMAGE=//p' "$PLATFORM/deploy/up.sh")}"
[[ -n "$UV_IMAGE" ]] || { echo "cannot read UV_IMAGE from $PLATFORM/deploy/up.sh" >&2; exit 1; }
runtime_id="$(docker image inspect --format '{{.Id}}' "$RUNTIME_IMAGE" 2>/dev/null)" \
  || { echo "$RUNTIME_IMAGE is not in the Docker daemon: run $PLATFORM/deploy/up.sh first" >&2; exit 1; }

# The libraries the image installs, pinned from uv.lock (--locked fails when uv.lock is stale), minus the packages the
# runtime already pins (its constraints win; the SDK and its engine deps come from there).
norm='{ sub(/==.*/, ""); gsub(/_/, "-"); print tolower($0) }'
docker run --rm --entrypoint cat "$RUNTIME_IMAGE" /app/constraints.txt | awk "$norm" >.runtime-pins
uv export --locked --no-dev --no-emit-project --no-emit-package visualizer-sdk --no-hashes --no-header --no-annotate \
  | awk 'NR == FNR { pinned[$0] = 1; next } { n = $1; sub(/[ =;].*/, "", n); n = tolower(n); gsub(/_/, "-", n); if (!(n in pinned)) print }' \
    .runtime-pins - >requirements.lock
rm -f .runtime-pins

# Same inputs, same tag: the files that reach the image (requirements.lock carries the lockfile's part), the runtime image ID and the uv image.
tag="$({
  find pyproject.toml requirements.lock Dockerfile Dockerfile.dockerignore src stub "$BACKEND/models/__init__.py" \
    "$BACKEND/models/database/__init__.py" "$BACKEND/models/database/job.py" "$BACKEND/models/database/platform.py" \
    -type f -not -path '*/__pycache__/*' -print0 | LC_ALL=C sort -z | xargs -0 sha256sum
  printf '%s\n' "$runtime_id" "$UV_IMAGE"
} | sha256sum | cut -c1-12)"
ref="$IMAGE_REPO:$tag"

echo "==> image $ref"
docker build -q -f Dockerfile --build-arg "RUNTIME_IMAGE=$RUNTIME_IMAGE" --build-arg "UV_IMAGE=$UV_IMAGE" \
  -t "$ref" "$BACKEND" >/dev/null
"$PLATFORM/deploy/import-image.sh" "$ref"

# --sink and --target need their rows (sink-add, target registry) to exist, and the sink to be approved for a run.
"$PLATFORM/deploy/admin.sh" definition-upsert "$NAME" --image "$ref" --engine scrapling --target getonbrd.com \
  --sink scraping-jobs --entrypoint visualizer-getonbrd --deadline 600 --max-attempts 2 --retry-delay 60 \
  --params '{"query": "python", "max_pages": 2, "country_code": "CL"}' --actor "$VISUALIZER_ACTOR"

# Retired only after the upsert: a definition must never name an image that is gone.
if [[ -s "$LAST_IMAGE_FILE" ]]; then
  previous="$(<"$LAST_IMAGE_FILE")"
  [[ "$previous" == "$ref" ]] || docker rmi "$previous" >/dev/null 2>&1 || true
fi
echo "$ref" >"$LAST_IMAGE_FILE"
