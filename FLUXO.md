# Como funciona

O bot é executado a cada 4 minutos.

- Scrap de proximos jogos e resultados em: https://tipmanager.net/pt/sports/nba2k/leagues/7/h2h-gg-league
    - Resultados dos ultimos jogos com pontuacao
- Gerar palpite sobre o jogo:
    - caso tenha mais de 20 jogos de amostragem do jogador, use os dados consolidados nesse range
    - caso tenha menos de 20 jogos de amostragem do jogador, use os dados consolidados externos no range completo
- Enviar palpite e salvar no banco junto ao id da mensagem retornado
- Atualizar palpites de mensagens anteriores de acordo com o resultado finalizado.
- Atualizar palpite salvo como editado/concluido e o resultado. Salvar gols ocorridos e uma flag de sucesso ou erro.
- Sempre evite deduplicação de palpites

Precisamos de uma tabela local de estatisticas dos jogadores. Assim que um resultado for finalizado, atualizar a tabela local com as estatisticas do jogador.
Cruze o maximo de dados possiveis para gerar um palpite assertivo
