"""Testes do handler de webhook."""

from telegram.handler import handle_update


async def test_handle_start(mock_telegram):
    update = {"message": {"chat": {"id": 123}, "text": "/start"}}
    await handle_update(update)
    mock_telegram.post.assert_called_once()


async def test_handle_ping(mock_telegram):
    update = {"message": {"chat": {"id": 123}, "text": "/ping"}}
    await handle_update(update)
    mock_telegram.post.assert_called_once()


async def test_handle_no_message(mock_telegram):
    await handle_update({"update_id": 1})
    mock_telegram.post.assert_not_called()


async def test_handle_unknown_command_no_response(mock_telegram):
    """Mensagem sem comando conhecido não gera resposta."""
    update = {"message": {"chat": {"id": 123}, "text": "hello"}}
    await handle_update(update)
    mock_telegram.post.assert_not_called()


async def test_handle_channel_post(mock_telegram):
    """channel_post (canal) é processado igual a message."""
    update = {"channel_post": {"chat": {"id": -100123}, "text": "/ping"}}
    await handle_update(update)
    mock_telegram.post.assert_called_once()


async def test_handle_command_with_botname(mock_telegram):
    """Comando com @botname é normalizado corretamente."""
    update = {"message": {"chat": {"id": 123}, "text": "/ping@TanaEbasketballBot"}}
    await handle_update(update)
    mock_telegram.post.assert_called_once()
