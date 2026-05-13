"""Carrega resultados históricos do results_pre_load.md no PlayerLocalStats."""

import asyncio
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Adiciona app/ ao path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from sqlalchemy import select

from infra.database import async_session
from infra.models import PlayerLocalStats

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

    @property
    def total_score(self) -> int:
        return self.home_score + self.away_score


def parse_file(path: Path) -> list[RawResult]:
    lines = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    results: list[RawResult] = []
    i = 0
    while i < len(lines):
        # Tenta casar data
        if not _DATE_RE.match(lines[i]):
            i += 1
            continue

        m_date = _DATE_RE.match(lines[i])
        if not m_date:
            i += 1
            continue
        day, month, year, hour, minute = (int(g) for g in m_date.groups())
        kickoff = datetime(year, month, day, hour, minute, tzinfo=BRT)

        # Precisa de pelo menos 6 linhas após a data
        if i + 6 >= len(lines):
            break

        home_player = lines[i + 1]
        home_team = lines[i + 2]
        score_line = lines[i + 3]
        away_player = lines[i + 4]
        away_team = lines[i + 5]
        # linha i+6 é "H2H GG League" ou similar — ignorada

        m_score = _SCORE_RE.match(score_line)
        if not m_score:
            i += 1
            continue

        home_score = int(m_score.group(1))
        away_score = int(m_score.group(2))

        results.append(
            RawResult(
                kickoff=kickoff,
                home_player=home_player,
                home_team=home_team,
                home_score=home_score,
                away_player=away_player,
                away_team=away_team,
                away_score=away_score,
            )
        )
        i += 7  # avança bloco completo

    return results


async def update_stats(results: list[RawResult]) -> None:
    async with async_session() as session:
        for r in results:
            for player, gf, ga in [
                (r.home_player, r.home_score, r.away_score),
                (r.away_player, r.away_score, r.home_score),
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

        await session.commit()


async def main() -> None:
    md_path = Path(__file__).parent.parent / "results_pre_load.md"
    if not md_path.exists():
        print(f"Arquivo não encontrado: {md_path}")
        sys.exit(1)

    results = parse_file(md_path)
    print(f"Parsed: {len(results)} resultados")

    if not results:
        print("Nenhum resultado encontrado.")
        sys.exit(1)

    # Preview
    players: dict[str, dict] = {}
    for r in results:
        for player, gf, ga in [
            (r.home_player, r.home_score, r.away_score),
            (r.away_player, r.away_score, r.home_score),
        ]:
            if player not in players:
                players[player] = {"mp": 0, "gf": 0, "ga": 0}
            players[player]["mp"] += 1
            players[player]["gf"] += gf
            players[player]["ga"] += ga

    print(f"\nJogadores encontrados ({len(players)}):")
    for p, s in sorted(players.items()):
        avg_gf = s["gf"] / s["mp"]
        avg_ga = s["ga"] / s["mp"]
        print(f"  {p}: {s['mp']} jogos | GF avg {avg_gf:.2f} | GA avg {avg_ga:.2f}")

    await update_stats(results)
    print("\nStats salvos no banco com sucesso.")


if __name__ == "__main__":
    asyncio.run(main())
