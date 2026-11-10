ARG PYTHON_BASE=python:3.11-slim
FROM ${PYTHON_BASE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data LANG=C.UTF-8 LC_ALL=C.UTF-8
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=3 update && apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
    libreoffice-calc libreoffice-writer python3-uno poppler-utils ffmpeg \
    fonts-noto-cjk fonts-liberation fontconfig \
    && fc-cache -f \
    && groupadd -g 10001 esop && useradd -u 10001 -g esop -m esop \
    && mkdir /data && chown esop:esop /data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY center ./center
COPY shared ./shared
COPY scripts ./scripts
COPY tests ./tests
COPY requirements-dev.txt .
# Normalize source permissions: NAS checkout umask may otherwise copy owner-only files.
RUN chmod -R a+rX /app
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "center.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
