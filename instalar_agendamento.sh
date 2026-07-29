#!/usr/bin/env bash
# Instala o cron: tercas e quintas, 08:00. Uso: bash instalar_agendamento.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3 || command -v python)"

( crontab -l 2>/dev/null | grep -v "agente-vagas" ; \
  echo "0 8 * * 2,4 cd $DIR && $PY main.py --auto >> $DIR/cron.log 2>&1  # agente-vagas" ) | crontab -

echo "OK: cron instalado (ter/qui 08:00). Confira com: crontab -l"
