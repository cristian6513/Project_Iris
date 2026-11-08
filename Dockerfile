FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Runtime libraries used by local audio plus the tools required by Hermes'
# documented Linux installation/diagnostic path.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl ffmpeg git libportaudio2 xz-utils \
    && rm -rf /var/lib/apt/lists/*

COPY . /app
RUN python -m pip install --no-cache-dir .

EXPOSE 8000 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/status', timeout=3).read(256)" || exit 1

CMD ["python", "-m", "core.agent"]
