#!/usr/bin/env sh
# Render sets PORT; Gravitino defaults to 8090 via gravitino.conf.
# Patch known image layout paths before delegating to the upstream entrypoint.
set -eu
HTTP_PORT="${PORT:-8090}"
for root in /root/gravitino /opt/gravitino; do
  conf="${root}/conf/gravitino.conf"
  if [ -f "$conf" ]; then
    if grep -q '^[[:space:]]*gravitino.server.webserver.httpPort[[:space:]]*=' "$conf"; then
      sed -i.bak "s/^\([[:space:]]*gravitino.server.webserver.httpPort[[:space:]]*=[[:space:]]*\).*/\1${HTTP_PORT}/" "$conf"
    else
      printf '\ngravitino.server.webserver.httpPort = %s\n' "$HTTP_PORT" >> "$conf"
    fi
  fi
done
if [ -x /root/gravitino/bin/start-gravitino.sh ]; then
  exec /bin/bash /root/gravitino/bin/start-gravitino.sh "$@"
fi
if [ -x /opt/gravitino/bin/start-gravitino.sh ]; then
  exec /bin/bash /opt/gravitino/bin/start-gravitino.sh "$@"
fi
echo "gravitino-render-entrypoint: could not find start-gravitino.sh" >&2
exit 1
