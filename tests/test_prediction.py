"""Testes motor de palpites — eBasketball."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from prediction import PredictionResult, _pick_over_line, generate_prediction
from scrapers.tipmanager import UpcomingMatch

BRT = ZoneInfo("America/Sao_Paulo")


def _make_match() -> UpcomingMatch:
    return UpcomingMatch(
        kickoff_brt=datetime(2026, 5, 12, 14, 0, tzinfo=BRT),
        home_team="Lakers",
        home_player="Grellz",
        away_team="Celtics",
        away_player="Simaponika",
    )


def _make_local_stats(player: str, mp: int, gf: int, ga: int):
    from infra.models import PlayerLocalStats

    return PlayerLocalStats(
        player=player,
        matches_played=mp,
        goals_for=gf,
        goals_against=ga,
        wins=0,
        draws=0,
        losses=0,
    )


def test_pick_over_line_basketball():
    assert _pick_over_line(107.0) == 99.5
    assert _pick_over_line(115.0) == 107.5
    assert _pick_over_line(95.0) == 90.5
    assert _pick_over_line(80.0) == 90.5


@pytest.mark.asyncio
async def test_generate_prediction_returns_none_when_no_data():
    """Sem stats no banco → não gera palpite."""
    session = AsyncMock()
    # scalar_one_or_none → None (nenhum jogador), one() → (None, None, None)
    mock_player = MagicMock()
    mock_player.scalar_one_or_none.return_value = None
    mock_global = MagicMock()
    mock_global.one.return_value = (None, None, None)
    session.execute.side_effect = [mock_player, mock_player, mock_global]

    result = await generate_prediction(session, _make_match())
    assert result is None


@pytest.mark.asyncio
async def test_generate_prediction_both_local():
    home = _make_local_stats("Grellz", 5, 280, 250)  # 56 PF, 50 PA
    away = _make_local_stats("Simaponika", 5, 260, 270)  # 52 PF, 54 PA

    session = AsyncMock()
    results = [MagicMock(), MagicMock()]
    results[0].scalar_one_or_none.return_value = home
    results[1].scalar_one_or_none.return_value = away
    session.execute.side_effect = results

    pred = await generate_prediction(session, _make_match())

    assert isinstance(pred, PredictionResult)
    assert pred.home_avg_pf == home.avg_goals_for
    assert pred.away_avg_pf == away.avg_goals_for
    assert pred.source == "home=local,away=local"
    assert pred.over_line >= 90.5


@pytest.mark.asyncio
async def test_generate_prediction_returns_none_when_one_missing():
    """Um jogador sem dados → None (não usa fallback)."""
    home = _make_local_stats("Grellz", 5, 280, 250)

    session = AsyncMock()
    mock_home = MagicMock()
    mock_home.scalar_one_or_none.return_value = home
    mock_away = MagicMock()
    mock_away.scalar_one_or_none.return_value = None
    session.execute.side_effect = [mock_home, mock_away]

    result = await generate_prediction(session, _make_match())
    assert result is None
