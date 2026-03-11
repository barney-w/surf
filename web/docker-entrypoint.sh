#!/bin/sh
set -e

# Extract DNS resolver from container runtime
export RESOLVER=$(grep -m1 '^nameserver' /etc/resolv.conf | awk '{print $2}')

envsubst '${API_INTERNAL_FQDN} ${RESOLVER}' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf
exec nginx -g 'daemon off;'
