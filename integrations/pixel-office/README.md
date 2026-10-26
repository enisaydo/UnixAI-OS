# Pixel Office — ajanların konuştuğu açık kaynak ofis

UnixAI OS ve MorpheusAI ajanları, açık kaynak **[Pixel Agents](https://github.com/pixel-agents-hq/pixel-agents)**
ofisinde piksel-art karakterler olarak çalışır: masalarında yazar, okur, birbirine soru sorar, playbook yazar,
onay bekler. Şablon her makineye yerel olarak çekilir; Pixel Agents'ın koduna dokunulmaz.

**Köprü gerekmez:** UnixAI ve MorpheusAI, işledikleri her GET/POST, MCP çağrısı ve ajan adımında **aynı makinedeki**
Pixel Agents ofisine doğrudan olay gönderir (`.env`: `PIXEL_OFFICE=true`). Pixel Agents dışarıdan yalnızca kendi hook
biçimini (`POST /api/hooks/claude` + kendi token'ı) kabul ettiği için çeviri uygulamaların içinde yapılır; token aynı
makinede `~/.pixel-agents/` altından otomatik bulunur.

```
Makine A:  UnixAI OS ──(PIXEL_OFFICE=true)──► Pixel Agents (yerel)      ofiste: UnixAI + MorpheusAI karakterleri
Makine B:  MorpheusAI ──(PIXEL_OFFICE=true)──► Pixel Agents (yerel)     ofiste: UnixAI + MorpheusAI karakterleri
```

İsteğe bağlı: iki uygulamayı **üçüncü bir makinedeki tek ortak ofiste** görmek isterseniz `pixel-bridge.mjs`
(bağımlılıksız) iki uygulamanın canlı akışını okuyup o ofise gönderir — bkz. en alttaki bölüm.

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

## 3) Uygulamada açın (her makinede)

UnixAI makinesinde UnixAI `.env`'ine, MorpheusAI makinesinde MorpheusAI `.env`'ine ekleyip servisi yeniden başlatın:

```env
PIXEL_OFFICE=true
# Pixel Agents aynı makinede ve aynı kullanıcıyla (ör. root) çalışıyorsa bu ikisi boş kalabilir (otomatik bulunur):
PIXEL_URL=
PIXEL_TOKEN=
```

- Uygulama Podman container'ında çalışıyorsa container host'un `127.0.0.1`'ine erişemez. Pixel Agents'ı host
  arayüzünde açın (`./baslat.sh --host 0.0.0.0`; 3100 portunu firewall'da dışarıya **açmayın**) ve uygulamaya
  `PIXEL_URL=http://host.containers.internal:3100` ile host'taki `~/.pixel-agents/servers/*.json` içindeki
  `token` değerini `PIXEL_TOKEN` olarak verin. Hook ucu token'sız isteği reddeder.
- Durum: `GET /api/pixel/status` (açık mı, gönderilen/başarısız olay sayısı, son hata).
- Pixel Agents kapalıysa uygulamalar etkilenmez.

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

## İsteğe bağlı: tek ortak ofis (üçüncü makine, köprü)

```bash
UNIXAI_URL=http://unixai.kurum.local:8080 MORPHEUS_URL=http://morpheus.kurum.local:3000 node ~/pixel-office/pixel-bridge.mjs
```

| Değişken | Açıklama |
|---|---|
| `UNIXAI_URL`, `UNIXAI_TOKEN` | UnixAI adresi; giriş (LDAP) açıksa `.env`'deki `UNIXAI_API_TOKEN` |
| `MORPHEUS_URL`, `MORPHEUS_TOKEN` | MorpheusAI (isteğe bağlı); giriş açıksa `MORPHEUS_API_TOKEN` |
| `PIXEL_URL`, `PIXEL_TOKEN` | boşsa `~/.pixel-agents/servers/*.json`'dan otomatik |
