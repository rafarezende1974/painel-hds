#!/usr/bin/env bash
# Painel de HDs — @ginhocarvalho
# Inicia o painel local (http://127.0.0.1:8765) sem criar servidores duplicados.
set -e
cd "$(dirname "$0")"
URL="http://127.0.0.1:8765"
if curl -fsS --max-time 2 "$URL/api/health" >/dev/null 2>&1; then
  echo "Painel de HDs já está rodando em $URL — abrindo essa instância."
else
  echo "Iniciando o Painel de HDs em $URL ..."
  nohup python3 painel-hds.py > data/servidor.log 2>&1 &
  for i in $(seq 1 20); do
    sleep 0.5
    if curl -fsS --max-time 2 "$URL/api/health" >/dev/null 2>&1; then break; fi
  done
fi
if command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 &
elif command -v open >/dev/null 2>&1; then open "$URL" >/dev/null 2>&1 &
else echo "Abra no navegador: $URL"; fi
echo "Para encerrar: feche o terminal do painel ou pressione Ctrl+C nele."
echo "Catálogo SQLite: $(pwd)/data/catalog.db"
