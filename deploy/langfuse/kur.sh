#!/usr/bin/env bash
# Langfuse — UnixAI OS ve MorpheusAI ajan izleri için kurum içi kurulum (resmi docker-compose şablonu, podman ile)
#
#   sudo bash deploy/langfuse/kur.sh [--host langfuse.kurum.local] [--port 3000] [--dir /opt/langfuse]
#                                    [--email admin@kurum.local] [--compose-file yerel/docker-compose.yml] [--guncelle]
#
# Ne yapar:
#   1) Resmi şablonu indirir (github.com/langfuse/langfuse → docker-compose.yml) veya --compose-file ile verileni kullanır
#   2) .env üretir: tüm CHANGEME parolaları rastgele, telemetri kapalı; organizasyon, proje, API anahtarları ve
#      ilk kullanıcı otomatik oluşur (LANGFUSE_INIT_*). Var olan .env'e DOKUNMAZ (parolalar değişmez).
#   3) podman compose / podman-compose / docker compose ile başlatır, hazır olmasını bekler, firewall'da portu açar
#   4) UnixAI OS ve MorpheusAI .env dosyalarına eklenecek satırları yazar
#
# Gereksinim (Langfuse dokümanı): ≥4 çekirdek, 16 GB bellek, ~100 GB disk. Servisler: langfuse-web (UI), worker,
# Postgres, ClickHouse, Redis, MinIO. Yalnızca UI portu (varsayılan 3000) dışarı açılır.
set -euo pipefail

DIR=/opt/langfuse
HOST="$(hostname -f 2>/dev/null || hostname)"
PORT=""
EMAIL=""
COMPOSE_FILE=""
UPDATE=0
URL="${LANGFUSE_COMPOSE_URL:-https://raw.githubusercontent.com/langfuse/langfuse/main/docker-compose.yml}"

while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --dir) DIR="$2"; shift 2 ;;
    --email) EMAIL="$2"; shift 2 ;;
    --compose-file) COMPOSE_FILE="$2"; shift 2 ;;
    --guncelle) UPDATE=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "Bilinmeyen seçenek: $1" >&2; exit 1 ;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "root olarak çalıştırın: sudo bash $0" >&2; exit 1; }
say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }

# --- compose aracı ---
if podman compose version >/dev/null 2>&1; then COMPOSE=(podman compose)
elif command -v podman-compose >/dev/null 2>&1; then COMPOSE=(podman-compose)
elif docker compose version >/dev/null 2>&1; then COMPOSE=(docker compose)
else
  echo "compose aracı bulunamadı. RHEL: dnf install -y podman podman-compose  (podman-compose EPEL'de; veya: pip3 install podman-compose)" >&2
  exit 1
fi
say "compose aracı: ${COMPOSE[*]}"

mem_gb=$(awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo)
[ "${mem_gb:-0}" -lt 15 ] && warn "Bellek ${mem_gb} GB; Langfuse en az 16 GB öneriyor (ClickHouse + Postgres + web + worker)."

# --- port: MorpheusAI varsayılan 3000'i kullanıyorsa çakışmasın ---
if [ -z "$PORT" ]; then
  PORT=3000
  if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]3000$"; then
    PORT=3100
    warn "3000 portu kullanımda (MorpheusAI olabilir); Langfuse $PORT portunda açılacak."
  fi
fi

mkdir -p "$DIR"
cd "$DIR"

# --- resmi şablon ---
if [ ! -f docker-compose.yml ] || [ "$UPDATE" -eq 1 ]; then
  if [ -n "$COMPOSE_FILE" ]; then
    cp "$COMPOSE_FILE" docker-compose.yml
  else
    say "Resmi şablon indiriliyor: $URL"
    curl -fsSL "$URL" -o docker-compose.yml.yeni || { echo "İndirilemedi. İnternet yoksa dosyayı başka yerden alıp --compose-file ile verin." >&2; exit 1; }
    mv docker-compose.yml.yeni docker-compose.yml
  fi
fi
grep -q "langfuse-web" docker-compose.yml || { echo "docker-compose.yml Langfuse şablonuna benzemiyor" >&2; exit 1; }
[ "$PORT" != "3000" ] && sed -i -E "s#^(\s*)- 3000:3000\s*\$#\1- ${PORT}:3000#" docker-compose.yml

# --- .env (bir kez üretilir; tekrar çalıştırınca korunur) ---
if [ ! -f .env ]; then
  rnd() { openssl rand -hex "${1:-24}"; }
  PG=$(rnd); MINIO=$(rnd); PK="pk-lf-$(rnd 16)"; SK="sk-lf-$(rnd 24)"; PASS=$(rnd 10)
  EMAIL="${EMAIL:-admin@${HOST}}"
  umask 077
  cat > .env <<EOF
# Langfuse — kur.sh tarafından üretildi ($(date -u +%FT%TZ)). Gizli: paylaşmayın, yedekleyin.
NEXTAUTH_URL=http://${HOST}:${PORT}
NEXTAUTH_SECRET=$(rnd 32)
SALT=$(rnd)
ENCRYPTION_KEY=$(openssl rand -hex 32)
POSTGRES_PASSWORD=${PG}
DATABASE_URL=postgresql://postgres:${PG}@postgres:5432/postgres
CLICKHOUSE_PASSWORD=$(rnd)
MINIO_ROOT_PASSWORD=${MINIO}
LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY=${MINIO}
LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY=${MINIO}
LANGFUSE_S3_BATCH_EXPORT_SECRET_ACCESS_KEY=${MINIO}
REDIS_AUTH=$(rnd)
TELEMETRY_ENABLED=false
LANGFUSE_INIT_ORG_ID=kurum
LANGFUSE_INIT_ORG_NAME=Kurum
LANGFUSE_INIT_PROJECT_ID=unixai-morpheus
LANGFUSE_INIT_PROJECT_NAME=UnixAI-MorpheusAI
LANGFUSE_INIT_PROJECT_PUBLIC_KEY=${PK}
LANGFUSE_INIT_PROJECT_SECRET_KEY=${SK}
LANGFUSE_INIT_USER_EMAIL=${EMAIL}
LANGFUSE_INIT_USER_NAME=Yonetici
LANGFUSE_INIT_USER_PASSWORD=${PASS}
EOF
  chmod 600 .env
  say ".env üretildi ($DIR/.env, izin 600)"
else
  say "Var olan .env korunuyor"
fi
val() { grep -E "^$1=" .env | head -1 | cut -d= -f2-; }

# --- başlat ---
say "Langfuse başlatılıyor (ilk açılış imajları indirir, 2-5 dk sürebilir)"
"${COMPOSE[@]}" up -d

if command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1 && ! firewall-cmd --query-port="${PORT}/tcp" >/dev/null 2>&1; then
  firewall-cmd --permanent --add-port="${PORT}/tcp" >/dev/null && firewall-cmd --reload >/dev/null
  say "firewall: ${PORT}/tcp açıldı"
fi

for i in $(seq 1 100); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/public/health" >/dev/null 2>&1; then ok=1; break; fi
  sleep 3
done
[ "${ok:-0}" = 1 ] || { warn "Langfuse ${PORT} portunda henüz cevap vermiyor. Durum: ${COMPOSE[*]} ps · Loglar: ${COMPOSE[*]} logs langfuse-web"; exit 1; }

cat <<EOF

$(say "Langfuse hazır: http://${HOST}:${PORT}")
  Giriş: $(val LANGFUSE_INIT_USER_EMAIL)  /  parola: $(val LANGFUSE_INIT_USER_PASSWORD)   (ilk girişten sonra değiştirin)

  UnixAI OS ve MorpheusAI .env dosyalarına ekleyin, sonra servisleri yeniden başlatın:

    LANGFUSE_URL=http://${HOST}:${PORT}
    LANGFUSE_PUBLIC_KEY=$(val LANGFUSE_INIT_PROJECT_PUBLIC_KEY)
    LANGFUSE_SECRET_KEY=$(val LANGFUSE_INIT_PROJECT_SECRET_KEY)
    LANGFUSE_PROJECT_ID=$(val LANGFUSE_INIT_PROJECT_ID)

  Durum: ${COMPOSE[*]} ps   ·   Durdur: ${COMPOSE[*]} down   ·   Güncelle: bash $0 --guncelle
EOF
