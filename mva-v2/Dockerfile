# =============================================================================
# Stage 1 — Builder
# Use the slim Node.js image and install pnpm to build the Vite/React app.
# =============================================================================
FROM node:22-alpine AS builder

# Enable pnpm via Corepack (ships with Node ≥ 16.10)
RUN corepack enable && corepack prepare pnpm@latest --activate

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
