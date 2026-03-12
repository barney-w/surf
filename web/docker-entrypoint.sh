#!/bin/sh
set -e

echo "API upstream: ${API_INTERNAL_FQDN}"
envsubst '${API_INTERNAL_FQDN}' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf
exec nginx -g 'daemon off;'
