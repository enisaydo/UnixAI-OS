#!/usr/bin/env bash
# Trinity yedeği: data/ (ayarlar, envanter, analiz geçmişi, zafiyet torbaları, MorpheusAI kayıtları) + .env
#
#   bash deploy/backup.sh                       → /var/backups/trinity/trinity-YYYYmmdd-HHMM.tar.gz
#   BACKUP_DIR=/yedek KEEP=30 bash deploy/backup.sh
#   Günlük (root crontab):  15 5 * * * /opt/unixai-os/deploy/backup.sh >/dev/null
#
# Geri yükleme:  systemctl stop unixai-os && tar -C /opt/unixai-os -xzf <yedek>.tar.gz && systemctl start unixai-os
# Yedek parola ve token içerir (data/*.json, .env): 0600 ile yazılır, güvenli bir yere kopyalayın.
set -euo pipefail

APP="${APP:-$(cd "$(dirname "$0")/.." && pwd)}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/trinity}"
KEEP="${KEEP:-14}"  # saklanacak yedek sayısı
INCLUDE_LOGS="${INCLUDE_LOGS:-false}"

[ -d "$APP/data" ] || { echo "HATA: $APP/data bulunamadı (APP=/opt/unixai-os gibi verin)" >&2; exit 1; }
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
out="$BACKUP_DIR/trinity-$(date +%Y%m%d-%H%M).tar.gz"
items=(data)
[ -f "$APP/.env" ] && items+=(.env)
[ "$INCLUDE_LOGS" = "true" ] && [ -d "$APP/logs" ] && items+=(logs)

umask 077
tar -C "$APP" -czf "$out.tmp" "${items[@]}"
mv "$out.tmp" "$out"
echo "Yedek: $out ($(du -h "$out" | cut -f1); ${items[*]})"

# eski yedekleri sil (en yeni KEEP tanesi kalır)
ls -1t "$BACKUP_DIR"/trinity-*.tar.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r f; do rm -f -- "$f"; done
