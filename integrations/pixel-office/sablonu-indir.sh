#!/usr/bin/env bash
# Pixel Agents ofis şablonunu (MIT) yerele çeker — sürüm sabit, bütünlük (sha512) doğrulanır, bağımlılıklarıyla tek klasör.
#
#   bash integrations/pixel-office/sablonu-indir.sh [hedef-klasör]      (varsayılan ~/pixel-office)
#
# İnternetli bir makinede çalıştırın (npm registry veya kurum içi npm aynası: npm config set registry ...).
# Sonra klasörü olduğu gibi internetsiz sunucuya kopyalayabilirsiniz:  tar czf pixel-office.tgz -C ~ pixel-office
# Gerekenler: Node.js 20+ ve npm, openssl.
set -euo pipefail

VERSION=1.5.0
INTEGRITY="sha512-48aBAjfN6elF5xq44knHuhMVUfuYfZ8kj/CT1KRpaQLd9H3Ds76Crrd1O+LRtv0CQKdb0ZJI3wGjJkMr+OPaoA=="
DIR="${1:-$HOME/pixel-office}"
HERE="$(cd "$(dirname "$0")" && pwd)"

command -v node >/dev/null && command -v npm >/dev/null || { echo "Node.js 20+ ve npm gerekli (RHEL: dnf module install nodejs:20)" >&2; exit 1; }
[ "$(node -p 'process.versions.node.split(".")[0]')" -ge 20 ] || { echo "Node.js 20 veya üstü gerekli" >&2; exit 1; }

mkdir -p "$DIR"
cd "$DIR"
echo "==> pixel-agents@$VERSION indiriliyor"
npm pack "pixel-agents@$VERSION" --silent >/dev/null
TGZ="pixel-agents-$VERSION.tgz"
GOT="sha512-$(openssl dgst -sha512 -binary "$TGZ" | base64 | tr -d '\n')"
[ "$GOT" = "$INTEGRITY" ] || { echo "Bütünlük doğrulanamadı! beklenen $INTEGRITY, gelen $GOT" >&2; rm -f "$TGZ"; exit 1; }
echo "==> sha512 doğrulandı"

[ -f package.json ] || echo '{ "name": "pixel-office", "private": true }' > package.json
npm install "./$TGZ" --omit=dev --no-audit --no-fund --silent
cp "$HERE/pixel-bridge.mjs" ./pixel-bridge.mjs

cat > baslat.sh <<'EOF'
#!/usr/bin/env bash
# Pixel Agents ofisini başlatır (gömülü terminal kapalı: tarayıcıdan komut çalıştırılamaz)
cd "$(dirname "$0")"
exec node node_modules/pixel-agents/dist/cli.js --port "${PORT:-3100}" --no-terminal "$@"
EOF
chmod +x baslat.sh

cat <<EOF

==> Hazır: $DIR
  1) Ofisi başlatın:     cd $DIR && ./baslat.sh          (yazdırılan ?token=… adresini tarayıcıda açın)
  2) Ofiste:             Settings → "Watch All Sessions" açın
  3) Köprüyü başlatın:   UNIXAI_URL=http://unixai:8080 MORPHEUS_URL=http://morpheus:3000 node $DIR/pixel-bridge.mjs
  Ayrıntı: integrations/pixel-office/README.md
EOF
