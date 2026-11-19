# Método

Todos os parâmetros estão em `scripts/defaults.py`.

## Coleta (`collect.py`)

- Repositórios da organização sem fork, sem arquivados e com push dentro do período.
  `--repos`/`--exclude-repos` filtram; `--max-repos N` mantém os N com mais PRs mergeados no período.
- PRs mergeados no período via busca do GitHub por data de merge (janelas divididas até ficarem
  abaixo do limite de 1.000 resultados da busca). Campos: número, título, rótulos, login e tipo do
  autor, `mergedAt`, e por arquivo: caminho, adições, remoções (paginado acima de 100 arquivos).
  Nunca conteúdo de arquivo nem diff.
- Período: os N meses de calendário completos anteriores ao mês atual (`--months`, `--until AAAA-MM`).
- Cache em `<out>/cache/`: `manifest.json` (parâmetros, repositórios, membros) e `prs/<repo>.json`.
  Rodar de novo com os mesmos parâmetros não acessa a rede. Parâmetros diferentes refazem o cache.

## Exclusões

- **PRs de bots**: autor do tipo Bot ou login que casa com `BOT_LOGIN` (dependabot, renovate,
  github-actions, `[bot]`…). PR sem autor (conta apagada) também sai.
- **PRs duplicados** (`DUPLICATE_PR`): cherry-picks, backports e sincronizações de branch
  ("sync main into epic", "Automated cherry pick of #123"). Repetem trabalho já contado no PR original.
- **Arquivos** (`EXCLUDE`): lockfiles, código vendorizado, saída de build/minificada, snapshots,
  migrations, código gerado e traduções para outros idiomas (o idioma-fonte `en` fica e conta como
  interface). Linhas e arquivo saem.
- **Binários e imagens** (`BINARY`): o arquivo conta (peso 1), as linhas não.
- **Linhas efetivas** = adições + remoções dos arquivos que sobram.

## Áreas (`classify.py`)

Regras de caminho/extensão em ordem (`AREAS`), a primeira que casa vence: `tests`, `docs`, `infra`,
`data`, servidores MCP, raízes de front-end (`webapp/`, `frontend/`, `*-front/`, `*-ui/`…), marcadores fortes de
backend (`api/`, `server/`, `trpc/`, `*-server/`…), extensões de interface (`.tsx`, `.vue`, `.css`,
`.erb`…), diretórios de interface (`components/`, `pages/`, `hooks/`, `stores/`, `i18n/`…),
extensões de backend (`.go`, `.py`, `.rb`, `.java`, `.clj`…).

**Testes unitários contam com o código que testam** (`UNIT_TESTS_FOLLOW_AREA = True`). Um arquivo que
as regras põem em `tests` é teste unitário, a menos que seja de integração/e2e (`INTEGRATION_TESTS`:
`e2e/`, `integration/`, `cypress/`, `playwright/`, `detox/`, carga/performance, `*.e2e.*`, `*.cy.*`,
`*.integration.test.*`, fixtures e helpers dessas suítes). O teste unitário vai para a área do caminho
testado: tira os diretórios de teste (`__tests__/`, `__mocks__/`, `tests/`, `spec/`, `test-utils/`…) e
o marcador do nome (`Button.test.tsx` → `Button.tsx`, `test_foo.py` → `foo.py`, `foo_test.go` → `foo.go`,
`FooTest.java` → `Foo.java`) e classifica com as regras e overrides. Árvores `tests/` no topo de um pacote
também tentam o espelho em `src/`, `lib/`, `app/` (`tests/unit/api/x.py` → `api/x.py`, `src/api/x.py`).
Se nada resolve, a área é inferida: a área dominante (em linhas) do código não-teste e não-docs alterado
no período no pacote do teste, subindo de diretório enquanto houver menos de `INFER_MIN_LINES` linhas de
evidência. O JSON conta as linhas de teste por tipo (`lines.tests`: `unit`, `unit_inferred`,
`integration`). A área `tests` do relatório se chama "Testes de integração"; um override `tests`
marca uma suíte de integração. `False` volta ao comportamento antigo (todo teste em `tests`).

O que nenhuma regra resolve vai para `ambiguous.json`, agrupado por diretório. O agente decide a
área de cada grupo pelo caminho e pelos títulos de PR e grava `overrides.json`
(`{"<repo>/<prefixo>": "<área>"}`, prefixo mais longo vence). Valores simples de área resolvem arquivos ambíguos. A forma `{"force": "interface"}` substitui uma classificação existente. O valor `"exclude"` remove o arquivo do score. Chaves com curingas combinam o glob de repositório antes da primeira barra e o glob de caminho depois dela. Exclusões automáticas têm precedência.
O peso de revisão soma até 1.000 linhas por caminho distinto e até 10.000 por grupo. Os totais brutos continuam disponíveis. JSON em diretórios de mocks e fixtures sai quando a mudança excede 10.000 linhas no PR. Esses limites usam linhas alteradas porque o cache não contém o tamanho em bytes.
Marcadores de módulo Android são identificados nos caminhos observados em todo o cache do repositório. Sem evidência de módulo, Kotlin e Java seguem as regras de caminho e extensão. Diretórios de ferramentas de IA têm uma área própria. O que continuar ambíguo conta como `other`.

## Score e custo (`report.py`)

- `score = log2(1 + linhas efetivas) + 0,5 × log2(1 + arquivos efetivos)`. PR com score 0
  (só lockfile, por exemplo) não recebe custo e não conta como atividade.
- Fatia de cada área no PR = linhas efetivas da área / linhas efetivas do PR (arquivo com 0 linhas conta 1).
- Manutenção: título ou rótulo casa com `MAINTENANCE`, exceto quando o título é tipado como trabalho
  novo em conventional commits (`feat:`, `feat(x):`, `docs:`; regex `NEW_WORK`). A parte de interface desse PR é
  "manutenção de interface"; o resto da interface é "interface nova".
- Custo: para cada pessoa e mês, o custo mensal é dividido entre os PRs mergeados por ela no mês,
  proporcional ao score. Mês sem PR carrega o custo para o próximo mês com PR dela. A pessoa é
  ativa do mês do primeiro ao mês do último PR no período.
  Custo do time = custo mensal × pessoas-mês ativas. Horas = custo × horas/mês ÷ custo mensal.
- Quem conta como time (`--authors`): `all`, `members` (membros da organização visíveis ao token)
  ou `min-prs:K` (K ou mais PRs no período). Organizações open source precisam de `members` ou
  `min-prs` para que contribuições avulsas não virem "time".

## Os quatro números

1. Fatia do tempo de engenharia em interface = custo de interface / custo do time.
2. Custo mensal de interface = custo de interface no período / meses.
3. Fatia de manutenção = custo de manutenção de interface / custo de interface.
4. Manutenção anualizada = custo de manutenção de interface / meses × 12.

## Determinismo

Tudo é ordenado (repositório, número do PR, autor) e os floats são arredondados a 4 casas no JSON.
O HTML usa a data da coleta, não a data de hoje. Mesmo cache + mesmos parâmetros → saída idêntica byte a byte.

## Base de referência e prioridades

A unidade da base é a empresa. Cada empresa tem o mesmo peso. Os quartis usam interpolação linear entre observações ordenadas. A posição do time é a fração de empresas com valor estritamente menor, tanto na base inteira quanto no grupo de porte (até 19, 20–49 ou 50+ pessoas com PR pontuado). Empates ficam fora do numerador. Sem custo de interface, a taxa de manutenção é indefinida.

O motor em `scripts/insights.py` seleciona até cinco sinais e ordena seus valores monetários. Os valores podem se sobrepor. Os limiares estão em `scripts/defaults.py`. Tendência exige inclinação e qualidade de ajuste da regressão linear sobre meses consecutivos com custo. O piso recorrente multiplica a menor fatia mensal pelo custo total. O pico usa custo mensal, não a participação percentual. A concentração compara diretórios quando apenas um repositório tem custo de interface. Custos nativos exigem evidência de plataforma no caminho. O cache não identifica telas equivalentes entre plataformas.
