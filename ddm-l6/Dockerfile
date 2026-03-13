# DDM demo static deploy via Nginx
FROM nginx:1.25-alpine

# Optional build-time proxy args (picked up from docker-compose)
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ARG http_proxy
ARG https_proxy
ARG no_proxy

# Ensure runtime env has proxies if provided
ENV HTTP_PROXY=${HTTP_PROXY} \
	HTTPS_PROXY=${HTTPS_PROXY} \
	NO_PROXY=${NO_PROXY} \
	http_proxy=${http_proxy} \
	https_proxy=${https_proxy} \
	no_proxy=${no_proxy}

# Nginx site config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy only the demo HTML and module helpers
COPY l6-ddm-demo.html /usr/share/nginx/html/l6-ddm-demo.html
COPY mva /usr/share/nginx/html/mva

EXPOSE 80
