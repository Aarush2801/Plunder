FROM python:3.12-slim

# System deps: libtorrent, ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        python3-libtorrent \
        xclip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir -e .

# Default: headless daemon (no TUI required in a container)
ENV PYTHONUNBUFFERED=1
EXPOSE 7889 6881/tcp 6881/udp

CMD ["python3", "ignition.py", "daemon"]
