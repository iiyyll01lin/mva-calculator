# DDM Demo Deployment (Option A: Manual Docker)

This deploys the single-file demo `l6-ddm-demo.html` with Nginx in Docker.

## Prerequisites
- A Linux server (or local machine) with Docker installed
- Port `8080` open (or choose another host port)

## Quick Start (local)
1. Change directory to the demo folder:
   ```bash
   cd demo/ddm
   ```
2. Build the image:
   ```bash
   docker build -t ddm-demo:latest .
   ```
3. Run the container:
   ```bash
   docker run -d --name ddm-demo -p 8080:80 ddm-demo:latest
   ```
4. Open the demo:
   - http://localhost:8080

## Using docker-compose
From `demo/ddm`:
```bash
docker compose up -d --build
```
Stops with:
```bash
docker compose down
```

## Proxy Settings (corporate networks)
Compose auto-loads `.env` in this folder. Edit [demo/ddm/.env](demo/ddm/.env) or export env vars before building:
```bash
export HTTP_PROXY=http://10.6.254.210:3128
export HTTPS_PROXY=http://10.6.254.210:3128
export NO_PROXY=localhost,127.0.0.1
export http_proxy=$HTTP_PROXY
export https_proxy=$HTTPS_PROXY
export no_proxy=$NO_PROXY
```
Then rebuild:
```bash
docker compose up -d --build
```

### If `docker build` fails to pull base images
Docker needs daemon-level proxy config for fetching base images. Set proxies for the Docker service and restart:

On systemd-based Linux (most distros):
```bash
sudo mkdir -p /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/proxy.conf >/dev/null <<'EOF'
[Service]
Environment="HTTP_PROXY=http://10.6.254.210:3128"
Environment="HTTPS_PROXY=http://10.6.254.210:3128"
Environment="NO_PROXY=localhost,127.0.0.1"
EOF

sudo systemctl daemon-reload
sudo systemctl restart docker

# Test image pull
docker pull nginx:1.25-alpine
```

Optionally configure client-side proxies for the Docker CLI:
```bash
mkdir -p ~/.docker
tee ~/.docker/config.json >/dev/null <<'EOF'
{
  "proxies": {
    "default": {
      "httpProxy": "http://10.6.254.210:3128",
      "httpsProxy": "http://10.6.254.210:3128",
      "noProxy": "localhost,127.0.0.1"
    }
  }
}
EOF
```

## Server Deploy (manual)
On your server:
```bash
# Copy the demo folder to server or git clone the repo, then:
cd demo/ddm

docker compose up -d --build
```
Access at `http://<server-host>:8080`.

## Updating after changes
When you commit changes to the demo:
```bash
cd demo/ddm
# Rebuild and redeploy
docker compose up -d --build
```
This rebuilds the image and restarts the container.

## No-Docker local fallback
If Docker is temporarily blocked by proxy policy, you can serve the demo locally:
```bash
cd demo/ddm
python3 -m http.server 8080
```
Open http://localhost:8080 (the demo is static and requires no backend).

## Notes
- The Nginx config sets `l6-ddm-demo.html` as the index and serves `/mva/*.js` modules.
- If you need a different external port, edit `docker-compose.yml` (e.g., "80:80").
- For production caching, remove the `Cache-Control: no-store` header in `nginx.conf`.
