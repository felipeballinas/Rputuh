# Histórico de XP e mortes dos Players Ativos

O workflow `Atualizar XP e mortes de jogadores ativos` roda a cada hora e usa como população a base `data/players-ativos/`, filtrando `last_seen_at` nos 90 dias anteriores.

## Arquivos CSV

- `xp-coverage.csv`: uma linha atual por personagem ativo, indicando se ele apareceu no ranking de experiência.
- `experience/<mundo>.csv`: snapshots diários de XP dos personagens ativos encontrados no ranking, com XP total, posição e variação em relação ao dia anterior. Mantém 90 dias.
- `deaths-daily.csv`: contagem por personagem e dia. Registra zero somente quando o perfil foi consultado naquele dia e a API informou que a lista de mortes não estava truncada.
- `death-events.csv`: eventos de morte retornados pela API, deduplicados e mantidos por 90 dias.
- `death-checks/<mundo>.csv`: estado e data da última consulta ao perfil de cada personagem ativo.
- `highscore-checkpoints/<mundo>.csv`: controla quais rankings diários já foram coletados.

## Cobertura e limites

A API v5 não informa XP no perfil individual. O XP é lido do ranking de experiência; por isso, personagens ativos fora do ranking aparecem em `xp-coverage.csv` como `not_listed_in_experience_highscores`, sem um valor de XP inventado. O ganho diário só é calculado quando há snapshots em dias consecutivos.

As consultas de perfil para mortes são feitas em lotes de até 100 por hora, para distribuir as requisições. A primeira varredura do cadastro pode levar vários ciclos; a coluna `last_checked_at` mostra a cobertura. A API também informa quando a lista de mortes está truncada, e esse caso fica marcado como parcial. Os dados históricos de XP começam na primeira execução bem-sucedida; não há snapshots retroativos de XP.

As datas dos CSVs usam o dia local de São Paulo. O workflow precisa estar em `.github/workflows/player-history.yml` na branch padrão, com GitHub Actions habilitado e permissão de escrita de conteúdo.
