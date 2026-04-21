# bot_nordeste

Bot para Telegram que coleta notícias (obras + política no Nordeste) e publica no chat/canal.

## Requisitos
- Docker + Docker Compose

## Configuração
1. Crie um arquivo `.env` a partir de `.env.example`.
2. Preencha `BOT_TOKEN` e `CHAT_ID` (ex.: `-100...`).
3. (Opcional) Telegraph:
	- `USE_TELEGRAPH=0` (padrão): botão **LEITURA RÁPIDA** aponta direto para a notícia.
	- `USE_TELEGRAPH=1`: botão aponta para uma página criada no Telegraph (requer `TELEGRAPH_TOKEN`).
4. Garanta que o bot seja admin no canal (se for canal).

## Rodar
```bash
docker compose up --build -d
```

## Teste manual (sem esperar 40 minutos)
- O coletor roda imediatamente ao iniciar o container. Para forçar uma nova coleta agora:
	- `docker compose restart bot`
- Para acompanhar ao vivo:
	- `docker compose logs -f bot`

### Fluxo recomendado de teste
1. Coloque `DRY_RUN=1` no seu `.env` (não posta de verdade no Telegram).
2. Para acelerar ainda mais, use temporariamente:
	 - `FETCH_INTERVAL_MIN=1`
	 - `SEND_INTERVAL_MIN=1`
	 - `IDLE_POLL_SEC=5`
3. Rode `docker compose up -d --build` e observe os logs.

Quando estiver OK, volte `DRY_RUN=0` e os intervalos desejados.

## Observações
- O bot busca a cada `FETCH_INTERVAL_MIN` minutos e envia um item a cada `SEND_INTERVAL_MIN` minutos (enquanto houver fila).
- Envio só ocorre entre `SEND_WINDOW_START` e `SEND_WINDOW_END` no fuso `TIMEZONE`.
- Se `REQUIRE_IMAGE=1`, o bot não envia sem foto (usa `IMAGE_FALLBACK=1` para buscar no Wikimedia Commons quando necessário).
- O bot evita enviar links que caiam na página inicial do portal; se detectar isso, ele reprograma o envio para tentar novamente.
