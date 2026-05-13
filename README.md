# eBasketball H2h 4x5min Bot

Bot automatizado para palpites de **eBasketball H2h 4x5min** (NBA 2K). Executa a cada 4 minutos: coleta jogos próximos, gera previsão de pontos cruzando dados locais, envia palpite no Telegram e atualiza com resultado final (✅/❌).

## Stack

- **Python 3.14** / **uv** / **ruff**
- **FastAPI** com lifespan
- **SQLAlchemy 2.0** async com asyncpg (Postgres)
- **httpx** + **BeautifulSoup + lxml** para scraping
- **Docker** multi-stage com limite 256MB RAM
- **Scheduler** interno (asyncio) para rotinas periódicas

## Quick Start

```bash
# 1. Instalar dependências
make install

# 2. Copiar e configurar variáveis
cp .env.example .env
# Preencha: TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, DATABASE_URL

# 3. Subir banco
docker compose up postgres -d

# 4. Rodar app
make dev

# 5. Testar scraping
curl "http://localhost:8012/api/upcoming"
curl "http://localhost:8012/api/results"
curl "http://localhost:8012/api/all"
```

## Como Funciona

```
┌─────────────────────────────────────────────────────────────────┐
│                    Ciclo a cada 4 minutos                       │
│                                                                 │
│  1. Scrap tipmanager.net ──→ próximos jogos + resultados       │
│  2. Filtro janela: kickoff nos próximos 10 min                 │
│  3. Motor de palpites ──→ expected_total + over_line           │
│     └→ sem dados: posta jogo sem palpite, acumula stats        │
│  4. Envia no Telegram ──→ salva Prediction (match_key único)   │
│  5. Consulta resultados ──→ edita mensagem com ✅/❌            │
│  6. Atualiza PlayerLocalStats ──→ alimenta dados locais        │
└─────────────────────────────────────────────────────────────────┘
```

### Fonte de dados

| Fonte | URL | Dados |
|-------|-----|-------|
| **tipmanager** | `tipmanager.net/pt/sports/nba2k/leagues/7/h2h-gg-league` | Próximos jogos + últimos 10 resultados com placar |
| **banco local** | `player_local_stats` | Stats acumuladas por jogador (atualizado a cada resultado) |

### Motor de palpites

Requer dados locais de ambos os jogadores. Se faltarem, posta o jogo sem palpite e acumula o resultado no banco quando finalizar.

```
home_expected = (home_avg_pf + away_avg_pa) / 2
away_expected = (away_avg_pf + home_avg_pa) / 2
expected_total = home_expected + away_expected
over_line = max(90.5, round((expected_total - 7.5) × 2) / 2)
```

### Mensagem no Telegram

Antes do resultado:
```
E-basketball H2h 4x5min - OVER @1.5+

🎯 Grellz (France) vs Simaponika (Germany)
🕒 14:00 (BRT)
🏀 Total esperado: 107.4 pts
📈 Over 99.5

📝Análise:
👨🏻 Grellz: AVG [PF: 56.2 | PA: 48.3]
🧔🏻 Simaponika: AVG [PF: 53.1 | PA: 49.8]
Total esperado: 107.4 pts
```

Após resultado:
```
E-basketball H2h 4x5min

🎯 Grellz (France) vs Simaponika (Germany)
🕒 14:00 (BRT)
🏀 Total esperado: 107.4 pts
📈 Over 99.5

Resultado: 58 - 51 (total: 109)

✅
```

### Comandos do Bot no Telegram

| Comando | Ação |
|---------|------|
| `/palpites` | Envia palpites de todos os jogos upcoming |
| `/resultados` | Atualiza predictions pendentes com resultado |
| `/stats <jogador>` | Estatísticas acumuladas do jogador |
| `/start` | Lista de comandos |
| `/ping` | Health check |

## Estrutura

```
app/
  main.py                  → Entry point, lifespan, webhook
  routes.py                → Endpoints API (/api/*)
  scheduler.py             → Scheduler asyncio para jobs periódicos
  prediction.py            → Motor de palpites (dados locais)
  scrapers/
    tipmanager.py          → Scrap próximos jogos + resultados
  jobs/
    ebasketball.py         → Job principal (ciclo completo a cada 4min)
  infra/
    config.py              → Settings via pydantic-settings (.env)
    database.py            → Engine async + session factory (NullPool em cloud)
    models.py              → SentMessage, PlayerLocalStats, Prediction
  telegram/
    client.py              → Cliente HTTP → Telegram API (retry 429)
    handler.py             → Comandos do bot (/palpites /resultados /stats)
    polling.py             → Long polling para dev local
    service.py             → Envio/edição com persistência e status
tests/                     → Testes unitários (SQLite em memória)
```

## Endpoints

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET` | `/api/health` | Health check |
| `GET` | `/api/upcoming` | Próximos jogos scrapeados |
| `GET` | `/api/results?player=nome` | Últimos 10 resultados |
| `GET` | `/api/all` | Upcoming + resultados numa requisição |
| `POST` | `/api/predictions/send?window=N` | Força envio de palpites (sem `window` = todos) |
| `POST` | `/api/predictions/update` | Força atualização de resultados pendentes |
| `POST` | `/api/predictions/simulate?limit=5` | Teste e2e com resultados reais |
| `POST` | `/api/admin/preload-stats` | Popula PlayerLocalStats a partir de `results_pre_load.md` |
| `POST` | `/api/send` | Envia mensagem ao canal |
| `PUT` | `/api/edit` | Edita mensagem por `reference_key` |
| `GET` | `/api/pending` | Mensagens pendentes |
| `POST` | `/webhook/telegram` | Webhook Telegram (interno) |

## Banco de Dados

### PlayerLocalStats

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `player` | str (PK) | Nome do jogador |
| `matches_played` | int | Total de partidas |
| `goals_for` | int | Pontos marcados |
| `goals_against` | int | Pontos sofridos |
| `wins/draws/losses` | int | Resultados |
| `avg_goals_for` | property | `goals_for / matches_played` |
| `avg_goals_against` | property | `goals_against / matches_played` |

### Prediction

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `id` | UUID7 | PK |
| `match_key` | str (unique) | `YYYYMMDD_HHMM_player1_player2` |
| `kickoff_brt` | datetime | Horário do jogo (UTC no DB, exibido em BRT) |
| `home/away_team` | str | Seleções |
| `home/away_player` | str | Jogadores |
| `expected_total_goals` | float\|null | Previsão de pontos totais (null = sem dados) |
| `over_line` | float\|null | Linha over recomendada (null = sem dados) |
| `message_id` | bigint | ID da mensagem no Telegram |
| `status` | str | `pending` → `done` |
| `home/away_goals` | int | Placar real |
| `success` | bool\|null | `total > over_line` (null = sem palpite) |

## Variáveis de Ambiente

| Variável | Obrigatória | Descrição |
|----------|-------------|-----------|
| `TELEGRAM_BOT_TOKEN` | Sim | Token do @BotFather |
| `TELEGRAM_CHANNEL_ID` | Sim | ID do canal (ex: `-1001234567890`) |
| `DATABASE_URL` | Sim | `postgresql+asyncpg://user:pass@host/db` |
| `TELEGRAM_WEBHOOK_SECRET` | Não | Secret para validar webhook |
| `TELEGRAM_POLLING` | Não | `true` = polling (dev), `false` = webhook (prod) |
| `LOG_LEVEL` | Não | `INFO` (default) |
| `DB_SCHEMA` | Não | Schema PostgreSQL — default `ebasketball_bot` |

## Comandos Make

| Comando | Descrição |
|---------|-----------|
| `make install` | Instala dependências via uv |
| `make dev` | Servidor local com reload (:8012) |
| `make run` | Servidor produção local (:8012) |
| `make test` | Testes com coverage |
| `make lint` | Ruff check |
| `make format` | Auto-format com ruff |
| `make up` | Docker compose up |
| `make down` | Docker compose down |
| `make clean` | Remove volumes e cache |
| `make resetdb` | Recria banco do zero |

## Pré-carga de dados históricos

Coloque resultados passados em `app/results_pre_load.md` no formato:

```
DD/MM/YYYY, HH:MM
Home Player
Home Team
HH : AA
Away Player
Away Team
H2H GG League
```

Depois dispare `POST /api/admin/preload-stats` para popular `PlayerLocalStats` sem criar predictions.
