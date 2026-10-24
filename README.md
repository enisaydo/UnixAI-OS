# UnixAI OS

RHEL sunuculardan **agentless (SSH)** günlük log ve metrik toplayan, kural motoru + kurumsal **GenAI (GPT)** ile
sunucu bazında anomali analizi yapıp **aksiyon maddeleri** çıkaran web arayüzü.
MorpheusAI ile aynı yapıdadır (aynı arayüz, aynı GenAI akışı: token servisi → chat/completions, token kullanımı/limitleri,
AI logları, LDAP girişi); backend **Python**'dır ve yalnızca standart kütüphaneyi kullanır — `pip install` gerekmez.

```
Arayüz (Ayarlar → GenAI bağlantısı)
  Token URL + gövde + header'lar ──▶ TMS token ──▶ Authorization: Bearer <token> ──▶ chat/completions (GPT)

Merkez sunucu ──ssh (root, anahtar, salt-okunur komutlar)──▶ RHEL hedefler   (hedefe hiçbir şey kurulmaz)
     │  parse → kural motoru → GPT analizi → anomaliler + aksiyonlar (komutlarıyla)
     └─ data/runs/YYYY-MM-DD/<run>/<sunucu>.json  (ham çıktı + metrik + analiz)
```

## Hızlı başlangıç (yerel)

```bash
python3 server.py              # → http://localhost:3000
python3 scripts/smoke_test.py  # sahte TMS/GPT gateway + sahte ssh ile uçtan uca testler
```

Python 3.9+ yeterli (RHEL 9 varsayılan `python3`; RHEL 8'de `python3.11` veya `python39` paketi). GenAI bağlı değilken **demo modunda** kural motoru çalışır;
*Toplama & Analiz → Demo veri üret* ile 4 örnek RHEL sunucusunun 7 günlük verisi oluşturulur.

## Sunucuya kurulum (RHEL 8/9, root)

```bash
sudo -i
git clone <repo> /opt/unixai-os && cd /opt/unixai-os
bash deploy/install.sh              # Python 3.9+, ssh anahtarı, .env (rastgele admin parolası), systemd, firewalld
# PORT=8080 bash deploy/install.sh
```

Podman ile: `./deploy/podman-up.sh --systemd` (host'taki `/root/.ssh` container'a bağlanır).

## GenAI bağlantısı (arayüzden)

**Ayarlar → GenAI bağlantısı**:

| Alan | Açıklama |
|---|---|
| Token URL, metot, gövde, header'lar | TMS token isteği (Bruno'daki istekle birebir). Gövde JSON veya `a=b&c=d` |
| Chat URL, model alanı, model, ek gövde, header'lar | chat/completions isteği. `model` / `model_name`, `{"max_completion_tokens":16000}` |
| Gelişmiş | token/süre JSON yolu, token header'ı, Basic auth, yetki header'ı/öneki, cevap yolu |

- **Kaydet + token al** ve **Kaydet + GPT'ye test mesajı** ile anında doğrulanır; istek/cevaplar *AI Logları*'nda görünür.
- Yer tutucular: `{{uuid}}` (her istekte yeni), `{{timestamp}}`, `{{token}}` (chat header'larında), `{{env:İSİM}}`.
- Gizli alanlar (password, client_secret, client-id, authorization…) tarayıcıya **maskeli** gelir; maskeyi değiştirmeden kaydederseniz eski değer korunur. Sırrı hiç arayüze yazmamak için `.env`'e koyup `{{env:İSİM}}` kullanın.
- Ayarlar `data/connection.json`'da (0600) saklanır; servis yeniden başlatmaya gerek yoktur. Token cache'lenir, 401/403'te yenilenir.
- `.env`'de MorpheusAI biçiminde `TMS_*` / `CHAT_*` varsa ilk değerler oradan gelir.

## Sunucular ve SSH

1. **Sunucular → Sunucu ekle / Toplu ekle** (`ad adres [kullanıcı] [port]` satırları).
2. **Sunucular → SSH anahtarı** kartındaki komutla her hedefte public key'i yetkilendirin:
   ```bash
   bash target-setup.sh "$(cat /root/.ssh/unixai_ed25519.pub)" <merkez_ip>   # from= kısıtlı, no-pty
   ssh-keyscan -H <hedef> >> /root/.ssh/known_hosts                           # merkezde
   ```
3. Satırdaki **SSH testi** ile doğrulayın. Hatalar yol gösteren mesaja çevrilir (host key, Permission denied, DNS…).

Toplama tek SSH oturumunda `bash -s` ile salt-okunur komutlar çalıştırır: df/inode, bellek, vmstat, load,
failed unit'ler, journal hataları, başarısız SSH girişleri, kernel uyarıları, SELinux/AVC, chrony, RHSA güncellemeleri,
needs-restarting, top prosesler, zombie. Root değilse ayrıcalıklı komutlar `sudo -n` ile çalışır.

## Ekranlar

| Ekran | Ne yapar |
|---|---|
| **Genel Bakış** | Sağlık skorları, kritik/uyarı, P1 aksiyonlar, GPT filo özeti ve bugünün öncelikli işleri |
| **Sunucu detayı** | Aksiyonlar (komutlarıyla), anomaliler, kural bulguları, metrikler, trend grafikleri, ham loglar, GPT'ye giden istek |
| **Toplama & Analiz** | Topla ve analiz et, sadece yeniden analiz et, demo veri; çalıştırma geçmişi ve canlı ilerleme |
| **Gelen Loglar** | Sunucu × log bölümü matrisi (satır sayısı); hücreden ham çıktı |
| **UnixAI'ye Sor** | Sunucu veya filo bağlamıyla sohbet |
| **Sunucular** | Envanter, SSH testi, public key ve hedef kurulum komutları |
| **Token Kullanımı** | Günlük grafik, kullanıcı/tür dağılımı, limitler, CSV |
| **AI Logları** | Token servisi ve GPT istek/cevapları (maskeli) |
| **Ayarlar** | GenAI bağlantısı, SSH, zamanlama, eşikler, token limitleri, sistem mesajı |

## Zamanlama

Her gün *Ayarlar → Zamanlama*'daki saatte (varsayılan 06:30, `USAGE_TIMEZONE`) tüm etkin sunucular toplanır.
Servis o dakikada kapalıysa sonraki 15 dakika içinde telafi edilir. Saklama süresi varsayılan 90 gün.

## Güncelleme

```bash
cd /opt/unixai-os && git pull && systemctl restart unixai-os
```

## Giriş ve güvenlik

- LDAP yoksa yerel yönetici: `.env` → `ADMIN_USER` / `ADMIN_PASSWORD` (install.sh üretir). LDAP ayarları MorpheusAI ile aynı.
- Root SSH anahtarı kullanıldığı için giriş kapalıyken arayüz uyarı gösterir; sunucuda mutlaka birini açın.
- `LDAP_ADMIN_GROUP` tanımlıysa bağlantı, sunucu, ayar ve çalıştırma işlemleri yalnızca bu gruba açıktır.
- GPT'ye giden log içeriği için *Ayarlar → IP adreslerini maskele* seçeneği vardır; log metinleri modele "veri, talimat değil" olarak verilir.
- Token limiti dolunca AI çağrısı yapılmaz, sunucular kural motoruyla analiz edilir.

## Dosyalar

```
server.py                HTTP sunucusu + API (http.server, çok iş parçacıklı)
unixai/connection.py     GenAI bağlantı ayarları (arayüzden, maskeleme)
unixai/genai.py          TMS token + chat/completions istemcisi (urllib)
unixai/collector.py      agentless SSH toplayıcı (sistem ssh istemcisi, subprocess)
unixai/parser.py         komut çıktısı → metrikler
unixai/rules.py          eşik kuralları + disk dolma tahmini
unixai/analyzer.py       GPT sunucu analizi, filo özeti, sohbet
unixai/runs.py           çalıştırmalar, kayıt, zamanlayıcı
unixai/inventory.py      sunucu envanteri
unixai/usage.py          token kullanımı ve limitler
unixai/logger.py         AI istek/cevap logları (maskeli)
unixai/auth.py, ldap.py  LDAP / yerel yönetici girişi (bağımlılıksız LDAPv3 istemcisi)
public/                  arayüz (HTML/CSS/JS, MorpheusAI tasarımı)
scripts/                 smoke_test.py, fake_ssh.py
deploy/                  install.sh, systemd, podman, target-setup.sh
```
