#!/bin/sh
set -eu
# La sincronización corre en segundo plano; si se detiene, nginx sigue sirviendo la última versión.
python /app/sitio/sincronizar.py &
exec nginx -g 'daemon off;'
