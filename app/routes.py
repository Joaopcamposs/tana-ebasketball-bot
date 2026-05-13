"""Rotas API — endpoints de negócio."""

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from jobs.ebasketball import send_predictions, simulate_e2e, update_results
from scrapers.tipmanager import fetch_all, fetch_results, fetch_upcoming
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram.service import edit_by_reference, list_pending, send_and_store

from infra.config import settings
from infra.database import get_session
from infra.models import PlayerLocalStats

_DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4}),\s*(\d{2}):(\d{2})$")
_SCORE_RE = re.compile(r"^(\d+)\s*:\s*(\d+)$")
_PRELOAD_FILE = Path(__file__).parent / "results_pre_load.md"


def _parse_preload(path: Path) -> list[tuple[str, int, str, int]]:
    """Retorna lista de (home_player, home_score, away_player, away_score)."""
    lines = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    results = []
    i = 0
    while i < len(lines):
        if not _DATE_RE.match(lines[i]):
            i += 1
            continue
        if i + 6 >= len(lines):
            break
        home_player = lines[i + 1]
        score_line = lines[i + 3]
        away_player = lines[i + 4]
        m = _SCORE_RE.match(score_line)
        if not m:
            i += 1
            continue
        results.append((home_player, int(m.group(1)), away_player, int(m.group(2))))
        i += 7
    return results


router = APIRouter(prefix="/api", tags=["api"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy"}


@router.post("/send")
async def api_send(
    text: str,
    reference_key: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Envia mensagem ao canal configurado."""
    record = await send_and_store(
        session,
        settings.telegram_channel_id,
        text,
        reference_key=reference_key,
    )
    return {
        "message_id": record.message_id,
        "reference_key": record.reference_key,
        "status": record.status,
    }


@router.get("/pending")
async def api_pending(
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """Lista mensagens pendentes de atualização."""
    records = await list_pending(session)
    return [
        {
            "id": r.id,
            "chat_id": r.chat_id,
            "message_id": r.message_id,
            "reference_key": r.reference_key,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in records
    ]


@router.get("/upcoming")
async def api_upcoming() -> dict[str, Any]:
    """Próximos jogos eBasketball H2H GG League."""
    matches = await fetch_upcoming()
    return {
        "count": len(matches),
        "matches": [m.to_dict() for m in matches],
    }


@router.get("/results")
async def api_results(
    player: str | None = None,
) -> dict[str, Any]:
    """Últimos 10 resultados eBasketball H2H GG League."""
    results = await fetch_results()
    if player:
        p = player.lower()
        results = [
            r for r in results if p in r.home_player.lower() or p in r.away_player.lower()
        ]
    return {
        "count": len(results),
        "results": [r.to_dict() for r in results],
    }


@router.get("/all")
async def api_all() -> dict[str, Any]:
    """Upcoming e resultados numa única requisição."""
    upcoming, results = await fetch_all()
    return {
        "upcoming_count": len(upcoming),
        "results_count": len(results),
        "upcoming": [m.to_dict() for m in upcoming],
        "results": [r.to_dict() for r in results],
    }


@router.post("/predictions/send")
async def api_send_predictions(window: int | None = None) -> dict[str, Any]:
    """
    Força envio de palpites. Sem window envia todos os upcoming;
    com window filtra por minutos.
    """
    sent = await send_predictions(window_minutes=window)
    return {
        "sent_count": len(sent),
        "predictions": sent,
    }


@router.post("/predictions/update")
async def api_update_results() -> dict[str, Any]:
    """Força atualização de resultados dos palpites pendentes."""
    updated = await update_results()
    return {
        "updated_count": len(updated),
        "results": updated,
    }


@router.post("/predictions/simulate")
async def api_simulate_e2e(limit: int = 5) -> dict[str, Any]:
    """Teste e2e: pega resultados reais, gera palpites, envia e atualiza."""
    results = await simulate_e2e(limit=limit)
    return {
        "count": len(results),
        "results": results,
    }


@router.put("/edit")
async def api_edit(
    reference_key: str,
    text: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Edita mensagem por reference_key."""
    result = await edit_by_reference(session, reference_key, text)
    if not result:
        raise HTTPException(404, "Message not found")
    return {"status": "edited"}


@router.post("/admin/preload-stats")
async def api_preload_stats(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Lê results_pre_load.md e popula PlayerLocalStats no banco."""
    if not _PRELOAD_FILE.exists():
        raise HTTPException(404, f"Arquivo não encontrado: {_PRELOAD_FILE}")

    entries = _parse_preload(_PRELOAD_FILE)
    if not entries:
        raise HTTPException(422, "Nenhum resultado encontrado no arquivo")

    players_updated: dict[str, dict] = {}

    for home_player, home_score, away_player, away_score in entries:
        for player, gf, ga in [
            (home_player, home_score, away_score),
            (away_player, away_score, home_score),
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

            players_updated[player] = {
                "matches_played": stats.matches_played,
                "avg_pf": round(stats.goals_for / stats.matches_played, 2),
                "avg_pa": round(stats.goals_against / stats.matches_played, 2),
            }

    await session.commit()

    return {
        "parsed": len(entries),
        "players": len(players_updated),
        "stats": players_updated,
    }


@router.post("/demo")
async def api_demo(
    initial_text: str = "⏳ Carregando dados...",
    final_text: str = "✅ Dados carregados com sucesso!",
    delay: int = 3,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Demo: envia mensagem, espera N segundos e edita."""
    import asyncio
    import uuid

    ref_key = f"demo-{uuid.uuid4()}"

    record = await send_and_store(
        session,
        settings.telegram_channel_id,
        initial_text,
        reference_key=ref_key,
    )

    await asyncio.sleep(min(delay, 10))

    result = await edit_by_reference(session, ref_key, final_text)

    return {
        "reference_key": ref_key,
        "message_id": record.message_id,
        "initial_text": initial_text,
        "final_text": final_text,
        "delay": delay,
        "status": "done" if result else "error",
    }
