"""Job principal — ciclo completo eBasketball a cada 4 minutos."""

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc

from prediction import generate_prediction
from scheduler import register
from scrapers.tipmanager import MatchResult, UpcomingMatch, fetch_all, fetch_results
from sqlalchemy import select
from telegram import client

from infra.config import settings
from infra.database import async_session
from infra.models import PlayerLocalStats, Prediction

logger = logging.getLogger(__name__)

BRT = ZoneInfo("America/Sao_Paulo")


def _format_brt_time(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(BRT).strftime("%H:%M (BRT)")


def _make_match_key(kickoff: datetime, home_player: str, away_player: str) -> str:
    kickoff_brt = kickoff.astimezone(BRT)
    return f"{kickoff_brt.strftime('%Y%m%d_%H%M')}_{home_player}_{away_player}"


def _format_prediction_message(pred, match: UpcomingMatch | None = None) -> str:
    m = pred.match if pred else match
    assert m is not None
    header = (
        "E-basketball H2h 4x5min - OVER @1.5+\n\n" if pred else "E-basketball H2h 4x5min\n\n"
    )

    base = (
        f"{header}"
        f"🎯 {m.home_player} ({m.home_team}) vs "
        f"{m.away_player} ({m.away_team})\n"
        f"🕒 {_format_brt_time(m.kickoff_brt)}\n"
    )

    if not pred:
        return base + "\n📊 Coletando dados..."

    return (
        base
        + f"🏀 Total esperado: {pred.expected_total:.1f} pts\n"
        + f"📈 Over {pred.over_line}\n\n"
        + "📝Análise:\n"
        + f"👨🏻{m.home_player}: AVG [PF: {pred.home_avg_pf:.1f} | PA: {pred.home_avg_pa:.1f}]\n"
        + f"🧔🏻{m.away_player}: AVG [PF: {pred.away_avg_pf:.1f} | PA: {pred.away_avg_pa:.1f}]\n"
        + f"Total esperado: {pred.expected_total:.1f} pts"
    )


async def send_predictions(window_minutes: int | None = 10) -> list[dict]:
    """Busca jogos próximos, gera palpites e envia no Telegram.

    window_minutes=None envia todos os upcoming sem filtro de janela.
    """
    upcoming, _ = await fetch_all()
    if window_minutes is not None:
        now_brt = datetime.now(BRT)
        since = now_brt - timedelta(minutes=4)
        cutoff = now_brt + timedelta(minutes=window_minutes)
        logger.info(
            "Filtro janela: since=%s cutoff=%s | kickoffs=%s",
            since.strftime("%H:%M"),
            cutoff.strftime("%H:%M"),
            [m.kickoff_brt.strftime("%H:%M%z") for m in upcoming],
        )
        upcoming = [m for m in upcoming if since <= m.kickoff_brt <= cutoff]
    if not upcoming:
        logger.info("Nenhum jogo upcoming encontrado")
        return []

    chat_id = settings.telegram_channel_id
    if not chat_id:
        logger.warning("TELEGRAM_CHANNEL_ID não configurado")
        return []

    sent: list[dict] = []

    async with async_session() as session:
        for match in upcoming:
            match_key = _make_match_key(
                match.kickoff_brt, match.home_player, match.away_player
            )

            existing = await session.execute(
                select(Prediction).where(Prediction.match_key == match_key)
            )
            if existing.scalar_one_or_none():
                logger.debug("Palpite já existe: %s", match_key)
                continue

            pred = await generate_prediction(session, match)
            text = _format_prediction_message(pred, match)

            try:
                result = await client.send_message(chat_id, text)
                msg_id = result["result"]["message_id"]
            except Exception:
                logger.exception("Falha ao enviar palpite: %s", match_key)
                continue

            prediction = Prediction(
                match_key=match_key,
                kickoff_brt=match.kickoff_brt,
                home_team=match.home_team,
                home_player=match.home_player,
                away_team=match.away_team,
                away_player=match.away_player,
                expected_total_goals=pred.expected_total if pred else None,
                over_line=pred.over_line if pred else None,
                message_id=msg_id,
                status="pending",
            )
            session.add(prediction)
            await session.commit()
            logger.info("Palpite salvo: %s msg_id=%d", match_key, msg_id)

            sent.append(
                {
                    "match_key": match_key,
                    "home_player": match.home_player,
                    "away_player": match.away_player,
                    "expected_total": pred.expected_total if pred else None,
                    "over_line": pred.over_line if pred else None,
                    "message_id": msg_id,
                }
            )

    return sent


async def update_results() -> list[dict]:
    """Consulta resultados finalizados e atualiza palpites pendentes."""
    results = await fetch_results()
    if not results:
        return []

    updated: list[dict] = []

    async with async_session() as session:
        stmt = select(Prediction).where(Prediction.status == "pending")
        pending = (await session.execute(stmt)).scalars().all()
        if not pending:
            return []

        now_brt = datetime.now(BRT)

        for pred in pending:
            kickoff = pred.kickoff_brt
            if kickoff and kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=UTC)
            if kickoff and kickoff.astimezone(BRT) > now_brt:
                logger.debug("Prediction %s ainda não iniciou, ignorando", pred.match_key)
                continue

            matched: MatchResult | None = None
            for r in results:
                if (
                    r.home_player.lower() == pred.home_player.lower()
                    and r.away_player.lower() == pred.away_player.lower()
                ):
                    matched = r
                    break

            if not matched:
                continue

            pred.home_goals = matched.home_score
            pred.away_goals = matched.away_score
            total = matched.total_score
            if pred.over_line is not None:
                pred.success = total > pred.over_line
            pred.status = "done"

            has_prediction = pred.over_line is not None
            icon = ("✅" if pred.success else "❌") if has_prediction else ""

            result_line = (
                f"Resultado: {matched.home_score} - {matched.away_score} (total: {total})\n\n"
            )
            pred_line = (
                (
                    f"🏀 Total esperado: {pred.expected_total_goals:.1f} pts\n"
                    f"📈 Over {pred.over_line}\n"
                )
                if has_prediction
                else ""
            )

            try:
                await client.api_call(
                    "editMessageText",
                    chat_id=settings.telegram_channel_id,
                    message_id=pred.message_id,
                    parse_mode="HTML",
                    text=(
                        f"E-basketball H2h 4x5min\n\n"
                        f"🎯 {pred.home_player} ({pred.home_team}) vs "
                        f"{pred.away_player} ({pred.away_team})\n"
                        f"🕒 {_format_brt_time(pred.kickoff_brt)}\n"
                        + pred_line
                        + f"\n{result_line}"
                        + icon
                    ),
                )
            except Exception:
                logger.exception("Falha ao editar mensagem pred=%s", pred.id)

            await _update_local_stats(session, matched)
            logger.info(
                "Resultado atualizado: %s %d-%d %s",
                pred.match_key,
                matched.home_score,
                matched.away_score,
                icon,
            )

            updated.append(
                {
                    "match_key": pred.match_key,
                    "home_score": matched.home_score,
                    "away_score": matched.away_score,
                    "total_score": total,
                    "over_line": pred.over_line,
                    "success": pred.success,
                }
            )

        await session.commit()

    return updated


async def _update_local_stats(session, result: MatchResult) -> None:
    """Atualiza estatísticas locais dos dois jogadores após resultado."""
    for player, gf, ga in [
        (result.home_player, result.home_score, result.away_score),
        (result.away_player, result.away_score, result.home_score),
    ]:
        stmt = select(PlayerLocalStats).where(PlayerLocalStats.player == player)
        stats = (await session.execute(stmt)).scalar_one_or_none()

        if not stats:
            stats = PlayerLocalStats(
                player=player,
                matches_played=0,
                goals_for=0,
                goals_against=0,
                wins=0,
                draws=0,
                losses=0,
            )
            session.add(stats)

        stats.matches_played += 1
        stats.goals_for += gf
        stats.goals_against += ga

        if gf > ga:
            stats.wins += 1
        elif gf == ga:
            stats.draws += 1
        else:
            stats.losses += 1


async def simulate_e2e(limit: int = 5) -> list[dict]:
    """Teste e2e: pega resultados reais, gera palpites, envia e atualiza com resultado."""
    results = await fetch_results()
    if not results:
        return []

    chat_id = settings.telegram_channel_id
    if not chat_id:
        return []

    results = results[:limit]
    output: list[dict] = []

    async with async_session() as session:
        for r in results:
            match = UpcomingMatch(
                kickoff_brt=r.kickoff_brt,
                home_team=r.home_team,
                home_player=r.home_player,
                away_team=r.away_team,
                away_player=r.away_player,
            )

            match_key = _make_match_key(r.kickoff_brt, r.home_player, r.away_player)

            existing = await session.execute(
                select(Prediction).where(Prediction.match_key == match_key)
            )
            if existing.scalar_one_or_none():
                continue

            pred = await generate_prediction(session, match)
            if pred is None:
                logger.info("Simulate e2e: sem dados para %s", match_key)
                continue
            text = _format_prediction_message(pred)

            try:
                msg_result = await client.send_message(chat_id, text)
                msg_id = msg_result["result"]["message_id"]
            except Exception:
                logger.exception("Simulate e2e: falha envio %s", match_key)
                continue

            prediction = Prediction(
                match_key=match_key,
                kickoff_brt=r.kickoff_brt,
                home_team=r.home_team,
                home_player=r.home_player,
                away_team=r.away_team,
                away_player=r.away_player,
                expected_total_goals=pred.expected_total,
                over_line=pred.over_line,
                message_id=msg_id,
                status="pending",
            )
            session.add(prediction)
            await session.commit()

            total = r.total_score
            prediction.home_goals = r.home_score
            prediction.away_goals = r.away_score
            prediction.success = total > pred.over_line
            prediction.status = "done"

            icon = "✅" if prediction.success else "❌"

            try:
                await client.api_call(
                    "editMessageText",
                    chat_id=chat_id,
                    message_id=msg_id,
                    parse_mode="HTML",
                    text=(
                        f"E-basketball H2h 4x5min - OVER @1.5+\n\n"
                        f"🎯 {r.home_player} ({r.home_team}) vs "
                        f"{r.away_player} ({r.away_team})\n"
                        f"🏀 Total esperado: {pred.expected_total:.1f} pts\n"
                        f"📈 Over {pred.over_line}\n"
                        f"🕒 {_format_brt_time(r.kickoff_brt)}\n\n"
                        f"Resultado: {r.home_score} - {r.away_score} "
                        f"(total: {total})\n\n"
                        f"{icon}"
                    ),
                )
            except Exception:
                logger.exception("Simulate e2e: falha edição %s", match_key)

            await _update_local_stats(session, r)
            await session.commit()

            output.append(
                {
                    "match_key": match_key,
                    "home_player": r.home_player,
                    "away_player": r.away_player,
                    "expected_total": pred.expected_total,
                    "over_line": pred.over_line,
                    "result": f"{r.home_score}-{r.away_score}",
                    "total_score": total,
                    "success": prediction.success,
                }
            )

            logger.info(
                "Simulate e2e: %s → %d-%d %s",
                match_key,
                r.home_score,
                r.away_score,
                icon,
            )

    return output


@register("ebasketball-battle", interval_seconds=240)
async def ebasketball_cycle():
    """Ciclo completo: gerar palpites + atualizar resultados."""
    logger.info("Iniciando ciclo eBasketball Battle")
    await send_predictions()
    await update_results()
    logger.info("Ciclo eBasketball Battle concluído")
