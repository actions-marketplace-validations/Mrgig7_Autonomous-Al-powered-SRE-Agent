# syntax=docker/dockerfile:1.6
# Multi-stage build for the SRE Agent React dashboard.
# Stage 1 builds the production bundle, stage 2 serves it via nginx.

FROM node:20-alpine AS builder
WORKDIR /app

# Install dependencies first to leverage layer caching
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

# Copy source and build
COPY . .
ARG VITE_API_URL=
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build

# --- Runtime stage ---
FROM nginx:1.27-alpine AS runtime

# SPA-friendly nginx config that proxies /api to the SRE Agent API container
COPY <<'EOF' /etc/nginx/conf.d/default.conf
server {
    listen 3000;
    server_name _;

    root /usr/share/nginx/html;
    index index.html;

    # SPA history fallback
    location / {
        try_files $uri $uri/ /index.html;
    }

    # API + SSE proxy. Container DNS resolves "api" to the FastAPI service.
    location /api/ {
        proxy_pass         http://api:8000/api/;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   Connection        "";
        proxy_buffering    off;
        proxy_read_timeout 3600s;
    }

    # Webhook + auth callbacks share the API origin
    location /webhooks/ {
        proxy_pass         http://api:8000/webhooks/;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }

    location = /health {
        proxy_pass http://api:8000/health;
    }
}
EOF

COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -qO- http://localhost:3000/ >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
