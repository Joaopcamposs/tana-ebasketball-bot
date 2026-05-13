"""Testes motor de palpites — eBasketball."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

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


class _FakeRecord:
    def __init__(self, player: str, opponent: str, pf: int, pa: int):
        self.player = player
        self.opponent = opponent
        self.points_for = pf
        self.points_against = pa
        self.kickoff_brt = datetime(2026, 1, 1, 12, 0, tzinfo=BRT)


def _make_match_record(player: str, opponent: str, pf: int, pa: int) -> _FakeRecord:
    return _FakeRecord(player, opponent, pf, pa)


def _mock_execute(records: list):
    """Mock de session.execute() que retorna scalars().all() = records."""
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = records
    return mock_result


def test_pick_over_line_basketball():
    assert _pick_over_line(107.0) == 99.5
    assert _pick_over_line(115.0) == 107.5
    assert _pick_over_line(95.0) == 90.5
    assert _pick_over_line(80.0) == 90.5


async def test_generate_prediction_returns_none_when_no_data():
    """Sem partidas no banco → não gera palpite."""
    session = AsyncMock()
    session.execute.side_effect = [_mock_execute([]), _mock_execute([])]

    result = await generate_prediction(session, _make_match())
    assert result is None


async def test_generate_prediction_returns_none_when_one_missing():
    """Um jogador sem dados → None."""
    home_records = [_make_match_record("Grellz", "Simaponika", 56, 50)]
    session = AsyncMock()
    session.execute.side_effect = [_mock_execute(home_records), _mock_execute([])]

    result = await generate_prediction(session, _make_match())
    assert result is None


async def test_generate_prediction_both_local():
    """Ambos com dados → gera palpite correto."""
    home_records = [
        _make_match_record("Grellz", "Simaponika", 56, 50),
        _make_match_record("Grellz", "Simaponika", 60, 48),
    ]
    away_records = [
        _make_match_record("Simaponika", "Grellz", 52, 54),
        _make_match_record("Simaponika", "Grellz", 50, 58),
    ]
    session = AsyncMock()
    session.execute.side_effect = [_mock_execute(home_records), _mock_execute(away_records)]

    pred = await generate_prediction(session, _make_match())

    assert isinstance(pred, PredictionResult)
    assert pred.home_avg_pf == 58.0  # (56+60)/2
    assert pred.home_avg_pa == 49.0  # (50+48)/2
    assert pred.away_avg_pf == 51.0  # (52+50)/2
    assert pred.away_avg_pa == 56.0  # (54+58)/2
    assert pred.home_matches == 2
    assert pred.away_matches == 2
    assert pred.over_line >= 90.5


async def test_generate_prediction_last_n():
    """last_n limita registros usados no cálculo."""
    home_records = [_make_match_record("Grellz", "X", pf, 50) for pf in [60, 55]]
    away_records = [_make_match_record("Simaponika", "Y", 50, 50)]
    session = AsyncMock()
    session.execute.side_effect = [_mock_execute(home_records), _mock_execute(away_records)]

    pred = await generate_prediction(session, _make_match(), last_n=2)
    assert pred is not None
    assert pred.home_matches == 2
