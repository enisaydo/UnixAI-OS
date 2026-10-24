#!/usr/bin/env bash
# UnixAI OS'u Podman ile (root) build edip ayağa kaldırır / günceller.
# Kullanım:  sudo ./deploy/podman-up.sh            → sadece container (podman run)
#            sudo ./deploy/podman-up.sh --systemd  → systemd servisi olarak (açılışta otomatik başlar)
# Host'taki /root/.ssh (unixai_ed25519 + known_hosts) container'a bağlanır.
set -euo pipefail

export no_proxy="localhost,127.0.0.1,::1${no_proxy:+,$no_proxy}"
export NO_PROXY="$no_proxy"

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="localhost/unixai-os:latest"
NAME="unixai-os"
UNIT=/etc/systemd/system/unixai-os.service
PODMAN="$(command -v podman || true)"

[[ $EUID -eq 0 ]] || { echo "Bu script root olarak çalıştırılmalı (sudo)." >&2; exit 1; }
[[ -n "$PODMAN" ]] || { echo "podman bulunamadı" >&2; exit 1; }
cd "$APP_DIR"
if [[ ! -f .env ]]; then
  PASS=$(openssl rand -base64 18 | tr -d '/+=')
  sed -e "s/^ADMIN_PASSWORD=.*/ADMIN_PASSWORD=$PASS/" .env.example > .env
  echo "==> .env oluşturuldu · admin parolası: $PASS"
fi
chmod 600 .env
mkdir -p logs data

install -d -m 700 /root/.ssh
[[ -f /root/.ssh/unixai_ed25519 ]] || ssh-keygen -q -t ed25519 -N "" -f /root/.ssh/unixai_ed25519 -C "root@$(hostname -s) unixai-os"
touch /root/.ssh/known_hosts

PORT="${HOST_PORT:-$(grep -E '^HOST_PORT=' .env | tail -n1 | cut -d= -f2 | tr -d '[:space:]"'"'" || true)}"
PORT="${PORT:-3000}"
[[ "$PORT" =~ ^[0-9]+$ ]] || { echo "Geçersiz port: '$PORT'" >&2; exit 1; }

systemctl stop unixai-os >/dev/null 2>&1 || true
"$PODMAN" rm -f "$NAME" >/dev/null 2>&1 || true

if command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1 && ! firewall-cmd --query-port="$PORT/tcp" >/dev/null 2>&1; then
  firewall-cmd --permanent --add-port="$PORT/tcp" >/dev/null && firewall-cmd --reload >/dev/null
fi

echo "==> İmaj build ediliyor ($IMAGE)"
"$PODMAN" build --format docker -t "$IMAGE" .

RUN_ARGS=(--name "$NAME" --user root -p "$PORT:3000" --env-file "$APP_DIR/.env" -e PORT=3000
  -v "$APP_DIR/logs:/app/logs:Z" -v "$APP_DIR/data:/app/data:Z" -v /root/.ssh:/root/.ssh:z)

if [[ "${1:-}" == "--systemd" ]]; then
  cat > "$UNIT" <<EOF
[Unit]
Description=UnixAI OS (Podman)
Wants=network-online.target
After=network-online.target
[Service]
Restart=always
ExecStartPre=-$PODMAN rm -f $NAME
ExecStart=$PODMAN run --rm ${RUN_ARGS[*]} $IMAGE
ExecStop=$PODMAN stop -t 10 $NAME
[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now unixai-os
else
  "$PODMAN" run -d --restart unless-stopped "${RUN_ARGS[@]}" "$IMAGE"
fi

for i in {1..30}; do
  if curl --noproxy '*' -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    echo "UnixAI OS hazır → http://$(hostname -I | awk '{print $1}'):$PORT"
    exit 0
  fi
  sleep 1
done
echo "Uygulama yanıt vermedi:  podman logs $NAME" >&2
exit 1
