# =============================================================================
# Stage 1 — Builder
# Use the slim Node.js image and install pnpm to build the Vite/React app.
# =============================================================================
FROM node:22-alpine AS builder

# ── Corporate proxy support ───────────────────────────────────────────────────
# Accept proxy co-ordinates as build-args so they can be injected by
# docker-compose (from the host shell) without being baked into the image.
ARG http_proxy
ARG https_proxy
ARG no_proxy

# Expose them as both lower- and upper-case ENV vars so that npm, pnpm,
# wget, and Alpine's apk all pick them up consistently.
ENV HTTP_PROXY=$http_proxy \
    HTTPS_PROXY=$https_proxy \
    http_proxy=$http_proxy \
    https_proxy=$https_proxy \
    no_proxy=$no_proxy \
    NO_PROXY=$no_proxy

# Install pnpm via npm (proxy-friendly; no corepack network calls required).
# strict-ssl false is needed when a corporate MITM CA intercepts TLS.
RUN npm config set proxy     "$http_proxy" && \
    npm config set https-proxy "$https_proxy" && \
    npm config set strict-ssl false && \
    npm install -g pnpm@latest && \
    pnpm config set proxy      "$http_proxy" && \
    pnpm config set https-proxy "$https_proxy" && \
    pnpm config set strict-ssl false

WORKDIR /app

# Copy dependency manifests first so Docker can cache the install layer.
COPY package.json pnpm-lock.yaml ./

# Install all dependencies (including devDependencies needed for the build).
RUN pnpm install --frozen-lockfile

# Copy source and build.
COPY . .
RUN pnpm run build

# =============================================================================
# Stage 2 — Runner
# Serve the compiled static assets with a minimal nginx:alpine image.
# =============================================================================
FROM nginx:1.27-alpine AS runner

# Remove the default nginx config and replace with our SPA-aware config.
RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/mva.conf

# Copy the Vite build output from the builder stage.
COPY --from=builder /app/dist /usr/share/nginx/html

# nginx listens on 80 inside the container; docker-compose maps it to 8080.
EXPOSE 80

# Use nginx's default CMD (runs in the foreground).
CMD ["nginx", "-g", "daemon off;"]
