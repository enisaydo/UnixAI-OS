# Pixel Office — ajanların konuştuğu açık kaynak ofis

UnixAI OS ve MorpheusAI ajanları, açık kaynak **[Pixel Agents](https://github.com/pixel-agents-hq/pixel-agents)**
ofisinde piksel-art karakterler olarak çalışır: masalarında yazar, okur, birbirine soru sorar, playbook yazar,
onay bekler. Şablon yerele çekilip orada çalışır; Pixel Agents'ın koduna dokunulmaz, araya yalnızca bu klasördeki
**köprü** (`pixel-bridge.mjs`, bağımlılıksız) girer.

```
UnixAI / MorpheusAI canlı akışı (SSE /api/live) ──► pixel-bridge.mjs ──► Pixel Agents yerel sunucusu (POST /api/hooks/claude)
```

## Lisans (ücretsiz, açık kaynak)

| Bileşen | Lisans | Kaynak |
|---|---|---|
| Pixel Agents kodu | **MIT** | [LICENSE](https://github.com/pixel-agents-hq/pixel-agents/blob/main/LICENSE) |
| Mobilya, zemin, duvar, halı görselleri | **MIT** (depoyla birlikte) | Mart 2026'da "önceden özel lisanslı tüm görseller tamamen açık kaynak olanlarla değiştirildi" (commit `add749a`, #117) |
| Karakterler | **CC0** (ticari kullanım serbest, atıf gerekmez) | [JIK-A-4 · Metro City](https://jik-a-4.itch.io/metrocity-free-topdown-character-pack) |
| Köprü (`pixel-bridge.mjs`) | bu proje | — |

Elenen şablonlar: *Star Office UI* (görseller "ticari kullanım yasak"), *Pixel Office* (lisans dosyası yok).
Pixel Agents'ın belgelerinde önerilen ücretli tileset'ler (ör. Donarg) **kullanılmaz**; yalnızca pakette gelen açık görseller.

## 1) Şablonu yerele çekin

İnternetli bir makinede (veya kurum içi npm aynasıyla):

```bash
bash integrations/pixel-office/sablonu-indir.sh ~/pixel-office
```

- Sürüm sabittir (`pixel-agents@1.5.0`) ve npm kaydındaki **sha512** ile doğrulanır.
- Bağımlılıklarıyla (fastify) tek klasör olur; internetsiz sunucuya kopyalamak için:
  `tar czf pixel-office.tgz -C ~ pixel-office` → hedefte `tar xzf pixel-office.tgz -C ~`
- Gereksinim: Node.js 20+ (RHEL 9: `dnf module install nodejs:20`).

## 2) Ofisi başlatın

```bash
cd ~/pixel-office && ./baslat.sh          # PORT=3100 varsayılan; --no-terminal ile (tarayıcıdan komut çalıştırılamaz)
```

Yazdırılan `http://localhost:3100/?token=…` adresini açın, sonra **Settings → "Watch All Sessions"** açın
(köprünün karakterleri ofise alınsın). **Layout** ile ofisi düzenleyebilirsiniz: masa, bilgisayar, beyaz tahta,
kitaplık, kahve, bitki… Düzeni JSON olarak dışa/içe aktarabilirsiniz.

Ekipten başkaları izleyecekse: `./baslat.sh --host 0.0.0.0 --allowed-host ofis.kurum.local`. `?token=` içeren adres
gizlidir; tokensız adres yalnızca izleme verir.

## 3) Köprüyü başlatın (Pixel Agents ile aynı makinede)

```bash
UNIXAI_URL=http://unixai.kurum.local:8080 \
MORPHEUS_URL=http://morpheus.kurum.local:3000 \
node ~/pixel-office/pixel-bridge.mjs
```

| Değişken | Açıklama |
|---|---|
| `UNIXAI_URL`, `UNIXAI_TOKEN` | UnixAI adresi; giriş (LDAP) açıksa `.env`'deki `UNIXAI_API_TOKEN` |
| `MORPHEUS_URL`, `MORPHEUS_TOKEN` | MorpheusAI (isteğe bağlı); giriş açıksa `MORPHEUS_API_TOKEN`. Verilmezse Morpheus'un düzeltme akışı UnixAI üzerinden gelir |
| `PIXEL_URL`, `PIXEL_TOKEN` | Boşsa `~/.pixel-agents/servers/*.json`'dan otomatik bulunur |
| `NODE_EXTRA_CA_CERTS` | kurum içi HTTPS sertifikası (ör. `/etc/pki/tls/certs/ca-bundle.crt`) |

Köprü bağlantı koparsa kendiliğinden yeniden bağlanır; açılırken gelen eski olayları canlandırmaz.

## Ofiste ne görürsünüz

| UnixAI / MorpheusAI olayı | Ofiste |
|---|---|
| ajan açıldı | iki karakter: **UnixAI** ve **MorpheusAI** |
| UnixAI MorpheusAI'ye soru sorar | UnixAI: *Subtask: MorpheusAI'ye soruyor* · MorpheusAI: *Subtask: UnixAI'den soru: …* |
| sunucu bulgusu / tanı / liste araçları | *Reading …* (okuma animasyonu) |
| diğer araçlar | *Running: araç_adı* (yazma animasyonu) |
| playbook yazılıyor | MorpheusAI: *Subtask: Playbook yazıyor* → *Writing playbook-sunucu.yml* |
| playbook onay bekliyor | MorpheusAI üstünde **izin balonu** |
| onay → Bitbucket → AAP | *Running: onaylandı · …*, *git commit · Bitbucket*, *AAP proje sync*, *AAP check · sunucu* |
| toplama | UnixAI: *Subtask: Toplama · N sunucu*, sunucu sunucu *Reading rhel-…* |
| iş bitti | **Done** |

Not: Pixel Agents Claude Code'u da tanır; makinede Claude Code kullanılıyorsa onlar da ofiste görünebilir.
Köprü yalnızca olay gönderir; Pixel Agents'tan komut almaz, hiçbir şey çalıştırmaz.
