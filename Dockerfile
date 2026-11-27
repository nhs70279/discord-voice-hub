# One Fly Machine runs BOTH the Discord Gateway/voice bot and FastAPI dashboard.
# Deno is required by yt-dlp's YouTube JavaScript challenge solver.
FROM debian:bookworm-slim AS deno-download

ARG TARGETARCH=amd64
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl unzip && rm -rf /var/lib/apt/lists/*
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) deno_archive=deno-x86_64-unknown-linux-gnu.zip ;; \
      arm64) deno_archive=deno-aarch64-unknown-linux-gnu.zip ;; \
      *) echo "Unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    base_url="https://github.com/denoland/deno/releases/latest/download/$deno_archive"; \
    curl --fail --location --retry 3 --silent --show-error "$base_url" -o /tmp/deno.zip; \
    curl --fail --location --retry 3 --silent --show-error "$base_url.sha256sum" -o /tmp/deno.sha256sum; \
    expected="$(awk 'NR == 1 { print $1 }' /tmp/deno.sha256sum)"; \
    test "${#expected}" -eq 64; \
    printf '%s  %s\n' "$expected" /tmp/deno.zip | sha256sum --check --status; \
    mkdir /tmp/deno-extracted; \
    unzip -q /tmp/deno.zip -d /tmp/deno-extracted; \
    install -m 0755 /tmp/deno-extracted/deno /usr/local/bin/deno

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/data

# gosu drops privileges after fixing permissions on the attached Fly Volume.
# tini forwards stop signals to the FastAPI process and Discord client.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates ffmpeg gosu tini && rm -rf /var/lib/apt/lists/*
COPY --from=deno-download /usr/local/bin/deno /usr/local/bin/deno

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install -r requirements.txt

COPY app/ ./app/
COPY deploy/fly-entrypoint.sh /usr/local/bin/voicehub-entrypoint
RUN groupadd --system --gid 10001 voicehub && \
    useradd --system --uid 10001 --gid voicehub --home-dir /data \
      --shell /usr/sbin/nologin voicehub && \
    chmod 0755 /usr/local/bin/voicehub-entrypoint && \
    mkdir -p /data

EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/voicehub-entrypoint"]
CMD ["python", "-m", "uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8080"]
