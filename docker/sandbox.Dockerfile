FROM python:3.11-slim

ARG GITLEAKS_VERSION=8.30.0
ARG TRIVY_VERSION=0.68.2
ARG SYFT_VERSION=1.40.1

ENV DEBIAN_FRONTEND=noninteractive
ENV TRIVY_CACHE_DIR=/var/lib/trivy

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    gzip \
    tar \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL \
    "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" \
    -o /tmp/gitleaks.tar.gz \
    && tar -xzf /tmp/gitleaks.tar.gz -C /tmp \
    && install -m 0755 /tmp/gitleaks /usr/local/bin/gitleaks \
    && rm -rf /tmp/gitleaks /tmp/gitleaks.tar.gz

RUN curl -fsSL \
    "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz" \
    -o /tmp/trivy.tar.gz \
    && tar -xzf /tmp/trivy.tar.gz -C /tmp trivy \
    && install -m 0755 /tmp/trivy /usr/local/bin/trivy \
    && rm -rf /tmp/trivy /tmp/trivy.tar.gz

RUN curl -fsSL \
    "https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}/syft_${SYFT_VERSION}_linux_amd64.tar.gz" \
    -o /tmp/syft.tar.gz \
    && tar -xzf /tmp/syft.tar.gz -C /tmp syft \
    && install -m 0755 /tmp/syft /usr/local/bin/syft \
    && rm -rf /tmp/syft /tmp/syft.tar.gz

RUN mkdir -p /var/lib/trivy /workspace \
    && trivy fs --cache-dir /var/lib/trivy --format json --output /tmp/trivy_bootstrap.json /workspace || true \
    && rm -f /tmp/trivy_bootstrap.json

WORKDIR /workspace

