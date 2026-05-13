# Architecture — eBasketball H2h 4x5min Bot

## Visão Geral

Bot automatizado para palpites eBasketball H2h 4x5min (NBA 2K).
Fonte única: tipmanager.net. Motor de palpites baseado em stats locais acumuladas.
Projetado para rodar em 256MB RAM em cloud (NullPool, sem conexões persistentes).

```
┌───────────────────────────────────────────────────────────────┐
│                        FastAPI App                            │
│                                                               │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │ /api/    │  │ /webhook/    │  │  Telegram Polling     │   │
│  │ routes   │  │ telegram     │  │  (dev local)          │   │
│  └────┬─────┘  └──────┬───────┘  └──────────┬────────────┘   │
│       │               │                     │                │
│       └───────────────▼─────────────────────▼──────┐        │
│                    handler.py                       │        │
│              /palpites /resultados /stats           │        │
│                        │                            │        │
│  ┌─────────────────────▼────────────────────────┐   │        │
│  │            scrapers/tipmanager.py             │   │        │
│  │   fetch_upcoming() + fetch_results()          │   │        │
│  └─────────────────────┬────────────────────────┘   │        │
│                        │                            │        │
│  ┌─────────────────────▼────────────────────────┐   │        │
│  │               prediction.py                   │   │        │
│  │   PlayerLocalStats → expected_total + over    │   │        │
│  │   sem dados → None (posta sem palpite)        │   │        │
│  └─────────────────────┬────────────────────────┘   │        │
│                        │                            │        │
│  ┌──────────┐  ┌───────▼──────┐  ┌───────────────┐ │        │
│  │scheduler │  │ telegram/    │  │ infra/models  │ │        │
│  │ + jobs   │  │ client       │  │ Prediction    │ │        │
│  └────┬─────┘  └──────┬───────┘  │ PlayerLocal.. │ │        │
└───────┼───────────────┼──────────┴───────────────┘─┘        │
        │               │                                      │
        │      ┌────────▼──┐    ┌────────────┐                │
        └─────►│ Telegram  │    │ PostgreSQL │                │
               │  Bot API  │    │            │                │
               └───────────┘    └────────────┘
```

## Ciclo Principal (Job eBasketball, 240s)

```
1. fetch_all() → tipmanager.net
   ├→ upcoming: próximos jogos com kickoff, times, jogadores
   └→ results: últimos 10 com placar

2. Filtro janela: kickoff entre (agora - 4min) e (agora + 10min)

3. Para cada match (deduplicação via match_key):
   └→ generate_prediction(session, match)
      ├→ tem stats locais de ambos → PredictionResult(expected_total, over_line)
      └→ sem dados → None → posta jogo sem palpite ("📊 Coletando dados...")

4. send_message(chat_id, texto)
   └→ Prediction salvo: status=pending, message_id=X

5. Para cada Prediction pendente:
   ├→ busca resultado em fetch_results() por home_player + away_player
   ├→ editMessageText com resultado + ✅/❌
   ├→ _update_local_stats(home, away) → atualiza PlayerLocalStats
   └→ Prediction: status=done
```

## Estrutura de Arquivos

```
app/
  main.py                  → Entry point, lifespan, webhook/polling
  routes.py                → APIRouter /api/*
  scheduler.py             → Registro e execução de jobs periódicos
  prediction.py            → Motor de palpites (local stats only)
  scrapers/
    tipmanager.py          → Scrap tipmanager.net (upcoming + results)
  jobs/
    ebasketball.py         → Ciclo: send_predictions + update_results
  infra/
    config.py              → Settings via pydantic-settings
    database.py            → Engine async (pool local / NullPool cloud)
    models.py              → SentMessage, PlayerLocalStats, Prediction
  telegram/
    client.py              → httpx → Telegram Bot API (retry 429)
    handler.py             → Comandos: /palpites /resultados /stats
    polling.py             → Long polling para dev local
    service.py             → send_and_store, edit_by_reference
```

## Scraper — tipmanager.py

- **Fonte**: HTML server-side, uma única requisição por ciclo
- **Tabelas**: `<table>` — índice 0 = upcoming, índice 1 = results
- **Upcoming**: rows com `aria-label` para times/jogadores + `<span>` com hora
- **Results**: rows com `aria-label` + `div.bg-accent` com placar home/away
- **Timezone**: site exibe UTC → convertido para BRT na ingestão
- **Sem cache** — ciclo de 4min já espaça as requisições

## Motor de Palpites

### Fontes

```
PlayerLocalStats (banco) ←── única fonte
sem dados → retorna None → jogo postado sem palpite
```

### Cálculo

```python
home_expected = (home_avg_pf + away_avg_pa) / 2
away_expected = (away_avg_pf + home_avg_pa) / 2
expected_total = home_expected + away_expected
over_line = max(90.5, round((expected_total - 7.5) * 2) / 2)
```

### Deduplicação

`match_key = f"{kickoff_brt:%Y%m%d_%H%M}_{home_player}_{away_player}"`

Constraint `UNIQUE` no banco. Prediction existente → ciclo ignora o jogo.

## Banco de Dados

SQLAlchemy 2.0 async com asyncpg.

**Pool**: `pool_size=5 / max_overflow=5` para localhost. `NullPool` para cloud (detectado por ausência de `localhost/127.0.0.1/0.0.0.0` na URL).

**Timezone**: `SET timezone TO 'UTC'` injetado em toda conexão via `event.listens_for(connect)`. asyncpg retorna datetimes naive em UTC — `_format_brt_time` trata como UTC antes de converter para BRT.

### Tabelas

| Tabela | PK | Descrição |
|--------|-----|-----------|
| `sent_messages` | UUID7 | Mensagens enviadas com reference_key |
| `player_local_stats` | player (str) | Stats acumuladas (PF, PA, W/D/L) |
| `predictions` | UUID7 | Palpites com resultado e sucesso |

Schema isolado via `DB_SCHEMA` (default `ebasketball_bot`).
Tabelas criadas no lifespan via `Base.metadata.create_all`.

## Telegram

### Modos de operação

| Modo | Quando usar | Env |
|------|------------|-----|
| **Polling** | Dev local, sem URL pública | `TELEGRAM_POLLING=true` |
| **Webhook** | Produção com HTTPS | `TELEGRAM_POLLING=false` |

### Comandos disponíveis

| Comando | Handler | Ação |
|---------|---------|------|
| `/palpites` | `_cmd_palpites` | `send_predictions(window_minutes=None)` |
| `/resultados` | `_cmd_resultados` | `update_results()` |
| `/stats <nome>` | `_cmd_stats` | Query `PlayerLocalStats ILIKE %nome%` |
| `/start` | inline | Lista de comandos |
| `/ping` | inline | pong |

## Timezone

Todos os horários internos armazenados e processados em UTC. Exibição em BRT.

| Ponto | Comportamento |
|-------|--------------|
| tipmanager.py `_parse_date` | Interpreta hora do site como UTC → converte para BRT |
| `_format_brt_time` | Naive datetime → assume UTC → converte para BRT |
| DB `SET timezone TO 'UTC'` | asyncpg retorna naives UTC consistentes |
| `match_key` | Usa `kickoff_brt` (já convertido) |
| Mensagem editada | Usa `pred.kickoff_brt` do banco (não o horário do resultado) |

## Camadas

| Camada | Arquivo | Responsabilidade |
|--------|---------|------------------|
| **HTTP** | `main.py` | Lifespan, webhook, inclui router |
| **API** | `routes.py` | Endpoints de negócio |
| **Bot** | `telegram/handler.py` | Comandos Telegram |
| **Scheduler** | `scheduler.py` | Registro e execução de jobs |
| **Jobs** | `jobs/ebasketball.py` | Ciclo completo de palpites |
| **Prediction** | `prediction.py` | Lógica de cálculo |
| **Scraper** | `scrapers/tipmanager.py` | Coleta tipmanager.net |
| **Service** | `telegram/service.py` | Envio/edição com persistência |
| **Client** | `telegram/client.py` | Telegram Bot API (retry 429) |
| **Model** | `infra/models.py` | Entidades SQLAlchemy |
| **Config** | `infra/config.py` | Variáveis de ambiente |
| **Database** | `infra/database.py` | Engine async + session |

Dependência flui para baixo. Nunca para cima.

## Proteções

- **Deduplicação**: `match_key` único no banco — mesmo jogo nunca é postado duas vezes
- **Jogo futuro**: `update_results` ignora predictions com `kickoff_brt > agora`
- **Match por nome**: case-insensitive (`lower()`) — tolerante a capitalização do site
- **Rate limit**: retry automático em 429 com `retry_after` do Telegram
- **SSL**: injetado apenas quando `sslmode=require/verify` ou `supabase` na URL
