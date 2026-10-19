FROM python:3.13-slim AS base

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY rates ./rates

# ---- dev: lint + test toolchain. Scripts bind-mount the repo over /app.
FROM base AS dev
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -e '.[dev]' \
    && pyright --version >/dev/null
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1

# ---- runtime
FROM base AS runtime
ARG ACME_VERSION=3.1.6
# acme.sh (Let's Encrypt, DNS-01 on Cloudflare) needs curl and openssl.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl openssl \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /opt/acme.sh \
    && curl -fsSL https://github.com/acmesh-official/acme.sh/archive/refs/tags/${ACME_VERSION}.tar.gz \
       | tar xz -C /opt/acme.sh --strip-components=1
RUN pip install --no-cache-dir . \
    && useradd --system --uid 10001 tata
COPY entrypoint.sh /usr/local/bin/
USER tata
ENV TATA_DATA_DIR=/data TATA_RATES_DIR=/app/rates PYTHONDONTWRITEBYTECODE=1
EXPOSE 443
ENTRYPOINT ["entrypoint.sh"]
CMD ["uvicorn", "--factory", "tata.web:app_from_env", "--host", "0.0.0.0", "--port", "443", \
     "--ssl-certfile", "/acme/live/fullchain.pem", "--ssl-keyfile", "/acme/live/key.pem"]
