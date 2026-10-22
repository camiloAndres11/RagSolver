#!/usr/bin/env bash
# Demostrador local (Goedel-Prover-V2-8B Q6_K) en la GPU vía llama-server Vulkan (Windows), lanzado desde WSL.
#   bash scripts/prover_server.sh start    # idempotente: si ya responde, no hace nada; espera a que cargue
#   bash scripts/prover_server.sh stop
#   bash scripts/prover_server.sh status
# Escucha SÓLO en la red virtual WSL↔Windows (el gateway de WSL), no en la LAN (SEC-3).
# 6 slots × 16 384 tokens: el prompt de DRAFT (plan + prueba informal + lemas) no cabe en slots de 12 288
# junto a los 11 500 de salida. La GPU satura en ~70 tok/s agregados con 6 u 8 slots.
set -u
cd "$(dirname "$0")/.."
GW=$(ip route show default | awk '{print $3}')
URL="http://$GW:8082"
LOG=tools/logs/prover_server.log

up() { curl -sf -m 3 "$URL/health" >/dev/null; }

case "${1:-start}" in
  start)
    if up; then echo "[prover] ya responde en $URL"; exit 0; fi
    mkdir -p tools/logs
    setsid nohup ./tools/llama-vulkan/llama-server.exe -m tools/models/Goedel-Prover-V2-8B.Q6_K.gguf -ngl 99 \
      --host "$GW" --port 8082 -c 98304 --parallel 6 -fa on -ctk q8_0 -ctv q8_0 --jinja > "$LOG" 2>&1 < /dev/null &
    for _ in $(seq 120); do
      if up; then echo "[prover] listo en $URL"; exit 0; fi
      sleep 5
    done
    echo "[prover] no arrancó en 10 min; ver $LOG" >&2; exit 1 ;;
  stop)
    pgrep -f "llama-server[.]exe.*--port 8082" | xargs -r kill && echo "[prover] detenido" ;;
  status)
    if up; then echo "[prover] responde en $URL"; else echo "[prover] apagado"; exit 1; fi ;;
  *)
    echo "uso: $0 start|stop|status" >&2; exit 2 ;;
esac
