"""Rotas API — endpoints de negócio."""

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from jobs.ebasketball import send_predictions, simulate_e2e, update_results
from scrapers.tipmanager import fetch_all, fetch_results, fetch_upcoming
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import client as tg_client
from telegram.service import edit_by_reference, list_pending, send_and_store

from infra.config import settings
from infra.database import get_session
from infra.models import PlayerMatchResult

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


@router.get("/bot-info")
async def api_bot_info() -> dict[str, Any]:
    """Diagnóstico: info do bot e status do webhook."""
    me = await tg_client.api_call("getMe")
    webhook = await tg_client.api_call("getWebhookInfo")
    return {
        "polling_mode": settings.telegram_polling,
        "bot": me.get("result", {}),
        "webhook": webhook.get("result", {}),
    }


@router.post("/bot-webhook")
async def api_set_webhook(url: str) -> dict[str, Any]:
    """Registra webhook no Telegram. url = URL pública completa do app."""
    webhook_url = f"{url.rstrip('/')}{settings.webhook_path}"
    params: dict[str, Any] = {"url": webhook_url}
    if settings.telegram_webhook_secret:
        params["secret_token"] = settings.telegram_webhook_secret
    result = await tg_client.api_call("setWebhook", **params)
    return {"webhook_url": webhook_url, "result": result.get("result")}


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
    """Lê results_pre_load.md e insere partidas individuais em PlayerMatchResult."""
    from datetime import datetime
    from typing import cast
    from zoneinfo import ZoneInfo

    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.engine import CursorResult

    brt = ZoneInfo("America/Sao_Paulo")

    if not _PRELOAD_FILE.exists():
        raise HTTPException(404, f"Arquivo não encontrado: {_PRELOAD_FILE}")

    lines = [line.strip() for line in _PRELOAD_FILE.read_text().splitlines() if line.strip()]
    _date_re_full = re.compile(r"^(\d{2})/(\d{2})/(\d{4}),\s*(\d{2}):(\d{2})$")

    inserted = 0
    skipped = 0
    i = 0
    while i < len(lines):
        m = _date_re_full.match(lines[i])
        if not m:
            i += 1
            continue
        if i + 6 >= len(lines):
            break

        day, month, year, hour, minute = (int(g) for g in m.groups())
        kickoff = datetime(year, month, day, hour, minute, tzinfo=brt)

        home_player = lines[i + 1]
        score_line = lines[i + 3]
        away_player = lines[i + 4]
        ms = _SCORE_RE.match(score_line)
        if not ms:
            i += 1
            continue

        home_score, away_score = int(ms.group(1)), int(ms.group(2))

        for player, pf, pa, opponent in [
            (home_player, home_score, away_score, away_player),
            (away_player, away_score, home_score, home_player),
        ]:
            import uuid as _uuid

            stmt = (
                pg_insert(PlayerMatchResult)
                .values(
                    id=str(_uuid.uuid4()),
                    player=player,
                    opponent=opponent,
                    kickoff_brt=kickoff,
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

        i += 7

    await session.commit()

    return {
        "inserted": inserted,
        "skipped_duplicates": skipped,
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
