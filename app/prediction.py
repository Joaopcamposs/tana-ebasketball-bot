"""Motor de palpites — usa partidas individuais para gerar previsão de pontos (eBasketball)."""

import logging
from dataclasses import dataclass

from scrapers.tipmanager import UpcomingMatch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infra.models import PlayerMatchResult

logger = logging.getLogger(__name__)


@dataclass
class PredictionResult:
    match: UpcomingMatch
    expected_total: float
    over_line: float
    home_avg_pf: float
    home_avg_pa: float
    away_avg_pf: float
    away_avg_pa: float
    home_matches: int
    away_matches: int


async def _get_player_matches(
    session: AsyncSession,
    player: str,
    limit: int | None = None,
) -> list[PlayerMatchResult]:
    stmt = (
        select(PlayerMatchResult)
        .where(PlayerMatchResult.player == player)
        .order_by(PlayerMatchResult.kickoff_brt.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return list((await session.execute(stmt)).scalars().all())


def _pick_over_line(expected: float) -> float:
    """Linha 7.5 pontos abaixo do esperado, arredondada em .5, mínimo 90.5."""
    raw = expected - 7.5
    line = round(raw * 2) / 2
    return max(90.5, line)


async def generate_prediction(
    session: AsyncSession,
    match: UpcomingMatch,
    last_n: int | None = None,
) -> PredictionResult | None:
    """
    Gera palpite cruzando PF/PA dos jogadores a partir de partidas individuais.
    Retorna None se não houver dados de algum dos jogadores.
    last_n limita às últimas N partidas de cada jogador.
    """
    home_matches = await _get_player_matches(session, match.home_player, limit=last_n)
    away_matches = await _get_player_matches(session, match.away_player, limit=last_n)

    if not home_matches or not away_matches:
        logger.info(
            "Sem dados para palpite: %s (%d) vs %s (%d)",
            match.home_player,
            len(home_matches),
            match.away_player,
            len(away_matches),
        )
        return None

    home_avg_pf = sum(m.points_for for m in home_matches) / len(home_matches)
    home_avg_pa = sum(m.points_against for m in home_matches) / len(home_matches)
    away_avg_pf = sum(m.points_for for m in away_matches) / len(away_matches)
    away_avg_pa = sum(m.points_against for m in away_matches) / len(away_matches)

    home_expected = (home_avg_pf + away_avg_pa) / 2
    away_expected = (away_avg_pf + home_avg_pa) / 2
    expected_total = home_expected + away_expected
    over_line = _pick_over_line(expected_total)

    logger.info(
        "Palpite: %s (%d jogos) vs %s (%d jogos) → %.1f pts esperados, Over %.1f",
        match.home_player,
        len(home_matches),
        match.away_player,
        len(away_matches),
        expected_total,
        over_line,
    )

    return PredictionResult(
        match=match,
        expected_total=expected_total,
        over_line=over_line,
        home_avg_pf=home_avg_pf,
        home_avg_pa=home_avg_pa,
        away_avg_pf=away_avg_pf,
        away_avg_pa=away_avg_pa,
        home_matches=len(home_matches),
        away_matches=len(away_matches),
    )
