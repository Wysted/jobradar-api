# GetOnBoard scraper image, built the way the visualizer's ADR 0020 asks: FROM the runtime image, installed into the
# runtime's venv under its frozen constraints (the SDK, engine and browser versions cannot move). Build context is the
# backend root (the ORM models live there); build.sh does it, with an allowlist in Dockerfile.dockerignore:
#   docker build -f scrapers/getonbrd/Dockerfile --build-arg UV_IMAGE=<ref> -t scraping-jobs/getonbrd:<tag> .
# No CMD: the definition's entrypoint (the console script) is the container's args.
ARG RUNTIME_IMAGE=visualizer/runtime-scrapling:sdk-0.2
ARG UV_IMAGE

FROM ${UV_IMAGE} AS uv

FROM ${RUNTIME_IMAGE} AS builder
USER root
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
COPY scrapers/getonbrd /src/scraper
# requirements.lock is build.sh's export of uv.lock minus what the runtime already pins, so the libraries here are the
# locked versions. Both installs run under the runtime's constraints: a conflicting dependency fails this build, not a
# run. The project goes in with --no-deps (all its dependencies are installed or in the runtime), and --no-sources
# because the path source in pyproject.toml is for development: the runtime already holds the SDK.
RUN uv pip install --python /app/.venv/bin/python -c /app/constraints.txt --no-cache -r /src/scraper/requirements.lock \
 && uv pip install --python /app/.venv/bin/python -c /app/constraints.txt --no-cache --no-deps --no-sources /src/scraper \
 && uv pip check --python /app/.venv/bin/python
# Only the two ORM tables the scraper writes, plus a stub for `database` (the real package loads the API's engine and
# config). A .pth puts them on the venv's path.
COPY models/__init__.py /app/lib/models/__init__.py
COPY models/database/__init__.py models/database/job.py models/database/platform.py /app/lib/models/database/
COPY scrapers/getonbrd/stub/database /app/lib/database
RUN echo /app/lib > "$(/app/.venv/bin/python -c 'import sysconfig; print(sysconfig.get_path("purelib"))')/backend_models.pth"

FROM ${RUNTIME_IMAGE}
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/lib /app/lib
