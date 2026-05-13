"""Carrega resultados históricos do results_pre_load.md em PlayerMatchResult."""

import asyncio
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from typing import cast

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult

from infra.database import async_session
from infra.models import PlayerMatchResult

BRT = ZoneInfo("America/Sao_Paulo")
_DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4}),\s*(\d{2}):(\d{2})$")
_SCORE_RE = re.compile(r"^(\d+)\s*:\s*(\d+)$")


@dataclass
class RawResult:
    kickoff: datetime
    home_player: str
    home_team: str
    home_score: int
    away_player: str
    away_team: str
    away_score: int


def parse_file(path: Path) -> list[RawResult]:
    lines = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    results: list[RawResult] = []
    i = 0
    while i < len(lines):
        m_date = _DATE_RE.match(lines[i])
        if not m_date:
            i += 1
            continue

        day, month, year, hour, minute = (int(g) for g in m_date.groups())
        kickoff = datetime(year, month, day, hour, minute, tzinfo=BRT)

        if i + 6 >= len(lines):
            break

        home_player = lines[i + 1]
        home_team = lines[i + 2]
        score_line = lines[i + 3]
        away_player = lines[i + 4]
        away_team = lines[i + 5]

        m_score = _SCORE_RE.match(score_line)
        if not m_score:
            i += 1
            continue

        results.append(
            RawResult(
                kickoff=kickoff,
                home_player=home_player,
                home_team=home_team,
                home_score=int(m_score.group(1)),
                away_player=away_player,
                away_team=away_team,
                away_score=int(m_score.group(2)),
            )
        )
        i += 7

    return results


async def insert_matches(results: list[RawResult]) -> tuple[int, int]:
    inserted = skipped = 0
    async with async_session() as session:
        for r in results:
            for player, pf, pa, opponent in [
                (r.home_player, r.home_score, r.away_score, r.away_player),
                (r.away_player, r.away_score, r.home_score, r.home_player),
            ]:
                stmt = (
                    pg_insert(PlayerMatchResult)
                    .values(
                        id=str(uuid.uuid4()),
                        player=player,
                        opponent=opponent,
                        kickoff_brt=r.kickoff,
                        points_for=pf,
                        points_against=pa,
                    )
                    .on_conflict_do_nothing(constraint="uq_player_kickoff")
                )
                cursor = cast(CursorResult, await session.execute(stmt))
                if cursor.rowcount:
                    inserted += 1
                else:
                    skipped += 1

        await session.commit()
    return inserted, skipped


async def main() -> None:
    md_path = Path(__file__).parent.parent / "results_pre_load.md"
    if not md_path.exists():
        print(f"Arquivo não encontrado: {md_path}")
        sys.exit(1)

    results = parse_file(md_path)
    print(f"Parsed: {len(results)} partidas")

    if not results:
        print("Nenhum resultado encontrado.")
        sys.exit(1)

    # preview
    players: dict[str, dict] = {}
    for r in results:
        for player, pf, pa in [
            (r.home_player, r.home_score, r.away_score),
            (r.away_player, r.away_score, r.home_score),
        ]:
            if player not in players:
                players[player] = {"mp": 0, "pf": 0, "pa": 0}
            players[player]["mp"] += 1
            players[player]["pf"] += pf
            players[player]["pa"] += pa

    print(f"\nJogadores encontrados ({len(players)}):")
    for p, s in sorted(players.items()):
        print(
            f"  {p}: {s['mp']} jogos | PF avg {s['pf'] / s['mp']:.2f} | PA avg {s['pa'] / s['mp']:.2f}"
        )

    inserted, skipped = await insert_matches(results)
    print(f"\nInseridos: {inserted} | Duplicatas ignoradas: {skipped}")


if __name__ == "__main__":
    asyncio.run(main())
