"""Handler de webhook/polling — processa updates do Telegram."""

import logging
from typing import Any

from sqlalchemy import select
from telegram import client

from infra.database import async_session
from infra.models import PlayerMatchResult

logger = logging.getLogger(__name__)


async def _cmd_palpites(chat_id: int) -> None:
    from jobs.ebasketball import send_predictions

    await client.send_message(chat_id, "⏳ Buscando palpites...")
    sent = await send_predictions(window_minutes=10)
    if not sent:
        await client.send_message(chat_id, "Nenhum palpite novo nos próximos 10 min.")
    else:
        await client.send_message(chat_id, f"✅ {len(sent)} palpite(s) enviado(s).")


async def _cmd_resultados(chat_id: int) -> None:
    from jobs.ebasketball import update_results

    await client.send_message(chat_id, "⏳ Atualizando resultados...")
    updated = await update_results()
    if not updated:
        await client.send_message(chat_id, "Nenhum resultado atualizado.")
    else:
        await client.send_message(chat_id, f"✅ {len(updated)} resultado(s) atualizado(s).")


async def _cmd_stats(chat_id: int, player: str) -> None:
    async with async_session() as session:
        stmt = (
            select(PlayerMatchResult)
            .where(PlayerMatchResult.player.ilike(f"%{player}%"))
            .order_by(PlayerMatchResult.kickoff_brt.desc())
        )
        matches = (await session.execute(stmt)).scalars().all()

    if not matches:
        await client.send_message(chat_id, f"Nenhum dado encontrado para '{player}'.")
        return

    # agrupa por jogador (busca pode retornar múltiplos nomes similares)
    from collections import defaultdict

    by_player: dict[str, list] = defaultdict(list)
    for m in matches:
        by_player[m.player].append(m)

    lines = []
    for name, records in by_player.items():
        mp = len(records)
        avg_pf = sum(r.points_for for r in records) / mp
        avg_pa = sum(r.points_against for r in records) / mp
        wins = sum(1 for r in records if r.points_for > r.points_against)
        draws = sum(1 for r in records if r.points_for == r.points_against)
        losses = mp - wins - draws
        lines.append(
            f"👤 <b>{name}</b>\n"
            f"🎮 Jogos: {mp} | W/D/L: {wins}/{draws}/{losses}\n"
            f"📊 AVG PF: {avg_pf:.1f} | PA: {avg_pa:.1f}"
        )

    await client.send_message(chat_id, "\n\n".join(lines))


async def handle_update(update: dict[str, Any]) -> None:
    """Processa um update recebido via webhook ou polling."""
    message = update.get("message") or update.get("channel_post")
    if not message:
        logger.debug("Update sem message: %s", update.get("update_id"))
        return

    chat_id = message["chat"]["id"]
    # strip @botname suffix that Telegram appends in groups/channels
    text = message.get("text", "").strip().split("@")[0]

    logger.info("Update: chat_id=%s text='%s'", chat_id, text[:60])

    if text.startswith("/palpites"):
        await _cmd_palpites(chat_id)

    elif text.startswith("/resultados"):
        await _cmd_resultados(chat_id)

    elif text.startswith("/stats"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await client.send_message(chat_id, "Uso: /stats <nome do jogador>")
        else:
            await _cmd_stats(chat_id, parts[1])

    elif text.startswith("/start"):
        await client.send_message(
            chat_id,
            "Bot ativo.\n\nComandos:\n/palpites — envia palpites\n/resultados — atualiza resultados\n/stats <jogador> — estatísticas",
        )

    elif text.startswith("/ping"):
        await client.send_message(chat_id, "pong")
