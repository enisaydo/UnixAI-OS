# Langfuse — ajan izleme arayüzü (kurum içi)

UnixAI OS ve MorpheusAI'nin her ajan çalışması, araç adımı ve GPT çağrısı (model, token, süre) Langfuse'a
**OpenTelemetry (OTLP/HTTP JSON)** ile gönderilir. UnixAI MorpheusAI'ye soru sorduğunda iz bağlamı MCP üzerinden
aktarılır: **iki ajanın konuşması Langfuse'ta tek iz** olarak görünür (UnixAI ajanı → `morpheus_agent_ask` adımı →
MorpheusAI ajanı → playbook üretimi → GPT çağrıları).

Langfuse MIT lisanslıdır ve kurum içine kurulur; veri dışarı çıkmaz. Kurulum resmi docker-compose şablonunu kullanır.

## Kurulum (ayrı bir sunucu önerilir: ≥4 çekirdek, 16 GB bellek, ~100 GB disk)

```bash
sudo bash deploy/langfuse/kur.sh --host langfuse.kurum.local
```

- Resmi şablonu indirir (internet yoksa: dosyayı başka yerden alıp `--compose-file docker-compose.yml`)
- Tüm parolaları rastgele üretir (`/opt/langfuse/.env`, izin 600), telemetriyi kapatır
- Organizasyon, proje (`unixai-morpheus`), API anahtarları ve ilk kullanıcıyı otomatik oluşturur
- `podman compose` / `podman-compose` / `docker compose` ile başlatır, portu firewall'da açar
- 3000 portu doluysa (aynı sunucuda MorpheusAI) 3100'ü kullanır; `--port` ile değiştirilebilir

Sonda yazdırılan satırları **iki uygulamanın** `.env` dosyasına ekleyip servisleri yeniden başlatın:

```env
LANGFUSE_URL=http://langfuse.kurum.local:3000
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_PROJECT_ID=unixai-morpheus
```

İsteğe bağlı: `LANGFUSE_CAPTURE_IO=false` (soru/cevap/argüman gönderilmez; yalnızca adlar, süreler, token),
`LANGFUSE_TLS_INSECURE=true` (kurum içi sertifika), `LANGFUSE_ENVIRONMENT=test`.

## Uygulamalarda

- Konuşma sayfasında (UnixAI → MorpheusAI sekmesi, MorpheusAI → UnixAI sekmesi) **Langfuse izleri ↗** ve her
  konuşma/görev/sohbet ayrıntısında **Langfuse'ta aç ↗**
- `GET /api/tracing/status`: açık mı, gönderilen/düşen gözlem sayısı, son hata
- Langfuse kapalı veya erişilemezse uygulamalar etkilenmez; izler düşer

## Langfuse'ta ne görürsünüz

| Gözlem | İçerik |
|---|---|
| `UnixAI ajanı` / `MorpheusAI ajanı` (agent) | soru, cevap, kullanıcı, hop |
| `araç · …` (tool / agent) | argümanlar, sonuç, nerede çalıştı (yerel / bağlı MCP) |
| `GPT · agent`, `GPT · chat`… (generation) | model, girdi/çıktı token, süre |
| `Düzeltme playbook'u · sunucu` (chain) | playbook üretimi + standart analizi |
| `Sunucu analizi · sunucu` (chain) | toplama sonrası GPT analizi |
