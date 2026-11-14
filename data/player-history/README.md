# Histórico de XP e mortes dos Players Ativos

O workflow `Atualizar XP e mortes de jogadores ativos` roda de hora em hora. Ele cria tarefas para os mundos que tenham personagens com `last_seen_at` nos últimos 30 dias. Antica, Celesta, Bona, Harmonia, Farsabra, Dia, Inabra, Belobra, Karmeya e Astera são divididos em quatro shards estáveis de personagens; os demais mundos usam um shard. Até 20 tarefas rodam em paralelo. Cada shard consulta todos os perfis vencidos que lhe foram atribuídos, com intervalo de um segundo entre chamadas. No mundo dividido, apenas o shard 1 coleta o ranking completo de XP; uma etapa final reconcilia os CSVs de todos os shards e publica um único commit.

## Arquivos CSV

Os dados ficam separados por mundo em `data/player-history/worlds/<mundo>/`:

- `xp-coverage.csv`: status atual de XP de cada personagem, incluindo quem não aparece no ranking.
- `experience.csv`: snapshots de XP do ranking, posição e variação em relação ao dia anterior.
- `deaths-daily.csv`: contagem por personagem e dia. Zero só é registrado quando o perfil foi consultado naquele dia e a API informou que a lista não estava truncada.
- `death-events.csv`: eventos de morte retornados pela API, deduplicados.
- `death-checks.csv`: estado e data da última consulta a cada perfil.
- `highscore-checkpoints.csv`: controla quais rankings já foram coletados no dia.

Todos os CSVs conservam somente os últimos 30 dias. A população vem de `data/players-ativos/`, mas só considera personagens cujo `last_seen_at` esteja nessa janela. A base Players Ativos continua com retenção própria de 90 dias.

## Cobertura e limites

A API v5 não informa XP no perfil individual. O XP é lido do ranking de experiência; personagens ativos fora dele aparecem em `xp-coverage.csv` como `not_listed_in_experience_highscores`, sem estimativa inventada. O ganho diário só é calculado quando existem snapshots de dias consecutivos. Os snapshots históricos começam na primeira execução bem-sucedida, pois não há XP retroativo disponível pelo perfil.

As mortes vêm do histórico de perfil. Eventos visíveis são deduplicados, e a API pode sinalizar que a lista está truncada; nesses casos, a cobertura fica marcada como parcial. Uma consulta bem-sucedida não recupera mortes anteriores que a API não fornece.

O workflow deve estar em `.github/workflows/player-history.yml` na branch padrão, com GitHub Actions habilitado. É normal a execução inicial demorar mais, pois precisa consultar todos os personagens vencidos; cada tarefa tem limite máximo de seis horas do runner e o workflow permite 20 tarefas em paralelo. Se o número de perfis exceder a duração máxima, a tarefa daquele shard falha e será tentada novamente na próxima execução. O tempo real depende da distribuição de personagens entre mundos, respostas da API, tentativas após erros e do tamanho da população ativa acumulada em 30 dias, que não é igual ao total online em um único instante.
