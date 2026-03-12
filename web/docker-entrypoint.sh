#!/bin/sh
set -e

if [ -z "${API_INTERNAL_FQDN}" ]; then
    echo "ERROR: API_INTERNAL_FQDN is not set" >&2
    exit 1
fi

# Validate format (hostname characters only)
if ! echo "${API_INTERNAL_FQDN}" | grep -qE '^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$'; then
    echo "ERROR: API_INTERNAL_FQDN contains invalid characters" >&2
    exit 1
fi

echo "API upstream: ${API_INTERNAL_FQDN}"
envsubst '${API_INTERNAL_FQDN}' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf
exec nginx -g 'daemon off;'
