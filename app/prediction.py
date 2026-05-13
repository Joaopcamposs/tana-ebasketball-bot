"""Motor de palpites — usa stats locais para gerar previsão de pontos (eBasketball)."""

import logging
from dataclasses import dataclass

from scrapers.tipmanager import UpcomingMatch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infra.models import PlayerLocalStats

logger = logging.getLogger(__name__)

MIN_LOCAL_MATCHES = 0


@dataclass
class PredictionResult:
    match: UpcomingMatch
    expected_total: float
    over_line: float
    home_avg_pf: float
    home_avg_pa: float
    away_avg_pf: float
    away_avg_pa: float
    source: str


async def _get_local_stats(session: AsyncSession, player: str) -> PlayerLocalStats | None:
    stmt = select(PlayerLocalStats).where(PlayerLocalStats.player == player)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def _pick_over_line(expected: float) -> float:
    """Linha 7.5 pontos abaixo do esperado, arredondada em .5, mínimo 90.5."""
    raw = expected - 7.5
    line = round(raw * 2) / 2
    return max(90.5, line)


async def generate_prediction(
    session: AsyncSession,
    match: UpcomingMatch,
) -> PredictionResult | None:
    """
    Gera palpite cruzando PF/PA dos jogadores.
    Retorna None se não houver dados suficientes no banco.
    """
    home_local = await _get_local_stats(session, match.home_player)
    away_local = await _get_local_stats(session, match.away_player)

    has_home = home_local and home_local.matches_played > MIN_LOCAL_MATCHES
    has_away = away_local and away_local.matches_played > MIN_LOCAL_MATCHES

    if not has_home or not has_away:
        logger.info(
            "Sem dados para palpite: %s vs %s",
            match.home_player,
            match.away_player,
        )
        return None

    home_avg_pf = home_local.avg_goals_for
    home_avg_pa = home_local.avg_goals_against
    away_avg_pf = away_local.avg_goals_for
    away_avg_pa = away_local.avg_goals_against
    source_home = source_away = "local"

    home_expected = (home_avg_pf + away_avg_pa) / 2
    away_expected = (away_avg_pf + home_avg_pa) / 2
    expected_total = home_expected + away_expected

    over_line = _pick_over_line(expected_total)
    source = f"home={source_home},away={source_away}"

    logger.info(
        "Palpite: %s vs %s → %.1f pts esperados, Over %.1f (%s)",
        match.home_player,
        match.away_player,
        expected_total,
        over_line,
        source,
    )

    return PredictionResult(
        match=match,
        expected_total=expected_total,
        over_line=over_line,
        home_avg_pf=home_avg_pf,
        home_avg_pa=home_avg_pa,
        away_avg_pf=away_avg_pf,
        away_avg_pa=away_avg_pa,
        source=source,
    )
