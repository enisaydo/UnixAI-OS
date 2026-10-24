#!/bin/bash
# Hedef RHEL sunucuda root ile calistirin: merkezi UnixAI sunucusunun root SSH anahtarini yetkilendirir.
# Kullanim: bash target-setup.sh "ssh-ed25519 AAAA... unixai@merkez" [MERKEZ_IP]
#   MERKEZ_IP verilirse anahtar sadece o adresten kullanilabilir (onerilir).
# Ansible ile: ansible all -b -m script -a "target-setup.sh '<pubkey>' 10.10.0.5"
set -euo pipefail
PUBKEY=${1:?public key gerekli (merkezde: cat /root/.ssh/unixai_ed25519.pub)}
CENTRAL_IP=${2:-}

[ "$(id -u)" -eq 0 ] || { echo "root olarak calistirin"; exit 1; }

OPTS="no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty"
[ -n "$CENTRAL_IP" ] && OPTS="from=\"$CENTRAL_IP\",$OPTS"
LINE="$OPTS $PUBKEY"

install -d -m 700 /root/.ssh
touch /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
# Ayni anahtarin eski satirini kaldirip yenisini ekle (tekrar calistirilabilir)
KEYBODY=$(awk '{print $2}' <<<"$PUBKEY")
grep -v -F "$KEYBODY" /root/.ssh/authorized_keys > /root/.ssh/authorized_keys.tmp || true
echo "$LINE" >> /root/.ssh/authorized_keys.tmp
mv /root/.ssh/authorized_keys.tmp /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
restorecon -R /root/.ssh 2>/dev/null || true

# sshd root'un anahtarla girisine izin veriyor mu?
PRL=$(sshd -T 2>/dev/null | awk '/^permitrootlogin/{print $2}')
case "$PRL" in
  yes|prohibit-password|without-password|forced-commands-only)
    echo "OK: PermitRootLogin=$PRL" ;;
  *)
    echo "UYARI: PermitRootLogin=$PRL -> root anahtarla giremez."
    echo "  /etc/ssh/sshd_config icinde 'PermitRootLogin prohibit-password' yapip 'systemctl reload sshd' calistirin."
    [ -n "$CENTRAL_IP" ] && echo "  Sadece merkezi sunucuya izin icin sonuna:  Match Address $CENTRAL_IP / PermitRootLogin prohibit-password" ;;
esac
echo "Hazir: $(hostname -f)"
