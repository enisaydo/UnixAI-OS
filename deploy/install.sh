#!/usr/bin/env bash
# UnixAI OS merkez sunucu kurulumu (RHEL 8 / 9, container'sız, root, Python standart kütüphanesi)
# Kullanım (proje dizininde):  sudo bash deploy/install.sh
#   PORT=8080 sudo bash deploy/install.sh
set -euo pipefail

APP=/opt/unixai-os
PORT=${PORT:-3000}
SRC=$(cd "$(dirname "$0")/.." && pwd)

[[ $EUID -eq 0 ]] || { echo "root olarak çalıştırın (sudo)" >&2; exit 1; }
. /etc/os-release
case "${VERSION_ID%%.*}" in 8|9) ;; *) echo "Desteklenen: RHEL 8/9 (bulunan: $PRETTY_NAME)" >&2; exit 1;; esac
echo "==> $PRETTY_NAME üzerine kuruluyor: $APP (port $PORT, root)"

echo "==> Paketler (Python 3.9+, openssh-clients) — pip/ek modül gerekmez"
PY=""
for c in python3.12 python3.11 python3.9 python3; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then PY=$(command -v "$c"); break; fi
done
if [[ -z "$PY" ]]; then
  # RHEL 8: varsayılan python3 3.6 → python3.11 (AppStream); RHEL 9: python3 zaten 3.9
  dnf -y -q install python3.11 || dnf -y -q install python39
  PY=$(command -v python3.11 || command -v python3.9)
fi
dnf -y -q install openssh-clients openssl tzdata
echo "    $("$PY" --version) ($PY)"

echo "==> Uygulama dosyaları"
mkdir -p "$APP"
if [[ "$SRC" != "$APP" ]]; then
  tar -C "$SRC" --exclude=./data --exclude=./logs --exclude=./.env --exclude=./.git --exclude='*/__pycache__' -cf - . | tar -C "$APP" -xf -
fi
mkdir -p "$APP/data" "$APP/logs"
chown -R root:root "$APP"
chmod 700 "$APP/data" "$APP/logs"

echo "==> root SSH anahtarı (hedeflerde /root/.ssh/authorized_keys'e eklenecek)"
install -d -m 700 /root/.ssh
# root'un mevcut anahtarı kullanılır; yoksa standart id_rsa üretilir
[[ -f /root/.ssh/id_rsa ]] || ssh-keygen -q -t rsa -b 4096 -N "" -f /root/.ssh/id_rsa
chmod 600 /root/.ssh/id_rsa
touch /root/.ssh/known_hosts && chmod 644 /root/.ssh/known_hosts

echo "==> $APP/.env"
if [[ ! -f "$APP/.env" ]]; then
  PASS=$(openssl rand -base64 18 | tr -d '/+=')
  sed -e "s/^ADMIN_PASSWORD=.*/ADMIN_PASSWORD=$PASS/" -e "s/^PORT=.*/PORT=$PORT/" "$APP/.env.example" > "$APP/.env"
  NEW_PASS=$PASS
fi
chmod 600 "$APP/.env"

echo "==> systemd"
sed -e "s#__APP__#$APP#g" -e "s#__PYTHON__#$PY#g" "$APP/deploy/unixai-os.service" > /etc/systemd/system/unixai-os.service
systemctl daemon-reload
systemctl enable --now unixai-os
systemctl restart unixai-os

if systemctl is-active -q firewalld; then
  echo "==> firewalld: $PORT/tcp"
  firewall-cmd -q --permanent --add-port="$PORT/tcp"
  firewall-cmd -q --reload
fi

for i in {1..30}; do
  curl --noproxy '*' -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break
  sleep 1
done
systemctl is-active -q unixai-os || { journalctl -u unixai-os -n 30 --no-pager; exit 1; }

cat <<EOF

==================================================================
 UnixAI OS kuruldu.
 Arayüz    : http://<sunucu_ip>:$PORT   (adresler: $(hostname -I))
 Kullanıcı : admin
 Parola    : ${NEW_PASS:-"(değişmedi, $APP/.env → ADMIN_PASSWORD)"}

 Sonraki adımlar (hepsi arayüzden):
  1) Ayarlar → GenAI bağlantısı: token servisi URL'i + gövde/header'lar,
     chat/completions URL'i + model → "Kaydet + GPT'ye test mesajı"
  2) Sunucular → Sunucu ekle / Toplu ekle
  3) Sunucular → SSH anahtarı: gösterilen komutla her hedefte public key'i yetkilendirin
       bash target-setup.sh "\$(cat /root/.ssh/id_rsa.pub)" <merkez_ip>
     ve merkezde:  ssh-keyscan -H <hedef> >> /root/.ssh/known_hosts
  4) Toplama & Analiz → Başlat   (veya "Demo veri üret" ile hemen deneyin)
 Günlük otomatik toplama: Ayarlar → Zamanlama (varsayılan 06:30)
 Loglar: journalctl -u unixai-os -f
==================================================================
EOF
