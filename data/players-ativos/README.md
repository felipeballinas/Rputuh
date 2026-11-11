# Banco Players Ativos

Este banco guarda em CSV os personagens que a automação observou online nos últimos 90 dias. O arquivo é separado por mundo, por exemplo `antica.csv`.

O workflow `Atualizar banco Players Ativos` roda uma vez por hora. Em cada execução, consulta a lista de jogadores online dos mundos regulares, registra nomes novos e atualiza nível, vocação e última aparição. Personagens cuja última aparição ocorreu há mais de 90 dias são removidos.

As colunas guardam nome, mundo, nível, vocação, guild e cargo, residência, último login, pontos de conquista, sexo, status da conta, casa, cônjuge e datas de primeira/última aparição. A automação enriquece até 50 fichas por hora e as atualiza semanalmente. Campos que a API não retornar ficam vazios; no perfil consultado durante a implementação, casa e cônjuge não foram fornecidos.

Na primeira carga, a lista de jogadores online é descoberta na primeira execução. O preenchimento das fichas completas acontece gradualmente, em lotes de até 50 por hora. `last_seen_at` avança na primeira verificação em que o personagem aparece online em cada dia UTC, para evitar regravar todas as linhas em cada execução. O banco contém personagens que forem observados online; sessões que começarem e terminarem entre duas verificações horárias podem não ser detectadas.

Para ativar no GitHub, publique `.github/workflows/active-characters.yml`, `scripts/collect_active_characters.py` e esta pasta `data/players-ativos/` na branch padrão. O repositório precisa permitir GitHub Actions com escrita de conteúdo. Os CSVs só são commitados quando houver alteração.
