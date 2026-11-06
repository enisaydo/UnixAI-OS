# Trinity

**RHEL filosu için yapay zekâ destekli operasyon merkezi.** Her sabah tüm Linux sunucularını ajan kurmadan SSH ile tarar,
logları ve metrikleri kurumsal GenAI ile yorumlar, Red Hat Satellite'tan açık zafiyetleri çıkarır ve sorunları
**onayınızla** çalışan Ansible playbook'larına (MorpheusAI → AAP) dönüştürür. Ekip mesaiye başladığında özet Teams'te / e-postada olur.

| | |
|---|---|
| **Linux ekibi** | Önceliklendirilmiş günlük iş listesi; her bulgunun kanıtı, doğrulama ve düzeltme komutu; Trinity Ajanı ile doğal dilde soru-cevap ve salt-okunur canlı tanı |
| **Bilgi güvenliği** | CVE etki analizi (hangi sunucu etkileniyor, yama hazır mı), sunucu bazında zafiyet torbası, Red Hat önem derecesine göre SLA takibi, CSV |
| **Yönetim** | Yazdırılabilir yönetim raporu: filo sağlık trendi, kritik konular, zafiyetler, otomasyonla kazanılan süre |
| **Güvence** | Hedefe kurulum yok, salt-okunur toplama, onaysız değişiklik yok, LDAP + rol, maskeli sırlar, işlem kayıtları, veri kurum içinde |

Ürün tanıtım sayfası: Trinity çalışırken **`/tanitim.html`** (giriş ekranında ve menüde *Trinity hakkında*).

Teknik olarak: RHEL sunuculardan **agentless (SSH)** günlük log ve metrik toplayan, kural motoru + kurumsal **GenAI (GPT)** ile
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
python3 scripts/satellite_test.py  # sahte Satellite (HTTPS) + sahte MorpheusAI ile CVE etki analizi ve zafiyet torbası testi
python3 scripts/notify_test.py     # sahte Teams/Slack webhook + sahte SMTP ile bildirim testi
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
- Token yanıt header'ında geliyorsa (`authorization: Bearer: <token>`) *Token nereden alınsın* → **Yanıt header'ı** seçin; `Bearer` / `Bearer:` öneki temizlenir.
- Yer tutucular: `{{uuid}}` (her istekte yeni), `{{timestamp}}`, `{{token}}` (chat header'larında), `{{env:İSİM}}`.
- Gizli alanlar (password, client_secret, client-id, authorization…) tarayıcıya **maskeli** gelir; maskeyi değiştirmeden kaydederseniz eski değer korunur. Sırrı hiç arayüze yazmamak için `.env`'e koyup `{{env:İSİM}}` kullanın.
- Ayarlar `data/connection.json`'da (0600) saklanır; servis yeniden başlatmaya gerek yoktur. Token cache'lenir, 401/403'te yenilenir.
- `.env`'de MorpheusAI biçiminde `TMS_*` / `CHAT_*` varsa ilk değerler oradan gelir.

## MorpheusAI ile düzeltme (bulgu → standartlara uygun playbook → AAP)

Trinity bir sunucuda bulgu bulduğunda aksiyonu **MorpheusAI'ye gönderir**; MorpheusAI kurumsal Ansible standartlarına uygun
bir playbook üretir, standart analizinden geçirir (uyum skoru düşükse ihlalleri modele verip bir kez düzeltir) ve **onayınızı ister**.
Onaydan önce AAP'de hiçbir şey çalışmaz. Onaylayınca AAP'de **check (dry-run)** başlar; check başarılıysa **Sunucuya uygula** ile gerçek çalıştırma yapılır.

1. Trinity → **Ayarlar → MorpheusAI**: adres + `MORPHEUS_API_TOKEN`, AAP envanterindeki host adı (sunucu adı / adres).
2. Sunucu detayı → **Aksiyonlar** → *MorpheusAI'ye gönder* (veya Ayarlar'da "toplama bitince P1'leri otomatik gönder").
3. Playbook (YAML) ve **Onayla / Reddet / Sunucuya uygula** düğmeleri Trinity'de görünür: ajan sohbet cevabı, **MorpheusAI** sekmesi,
   sunucu detayı → MorpheusAI sekmesi (*YAML / Onay*) ve dashboard'daki **"N playbook onayınızı bekliyor"** bandı (menüde rozet).
   Onay MorpheusAI'ye kişinin adıyla aktarılır (`EnisA (unixai üzerinden)`); MorpheusAI → Düzeltmeler ekranından da onaylanabilir.
   Bunun için MorpheusAI'de **"Onay aktarabilir"** işaretli token üretip Trinity'ye girin (Ayarlar → MorpheusAI veya MCP bağlantısı).
4. AAP runner kurulumu: MorpheusAI reposunda `deploy/aap/README.md`.

### MCP

`mcp/unixai_mcp.py` (Trinity) ve MorpheusAI'deki `mcp/morpheus-mcp.js` bir MCP istemcisine (Claude Code / Desktop)
birlikte eklenince iki sistem araç çağrılarıyla konuşur. Ayrıntılar ve örnek yapılandırma: [`mcp/README.md`](mcp/README.md).

## CVE takibi (Red Hat Satellite 6.17)

Güvenlik ekibinin ilettiği bir CVE için hangi sunucuların etkilendiği **Satellite'tan** bulunur; Satellite'ın göremediği
sunucular SSH ile doğrulanır. Satellite'ta ve sunucularda hiçbir şey değiştirilmez.

1. **Ayarlar → Satellite**: adres (`https://gbsatp04`; kısa ad çözülmüyorsa FQDN), salt-okunur kullanıcı (Viewer) ve parola
   veya Personal Access Token. Satellite CA'sını merkez sunucuya güvenin:
   `curl -ko /etc/pki/ca-trust/source/anchors/katello-ca.crt https://gbsatp04/pub/katello-server-ca.crt && update-ca-trust`
2. **CVE Takibi** ekranı (veya Genel Bakış'taki güvenlik kartı) → CVE numarası → *Etki analizi yap*.
3. Akış: `cve = CVE-…` ile errata (RHSA) → `applicable_errata` / `installable_errata` ile host'lar → Trinity envanteriyle
   eşleştirme (FQDN, kısa ad, IP) → Satellite'ta kayıtlı olmayan veya check-in'i eski sunucularda `dnf updateinfo list --cve`.

| Durum | Anlamı | Aksiyon |
|---|---|---|
| Etkileniyor · yama hazır | Errata uygulanabilir ve content view'de var | *MorpheusAI'ye gönder* → playbook → onay → AAP |
| Etkileniyor · yama content view'de yok | Errata uygulanabilir ama kurulabilir değil | Satellite'ta content view publish/promote |
| Etkileniyor (SSH ile doğrulandı) | Satellite'ta yok, sunucunun kendi depolarına göre etkileniyor | MorpheusAI'ye gönder; sunucuyu Satellite'a kaydet |
| Satellite'ta kayıtlı değil / verisi eski / doğrulanamadı | Sonuç kesin değil | Kayıt, check-in veya SSH erişimi |
| Etkilenmiyor / yamalı | Errata bu host'a uygulanamıyor | — |

Genel Bakış'ta: Satellite host sayısı, güvenlik yaması bekleyen host'lar, check-in'i eski host'lar, Satellite'ta olmayan
Trinity sunucuları ve filoyu etkileyen son Critical/Important errata'lar (10 dk önbellek). Raporlar `data/cve_reports.json`,
API çağrıları `logs/satellite/`. Ajan ve MCP istemcileri `unixai_cve_impact` aracıyla aynı analizi yapabilir.

Satellite'ta errata'sı olmayan CVE'ler (Red Hat henüz yama yayınlamadıysa) "errata yok" olarak raporlanır; ürün durumu için
Red Hat CVE sayfasına bakılır.

### Zafiyet torbası (sunucu bazında)

**Zafiyet Torbası** ekranı (ve sunucu detayındaki *Zafiyetler* sekmesi) her sunucuda açık olan güvenlik errata'larını ve
CVE'leri tutar. *Tüm filoyu tara* (veya tek sunucuda *Yeniden tara*):

- Satellite'taki host için `GET /katello/api/errata?host_id=…&errata_restrict_applicable=true` (+ `errata_restrict_installable`
  ile yamanın content view'de hazır olup olmadığı), `search=type = security`.
- Satellite'ta olmayan veya check-in'i eski Trinity sunucusu için SSH ile `dnf updateinfo list --security [--with-cve]`
  (*SSH ile tamamla* işaretliyse).
- Her errata için önem derecesi, CVE'ler, yayın tarihi, açık gün ve **SLA aşımı** (yayından itibaren Critical 15, Important 30,
  Moderate 90, Low 180 gün; `unixai/satellite.py` → `SLA_DAYS`), yeniden başlatma önerisi, torbaya ilk giriş tarihi.
- Yeniden taramada artık görünmeyen errata **Kapananlar** listesine düşer (kapanma zamanıyla).
- Filo görünümü: sunucu başına C·I·M·L sayıları, SLA aşan, yaması hazır; filoda en yaygın CVE'ler (→ *Etki analizi*).
- *MorpheusAI'ye gönder*: yaması hazır Critical+Important (veya tümü) errata'lar tek bulgu olarak gider
  (`dnf -y update --advisory=… --advisory=…`) → playbook → onay → AAP. CSV dışa aktarma güvenlik ekibi için.
- **Errata detayı** (torbada satıra tıklayın): zafiyetin içeriği (Red Hat açıklaması ve özeti), Red Hat'in çözüm metni, CVE'ler
  (Red Hat CVE sayfası + filo etki analizi), Bugzilla kayıtları, düzelten paket sürümleri; o sunucu için çözüm adımları
  (`dnf update --advisory=…`, gerekirse reboot, Satellite Remote Execution `hammer job-invocation …`, doğrulama) ve
  yama content view'de yoksa publish/promote notu. Tek errata için de MorpheusAI playbook'u üretilebilir.
  Kaynak `GET /katello/api/errata/:id`; Satellite'ta yoksa SSH ile `dnf updateinfo info`. İçerik `data/errata_cache.json`'da 7 gün tutulur
  (*İçeriği yenile* ile zorlanır). API: `GET /api/errata/RHSA-…?server=…`; ajan aracı `unixai_vuln_bag` → `advisory`.

Torbalar `data/vuln_bags.json`'da. Ajan ve MCP istemcileri `unixai_vuln_bag` aracıyla okur (server boşsa filo özeti).

## Ajan ve MCP (birbiriyle konuşan iki ajan)

Trinity bir **ajandır**: kurumsal GPT + araçlarla çok adımlı çalışır. Araçlarını `POST /mcp` (Streamable HTTP) üzerinden başka alanlara **barındırır** (arayüzden üretilen token'lar, salt-okunur seçeneği, izinli Origin listesi) ve başka MCP sunucularına **istemci olarak bağlanır** (ör. MorpheusAI, diğer ekiplerin ajanları). İki uygulama `*_agent_ask` araçlarıyla birbirinin ajanına doğal dilde soru sorar; hop sınırı döngüyü önler. Gerçek uygulama her zaman insan onayı gerektirir.

Kurulum ve mimari: [`mcp/README.md`](mcp/README.md) · Arayüz: **Ajan & MCP**

## Sunucular ve SSH

1. **Sunucular → Sunucu ekle / Toplu ekle** (`ad adres [kullanıcı] [port]` satırları).
2. **Sunucular → SSH anahtarı** kartındaki komutla her hedefte public key'i yetkilendirin:
   ```bash
   bash target-setup.sh "$(cat /root/.ssh/id_rsa.pub)" <merkez_ip>   # from= kısıtlı, no-pty
   ssh-keyscan -H <hedef> >> /root/.ssh/known_hosts                           # merkezde
   ```
3. Satırdaki **SSH testi** ile doğrulayın. Hatalar yol gösteren mesaja çevrilir (host key, Permission denied, DNS…).

Toplama tek SSH oturumunda `bash -s` ile salt-okunur komutlar çalıştırır: df/inode, bellek, vmstat, load,
failed unit'ler, journal hataları, başarısız SSH girişleri, kernel uyarıları, SELinux/AVC, chrony, RHSA güncellemeleri,
needs-restarting, top prosesler, zombie. Root değilse ayrıcalıklı komutlar `sudo -n` veya Centrify `dzdo -n` ile
çalışır (Ayarlar → SSH → yetki yükseltme). SSH testi, parolasız yetki yükseltmenin çalışıp çalışmadığını da söyler.

### Kerberos ile toplama (anahtarsız, AD + Centrify)

Hedefler Centrify ile AD'ye bağlıysa Trinity anahtar yerine bir AD servis hesabının Kerberos biletiyle bağlanabilir.
Hedeflerde Trinity'ye ait anahtar tutulmaz; erişim AD'de hesap kapatılarak kesilir, oturumlar AD kullanıcısıyla Centrify
denetimine düşer.

1. AD'de servis hesabı (ör. `svc-trinity`) ve keytab: `adkeytab --new --upn svc-trinity@KURUM.LOCAL --keytab /etc/trinity/trinity.keytab svc-trinity`
   (veya Windows'ta `ktpass ... /crypto AES256-SHA1`). Keytab `chmod 600`, sahibi Trinity servis kullanıcısı.
2. Trinity sunucusunda `krb5-workstation` (kinit, klist) ve `/etc/krb5.conf` (Centrify `adjoin` ile hazır gelir); DC'lere 88/TCP-UDP.
3. Centrify zone'unda hesaba hedeflere giriş izni ve toplama komutları için parolasız `dzdo`.
4. **Ayarlar → SSH**: Kimlik doğrulama *Kerberos*, kullanıcı `svc-trinity`, hesap `svc-trinity@KURUM.LOCAL`, keytab yolu, yetki yükseltme *dzdo*.
5. Envanterdeki adresler **tam alan adı** (FQDN) olmalı; Kerberos IP ile çalışmaz.

Bilet Trinity'nin kendi önbelleğinde (`data/krb5cc_trinity`) tutulur, gerektiğinde keytab ile yenilenir (`KRB_RENEW_AFTER`, varsayılan
30 dk); bilet karşı sunucuya kopyalanmaz (`GSSAPIDelegateCredentials=no`). Durum ve kurulum komutları **Sunucular** ekranındaki kartta.
Geçiş sunucu sunucu yapılabilir: sunucuyu düzenleyip kimlik doğrulamayı tek tek *Kerberos* veya *SSH anahtarı* seçin.

## Ekranlar

| Ekran | Ne yapar |
|---|---|
| **Genel Bakış** | Sağlık skorları, kritik/uyarı, P1 aksiyonlar, GPT filo özeti ve bugünün öncelikli işleri |
| **Sunucu detayı** | Aksiyonlar (komutlarıyla), anomaliler, kural bulguları, metrikler, trend grafikleri, ham loglar, GPT'ye giden istek |
| **Toplama & Analiz** | Topla ve analiz et, sadece yeniden analiz et, demo veri; çalıştırma geçmişi ve canlı ilerleme |
| **Gelen Loglar** | Sunucu × log bölümü matrisi (satır sayısı); hücreden ham çıktı |
| **CVE Takibi** | Satellite'tan CVE etki analizi: etkilenen / belirsiz / etkilenmeyen sunucular, aksiyon maddeleri, MorpheusAI'ye gönderim |
| **Zafiyet Torbası** | Sunucu bazında açık güvenlik errata'ları / CVE'ler, SLA aşımı, kapananlar, CSV, MorpheusAI'ye toplu gönderim |
| **Trinity Ajanı** | Araç kullanan ajan: sunucu verisi, canlı salt-okunur tanı, MorpheusAI ajanıyla konuşma; adımlar cevabın altında |
| **Ajan & MCP** | Barındırılan /mcp, erişim token'ları, izinli alanlar, bağlı MCP sunucuları, ajan ayarları ve konuşmaları |
| **Sunucular** | Envanter, SSH testi, public key ve hedef kurulum komutları |
| **Token Kullanımı** | Günlük grafik, kullanıcı/tür dağılımı, limitler, CSV |
| **AI Logları** | Token servisi ve GPT istek/cevapları (maskeli) |
| **Yönetim Raporu** | Son 7 / 30 / 90 gün: filo sağlık puanı ve trendi, kritik sunucular, öne çıkan P1'ler, açık zafiyetler ve SLA, MorpheusAI düzeltmeleri, otomasyonla kazanılan süre. *Yazdır / PDF* ile A4 |
| **Ayarlar** | GenAI bağlantısı, MorpheusAI, Satellite, bildirimler, SSH, zamanlama, eşikler, token limitleri, sistem mesajı |

Arayüz koyu (varsayılan) ve açık temayı destekler (üst çubuktaki ay simgesi veya `?theme=light`).
**Ctrl+K** (veya `/`) ile sunucu, ekran ve işlem araması açılır; genel bakışta sunucular kart veya tablo olarak,
duruma göre filtrelenerek görüntülenebilir. Aksiyon komutları terminal bloğunda tek tıkla kopyalanır.

## Zamanlama

Her gün *Ayarlar → Zamanlama*'daki saatte (varsayılan 06:30, `USAGE_TIMEZONE`) tüm etkin sunucular toplanır.
Servis o dakikada kapalıysa sonraki 15 dakika içinde telafi edilir. Saklama süresi varsayılan 90 gün.
*Ardından zafiyet torbasını tara* açıksa (varsayılan) toplama bitince Satellite + SSH ile zafiyet torbaları da tazelenir.

## Yönetim raporu

**Yönetim Raporu** ekranı yöneticiye / güvenlik ekibine gönderilecek tek sayfalık özeti üretir (`GET /api/report?days=30`):
filo sağlık puanı ve dönem başına göre değişimi, günlük trend grafiği, en düşük puanlı sunucular, öne çıkan P1 sorunlar,
Satellite zafiyet özeti (Critical / Important, SLA aşan, en yaygın CVE'ler), MorpheusAI → AAP düzeltme sayıları ve
otomatik analizle kazanılan tahmini süre (varsayım raporda yazılıdır: sunucu başına 15 dk elle kontrol, `unixai/report.py`).
*Yazdır / PDF* düğmesi menüleri gizleyip açık temada A4 çıktı verir. Envanter tanımlıysa demo verisi rapora girmez.

## Bildirimler (Teams / Slack / e-posta)

**Ayarlar → Bildirimler**: ekip Trinity'yi açmadan haberdar olur.

| Olay | Ne gider |
|---|---|
| Toplama ve analiz bitti | Filo özeti, kritik / uyarıdaki sunucular, ortalama sağlık puanı, P1 aksiyonlar, toplanamayan sunucular |
| Kritik sunucu / P1 bulgu | Günlük özet kapalıyken bile yalnızca sorun varsa |
| Playbook onay bekliyor | MorpheusAI'nin ürettiği playbook: sunucu, öncelik, gönderen |
| Zafiyet taraması | Critical ve SLA'yı aşan errata sayıları, en kötü sunucular |

- **Webhook**: Microsoft Teams (kanal → *Workflows* → "Post to a channel when a webhook request is received"; Adaptive Card),
  Slack incoming webhook veya genel JSON (kendi otomasyonunuz / ITSM için). Adres gizlidir; loglara yalnızca host yazılır.
  Internet'e proxy ile çıkılıyorsa servis ortamındaki `https_proxy` / `no_proxy` kullanılır (`NOTIFY_NO_PROXY=true` ile doğrudan).
- **E-posta**: kurum SMTP relay'i (STARTTLS / SSL / düz), opsiyonel kullanıcı-parola, birden fazla alıcı; HTML + düz metin.
- *Kaydet + test bildirimi gönder* ile anında doğrulanır; her gönderim `logs/bildirim/` altına yazılır.
- *Trinity adresi* girilirse bildirimde **Trinity'de aç** düğmesi çıkar.

## Güncelleme

```bash
cd /opt/unixai-os && git pull && systemctl restart unixai-os
```

## Yedekleme

```bash
bash deploy/backup.sh                         # → /var/backups/trinity/trinity-YYYYmmdd-HHMM.tar.gz (data/ + .env, son 14 yedek)
# günlük: root crontab →  15 5 * * * /opt/unixai-os/deploy/backup.sh >/dev/null
# geri yükleme: systemctl stop unixai-os && tar -C /opt/unixai-os -xzf <yedek> && systemctl start unixai-os
```

`BACKUP_DIR`, `KEEP` (yedek sayısı) ve `INCLUDE_LOGS=true` ile ayarlanır. Yedek parola/token içerir; 0600 yazılır.

## Giriş ve güvenlik

- LDAP yoksa yerel yönetici: `.env` → `ADMIN_USER` / `ADMIN_PASSWORD` (install.sh üretir). LDAP ayarları MorpheusAI ile aynı.
- Root SSH anahtarı kullanıldığı için giriş kapalıyken arayüz uyarı gösterir; sunucuda mutlaka birini açın.
- `LDAP_ADMIN_GROUP` tanımlıysa bağlantı, sunucu, ayar ve çalıştırma işlemleri yalnızca bu gruba açıktır.
- GPT'ye giden log içeriği için *Ayarlar → IP adreslerini maskele* seçeneği vardır; log metinleri modele "veri, talimat değil" olarak verilir.
- Token limiti dolunca AI çağrısı yapılmaz, sunucular kural motoruyla analiz edilir.
- **CSRF koruması**: tarayıcıdan gelen durum değiştiren istekler JSON gövde ister ve başka siteden geldiğini bildiren (`Sec-Fetch-Site`) istekler reddedilir;
  önündeki proxy Host başlığını değiştirse de çalışır. Bearer token'lı servis çağrıları muaftır.
- `X-Forwarded-For` yalnızca `TRUSTED_PROXIES`'teki adreslerden (varsayılan yerel nginx: `127.0.0.1,::1`) gelirse dikkate alınır;
  böylece denetim kaydındaki ve giriş kilidindeki IP taklit edilemez.

## Pixel Office — ajanların konuştuğu açık kaynak ofis

Trinity ve MorpheusAI ajanları, açık kaynak (MIT kod, MIT/CC0 görseller) **Pixel Agents** piksel-art ofisinde karakter
olarak çalışır: soru sorar, playbook yazar, onay bekler. Şablon yerele çekilir (sürüm sabit, sha512 doğrulamalı),
`.env`'de `PIXEL_OFFICE=true` olunca Trinity aynı makinedeki ofise olayları doğrudan gönderir (köprü gerekmez):
[`integrations/pixel-office/README.md`](integrations/pixel-office/README.md).

## Ajan izleme arayüzü (Langfuse)

Kurum içine kurulan [Langfuse](https://langfuse.com) ile her ajan çalışması, araç adımı ve GPT çağrısı (model, token, süre)
izlenir; Trinity ⇄ MorpheusAI konuşması tek iz olarak görünür. Kurulum resmi docker-compose şablonuyla tek komut:
`sudo bash deploy/langfuse/kur.sh --host langfuse.kurum.local` → ayrıntı: [`deploy/langfuse/README.md`](deploy/langfuse/README.md).
`.env`'de `LANGFUSE_*` boşsa izleme kapalıdır.

## Loglar

Sistemin yaptığı her işlem kategorisine göre **ayrı dosyaya** yazılır: `logs/<kategori>/<kategori>-YYYY-MM-DD.jsonl`
(`LOG_RETENTION_DAYS` gün saklanır). Arayüzde **Log Dosyaları** ekranından kategori/tarih seçip arayabilirsiniz.

| Dosya | İçerik |
|---|---|
| `logs/islem/` | arayüz/API/MCP'den yapılan her değişiklik: kim, IP, ne, sonuç (İşlem Kayıtları ekranı ile aynı) |
| `logs/giris/` | giriş denemeleri |
| `logs/ssh/` | hedef sunuculara her SSH bağlantısı: sunucu, kullanıcı, amaç (toplama/test/tanı), çıkış kodu, süre, hata |
| `logs/toplama/` | toplama çalıştırmaları: başladı, her sunucunun sonucu (skor, P1 sayısı), bitti |
| `logs/tani/` | canlı salt-okunur tanı komutları |
| `logs/genai/` | TMS token ve GPT çağrıları |
| `logs/satellite/` | Satellite API çağrıları ve CVE etki analizleri |
| `logs/morpheus/` | MorpheusAI köprüsü: bulgu gönderimi, onay/ret/uygulama aktarımı, durum sorguları |
| `logs/mcp-istemci/`, `logs/mcp-sunucu/` | giden / gelen MCP çağrıları |
| `logs/ajan/`, `logs/ajanlar-arasi/` | ajanın araç adımları, Trinity ⇄ MorpheusAI konuşmaları |

Parola ve secret'lar yazılmaz; token'ların yalnızca ilk 4 karakteri görünür.

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
unixai/satellite.py      Red Hat Satellite: CVE etki analizi, güvenlik özeti
unixai/inventory.py      sunucu envanteri
unixai/usage.py          token kullanımı ve limitler
unixai/logger.py         AI istek/cevap logları (maskeli)
unixai/auth.py, ldap.py  LDAP / yerel yönetici girişi (bağımlılıksız LDAPv3 istemcisi)
public/                  arayüz (HTML/CSS/JS, MorpheusAI tasarımı)
scripts/                 smoke_test.py, fake_ssh.py
deploy/                  install.sh, systemd, podman, target-setup.sh
```
