# UnixAI OS - container imajı (Podman / Docker) — yalnızca Python standart kütüphanesi
FROM docker.io/library/python:3.12-alpine

# Agentless toplama için OpenSSH istemcisi; saat dilimi verisi
RUN apk add --no-cache openssh-client bash tzdata

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=3000

WORKDIR /app
COPY server.py ./
COPY unixai ./unixai
COPY public ./public

# Uygulama root kullanıcısıyla çalışır; SSH anahtarı host'tan /root/.ssh'a bağlanır
USER root
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python3", "-c", "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','3000')+'/api/health').status==200 else 1)"]

CMD ["python3", "server.py"]
