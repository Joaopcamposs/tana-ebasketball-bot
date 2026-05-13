# Fluxo de Operação

Bot de palpites para **eBasketball H2h 4x5min** (NBA 2K) via tipmanager.net.

## Ciclo automático (a cada 4 minutos)

1. Scrap de `tipmanager.net/pt/sports/nba2k/leagues/7/h2h-gg-league`
   - Próximos jogos (upcoming): kickoff, times, jogadores
   - Últimos 10 resultados com placar

2. Filtrar upcoming com kickoff entre (agora - 4min) e (agora + 10min)

3. Para cada jogo no filtro:
   - Checar `match_key` no banco — se existe, pular (deduplicação)
   - Buscar stats locais de ambos os jogadores em `PlayerLocalStats`
   - Com dados → gerar palpite (expected_total + over_line)
   - Sem dados → postar jogo sem palpite (`📊 Coletando dados...`)
   - Enviar mensagem no Telegram, salvar `Prediction` com `status=pending`

4. Para cada `Prediction` com `status=pending`:
   - Ignorar se `kickoff_brt > agora`
   - Buscar resultado em tipmanager por home_player + away_player
   - Se encontrado: editar mensagem com placar + ✅/❌
   - Atualizar `PlayerLocalStats` dos dois jogadores
   - Marcar `Prediction` como `status=done`

## Comandos Telegram (manual)

| Comando | Efeito |
|---------|--------|
| `/palpites` | Dispara `send_predictions` sem filtro de janela (todos os upcoming) |
| `/resultados` | Dispara `update_results` (atualiza pendentes) |
| `/stats <jogador>` | Exibe W/D/L e AVG PF/PA do jogador |

## Acumulação de dados locais

Stats são acumuladas organicamente a cada resultado finalizado:
- `matches_played`, `goals_for` (pontos marcados), `goals_against` (pontos sofridos)
- `wins`, `draws`, `losses`

Pré-carga histórica: coloque resultados em `app/results_pre_load.md` e dispare `POST /api/admin/preload-stats`.

## Cálculo do palpite

```
home_expected = (home_avg_pf + away_avg_pa) / 2
away_expected = (away_avg_pf + home_avg_pa) / 2
expected_total = home_expected + away_expected
over_line = max(90.5, round((expected_total - 7.5) × 2) / 2)
```

## Timezone

- Site tipmanager exibe horários em **UTC**
- Bot armazena e processa tudo em **UTC** internamente
- Mensagens exibem horários em **BRT (America/Sao_Paulo)**
