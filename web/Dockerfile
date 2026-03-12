FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10

# Copy nginx config as template (envsubst replaces API_INTERNAL_FQDN at startup)
COPY nginx.conf /etc/nginx/nginx.conf.template

# Copy entrypoint script
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Copy pre-built SPA
COPY dist/ /usr/share/nginx/html/

# Ensure nginx user can write to required paths
RUN chown -R nginx:nginx /usr/share/nginx/html /var/cache/nginx /var/log/nginx /etc/nginx/nginx.conf && \
    mkdir -p /tmp/client_body /tmp/proxy /tmp/fastcgi /tmp/uwsgi /tmp/scgi && \
    chown -R nginx:nginx /tmp/client_body /tmp/proxy /tmp/fastcgi /tmp/uwsgi /tmp/scgi

USER nginx

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO- http://localhost:8080/healthz || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
