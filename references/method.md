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
- **Automação feita por conta humana** (`AUTOMATION_TITLE`): "New Crowdin updates", "Translations update",
  "Sync Strings", "chore(deps): update…", "Update dependencies", "Automatic merge back…". Mesmo tratamento de
  PR de bot; o JSON conta esses PRs em `counts.prs_automation`.
- **PRs duplicados** (`DUPLICATE_PR`): cherry-picks, backports e sincronizações de branch
  ("sync main into epic", "Automated cherry pick of #123"). Repetem trabalho já contado no PR original.
- **Cópias de release e merge** (`RELEASE_TITLE`): PR com título de versão, release, merge ou uplift ("v1.2.3",
  "Release 2.57", "merge release-8.8", "uplift(beta): …") **e** com pelo menos `RELEASE_MIN_FILES` (3) arquivos, dos
  quais pelo menos 80% (`RELEASE_MIN_OVERLAP`) também foram alterados por outro PR do mesmo repositório no período.
  A condição de sobreposição mantém releases que trazem trabalho próprio. O prefixo "release-26.1: <título>"
  (`RELEASE_COPY`) é sempre cópia. O rótulo `backport` não conta: em vários projetos ele marca o PR original. O
  relatório mostra no método quantos PRs saíram assim (`counts.prs_release_copy`, contado dentro de `prs_duplicate`).
- **Arquivos** (`EXCLUDE`): lockfiles, código vendorizado (inclui `thirdparty/`, `3rdparty/`), saída de
  build/minificada, snapshots e gravações (`cassettes/`, `.har`, `__golden__/`), migrations, código gerado
  (inclui `Package.resolved`, `next-env.d.ts`, `.tsbuildinfo`, declarações de CSS modules `*.module.scss.d.ts`, `llms.txt`, sitemap, esquemas Room) e traduções
  para outros idiomas. Linhas e arquivo saem.
- **Traduções**: pastas de idioma (`locale/`, `locales/`, `i18n/`, `lang/`, `languages/`, `localization/`…) com
  códigos de uma a três partes (`de`, `pt_BR`, `es-419`, `zh-Hans-CN`), idioma no nome do arquivo dentro dessas
  pastas (`client.pt_BR.yml`, `app_zh.arb`), Apple `xx.lproj/`, Android `res/values-xx[-rYY]/strings*.xml` e
  `values-b+…`, extensões Chrome `_locales/xx/`, `.xtb`, `.mo` e catálogos Qt `translations/*_xx.ts` (XML, não
  TypeScript; reconhecidos pelo caminho e pelo código de idioma). Só o idioma-fonte fica e conta como interface:
  `en`, `en-US`, `Base`, `source`. Variantes `en-GB`, `en_GB`, `en-AU`… são traduções. Se o idioma-fonte de um
  projeto for `en-GB`, reverta com override. `.xcstrings` guarda todos os idiomas num arquivo e conta inteiro como
  interface: é um limite do método.
- **Binários e imagens** (`BINARY`): o arquivo conta (peso 1), as linhas não.
- **Linhas efetivas** = adições + remoções dos arquivos que sobram.

## Áreas (`classify.py`)

Regras de caminho/extensão em ordem (`AREAS`), a primeira que casa vence: `tests`, configuração de build e de
ferramentas (sempre `infra`, em qualquer pasta: `pnpm-workspace.yaml`, `.gitattributes`, `renovate.json`,
`Package.swift`, `*.xcconfig`, `*.pbxproj`, `composer.json`, `jest.config.*`, `vitest.config.*`, `fastlane/`,
`buildSrc/`, `gradle/`, `AndroidManifest.xml`…), `docs`, `infra`,
`data`, servidores MCP, raízes de front-end (`webapp/`, `frontend/`, `*-front/`, `*-ui/`…), marcadores fortes de
backend (`api/`, `server/`, `trpc/`, `*-server/`…), extensões de interface (`.tsx`, `.vue`, `.css`,
`.erb`…, templates de servidor `.blade.php`, `.cshtml`, `.razor`, `.jsp`, `.gsp`, `.heex`, `.tmpl`, `.njk`,
`.jinja`, `.mustache`, `.gjs`/`.gts` e UI desktop `.qml`, `.ui` do Qt, `.xaml`), diretórios de interface
(`components/`, `pages/`, `hooks/`, `stores/`, `i18n/`…), código de view no servidor (`app/views|helpers|components|mailers/*.rb`,
`resources/views/`, `*_web/live/`), extensões de backend (`.go`, `.py`, `.rb`, `.java`, `.clj`…).
Colisões de nome de pasta: `charts/` com extensão de front-end é componente de gráfico (interface); sem ela,
Helm (infra). `license*`, `notice*`, `authors*` e `codeowners*` só são docs sem extensão de código (`license.go`
segue a linguagem). `.mustache` em pastas de gerador de SDK (`openapi`, `codegen`, `generator`, `sdk`) é infra.

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
marca uma suíte de integração, inclusive em caminhos que a regra já chama de teste (não há inferência de teste
unitário depois de um override `tests`). Um `force` de outra área num teste unitário muda a área e o arquivo
continua contado em `lines.tests.unit`. `False` volta ao comportamento antigo (todo teste em `tests`).

O que nenhuma regra resolve vai para `ambiguous.json`, agrupado por diretório. O agente decide a
área de cada grupo pelo caminho e pelos títulos de PR e grava `overrides.json`
(`{"<repo>/<prefixo>": "<área>"}`, prefixo mais longo vence). Valores simples de área resolvem arquivos ambíguos. A forma `{"force": "interface"}` substitui uma classificação existente. O valor `"exclude"` remove o arquivo do score. Chaves com curingas combinam o glob de repositório antes da primeira barra e o glob de caminho depois dela. Exclusões automáticas têm precedência.
O peso de revisão soma até 1.000 linhas por caminho distinto e até 10.000 por grupo. Os totais brutos continuam disponíveis. JSON em diretórios de mocks e fixtures sai quando a mudança excede 10.000 linhas no PR. Esses limites usam linhas alteradas porque o cache não contém o tamanho em bytes.
Marcadores de módulo Android são identificados nos caminhos observados em todo o cache do repositório. Sem evidência de módulo, Kotlin e Java seguem as regras de caminho e extensão. Diretórios de ferramentas de IA têm uma área própria. O que continuar ambíguo conta como `other`.

## Score e custo (`report.py`)

- `score = log2(1 + linhas efetivas) + 0,5 × log2(1 + arquivos efetivos)`. PR com score 0
  (só lockfile, por exemplo) não recebe custo e não conta como atividade.
- Fatia de cada área no PR = linhas efetivas da área / linhas efetivas do PR (arquivo com 0 linhas conta 1).
- Manutenção, em ordem:
  1. Rótulos primeiro. Um rótulo de bug (`BUG_LABEL`: bug, type: bug, kind/bug, pr: bugfix, regression, defect,
     hotfix, 🐛) marca manutenção. Um rótulo de funcionalidade (`FEATURE_LABEL`: feature, type: feature,
     enhancement, ✨) marca trabalho novo. Bug vence se os dois aparecem.
  2. Sem esses rótulos, decide o título. Primeiro sai o prefixo de issue (`ISSUE_PREFIX`: "Fix #123:",
     "Fixes #123 -", "Fix part of #123:", "Closes #123:", "Resolve #123:"). "Fix #12: Add Nepali" é trabalho novo;
     "Fix #12: Fix crash on save" é manutenção. O resto do título casa com `MAINTENANCE`, exceto quando é tipado
     como trabalho novo em conventional commits (`feat:`, `feat(x):`, `docs:`; regex `NEW_WORK`).
  A parte de interface desse PR é "manutenção de interface"; o resto da interface é "interface nova".
  Refatorações e migrações sem palavra ou rótulo de correção contam como trabalho novo.
- Cobertura de rótulos, medida nos caches das 100 organizações da base (PRs que não são de bot): 65 têm algum PR
  com rótulo de bug ou de funcionalidade, 25 em mais de 10% dos PRs, 16 em mais de 25% e 6 em mais de 50% (mediana
  0,2%). Na maioria das organizações o título continua decidindo.
- Amostra mínima: com menos de `MAINT_MIN_UI_PRS` (30) PRs de interface pontuados, a taxa de manutenção não é
  publicada. O JSON grava `null` em `interface_maintenance_share` e `interface_maintenance_annual_cost`, o HTML
  mostra "—" e diz que a amostra é pequena, a base de referência ignora a organização nessa métrica, e nenhum
  achado que depende da divisão manutenção/trabalho novo (capacidade, mediana, módulo, tendência de manutenção,
  frequência de correção, trabalho novo) entra no relatório.
- Custo: para cada pessoa e mês, o custo mensal é dividido entre os PRs mergeados por ela no mês,
  proporcional ao score. Mês sem PR carrega o custo para o próximo mês com PR dela. A pessoa é
  ativa do mês do primeiro ao mês do último PR no período.
  Custo do time = custo mensal × pessoas-mês ativas. Horas = custo × horas/mês ÷ custo mensal.
- Quem conta como time (`--authors`): `all`, `members` (membros da organização visíveis ao token)
  ou `min-prs:K` (K ou mais PRs no período). Organizações open source precisam de `members` ou
  `min-prs` para que contribuições avulsas não virem "time".

- Custo de interface nativa (cartão "interface nativa"): arquivos que terminaram em `interface`, por regra ou
  override, com extensão nativa (`.swift`, `.m`, `.mm`, `.kt`, `.java`, `.dart`, `.qml`, `.xib`, `.storyboard`,
  `.xcstrings`) ou em `src/main/res/`. Um `force` para fora da interface tira o arquivo do indicador.

## Os quatro números

1. Fatia do tempo de engenharia em interface = custo de interface / custo do time.
2. Custo mensal de interface = custo de interface no período / meses.
3. Fatia de manutenção = custo de manutenção de interface / custo de interface.
4. Manutenção anualizada = custo de manutenção de interface / meses × 12.

## Determinismo

Tudo é ordenado (repositório, número do PR, autor) e os floats são arredondados a 4 casas no JSON.
O HTML usa a data da coleta, não a data de hoje. Mesmo cache + mesmos parâmetros → saída idêntica byte a byte.

## Base de referência e prioridades

A unidade da base é a empresa. Cada empresa tem o mesmo peso. Os quartis usam interpolação linear entre observações ordenadas. A posição do time é a fração de empresas com valor estritamente menor, na base inteira (não há recorte por porte de time). Empates ficam fora do numerador. Sem custo de interface, a taxa de manutenção é indefinida.

A seção "Como vocês se comparam" mostra, para esforço em interface, manutenção, retrabalho em 30 dias e frequência de correções, cada empresa da base como um ponto e o time como o ponto aceso, com a mediana marcada. Uma métrica aparece só com quatro ou mais empresas com valor (`BENCH_MIN`) e com o valor do time publicado.

`report.py --findings` grava todas as evidências antes da seleção editorial. A ordem usa dinheiro no período
observado. Os valores dos cartões podem se sobrepor.

O relatório diagnostica e não prescreve: os textos não têm ação, e nenhuma comparação com a base vira economia ou
meta (não há cenário "se a manutenção estivesse na mediana"). A posição na base aparece como posição ("acima de 81%
das empresas"), com a mediana.

O relatório é um artigo em seis seções numeradas (01 custo, 02 onde a manutenção se concentra, 03 tempo,
04 retrabalho, 05 comparação com a base, 06 método). `findings.json` traz, por seção, os números formatados
(`facts`) e os achados que ela carrega (capacidade, plataformas e nativo em 01; concentração e correções em 02; pico
e tendência em 03). O agente escreve o título do relatório (`headline`, até 28 palavras, com pelo menos dois
números), o parágrafo de abertura (`lede`, até 75 palavras) e, por seção com fatos, um título que afirma o achado
(até 14 palavras, com um número) e uma ou duas frases de evidência (até 40 palavras cada). Todo número vem dos fatos
da própria seção ou dos seus achados (título e abertura podem usar os de qualquer seção); cada posição na base ou
mediana aparece em um campo só. Sem texto válido, modelos determinísticos escrevem os mesmos campos.

Cada achado aparece na sua seção, calculado dos dados: **quanto** (custo no período, ou no mês para o pico, e pessoas
equivalentes = custo ÷ meses ÷ custo mensal por pessoa), **onde** (os três módulos com mais custo no escopo do
achado), **quando** (custo mensal do escopo; para a tendência de interface, a participação mensal), **comparação**
(posição na base quando a métrica existe nela), **evidência** (PRs e arquivos de interface por trás do número) e
**limite** (quando importa: manutenção identificada pelo título, amostra pequena, telas pareadas pelo nome). A
comparação com a base fica toda na seção 05. O validador recusa linguagem prescritiva (imperativos e conselhos, sem
acento e por palavra inteira) e a mesma comparação com a base repetida em dois campos.

Um **módulo** é o diretório um nível abaixo da última pasta estrutural do caminho (`src`, `lib`, `app`, `apps`,
`packages`, `modules`, `features`, `frontend`, `web`, `client`, `main`): `twenty/packages/twenty-front/src/modules/
page-layout/widgets/x.tsx` → `…/modules/page-layout`; `n8n/packages/frontend/editor-ui/src/features/ai/…` →
`…/features/ai`.

Capacidade equivalente divide custo mensal pelo custo mensal por pessoa. A concentração usa o repositório com maior
custo de interface, acima de 30%. Quando um repositório tem 70% ou mais do custo de interface (`SINGLE_REPO_SHARE`;
inclui o caso de um repositório só), a concentração é medida por módulo dentro dele: a fatia dos três módulos com
mais custo de interface, nunca "o repositório X reúne N%".

Quando os cartões de manutenção (capacidade) e de frequência de correção têm pelo menos dois dos três módulos de
"onde" em comum, sai um cartão só: o de manutenção, com a frequência de correção e a posição na base como segunda
linha de comparação ("Frequência de correções: 20,7% dos PRs · acima de 27% das empresas"). A regra está na seleção
dos achados, não no agente.
A tendência de interface usa regressão sobre a participação mensal de interface no custo do time, com custo
positivo em todos os meses, inclinação mínima e qualidade de ajuste. O pico compara o mês mais caro com o custo
mensal médio de interface.

A frequência de correção conta cada PR pontuado com interface uma vez. Entre PRs de manutenção, título ou rótulo com marcador explícito de correção determina o numerador. A referência é a mediana das frequências por empresa. Relatórios antigos sem contagem de PRs de interface não contribuem para essa mediana.

Em caminhos explícitos de plataforma, um mesmo identificador de tela em `screens/` ou `pages/` dentro da mesma família de produto constitui evidência de implementações paralelas. O maior custo observado de construção é a plataforma de referência. Cada outra plataforma contribui seu próprio custo observado. A equivalência funcional precisa ser conferida pelos títulos dos PRs. Esses custos não estimam economia. O achado só aparece com 30 ou mais PRs de interface nessa tela.

## Blocos de diagnóstico

Calculados de cada arquivo de interface de cada PR pontuado (custo do arquivo = custo do PR × peso do arquivo).
Nada fica escondido atrás de clique. Uma plataforma com 95% ou mais do custo de interface (`PLATFORM_SINGLE_SHARE`)
não ganha gráfico: uma linha na seção 01 diz "Toda a interface medida é web (100% do custo)." ("Quase toda" quando
o valor arredondado fica abaixo de 100%).

- **Pontos de manutenção**: custo de manutenção por arquivo de interface, do maior para o menor. A frase diz que
  quantos arquivos de tela, e que fração dos que mudaram no período, concentram 80% (`HOTSPOT_COST_SHARE`) do custo
  de manutenção. Arquivos de tela são os de interface menos testes unitários, stories (`.stories.`, `__stories__/`,
  `storybook/`: `HOTSPOT_NOT_SCREEN`) e imagens ou binários; eles seguem contando no custo de interface; a
  curva mostra a fração acumulada do custo contra a fração de arquivos, com o eixo horizontal cortado na primeira
  fração redonda (10%, 20%, 25%, 50% ou 100%) que seja ao menos quatro vezes a dos arquivos que concentram 80%; a
  curva tem resolução de 0,1% dos arquivos. Os cinco módulos com mais custo de
  manutenção aparecem pelo nome, como pontos de luz com tamanho proporcional ao custo, com custo, fatia da
  manutenção, PRs e arquivos. Nunca por pessoa.
- **Retrabalho em 30 dias** (`REWORK_DAYS`): para cada arquivo de interface de um PR de trabalho novo, o arquivo
  conta como retrabalhado se um PR de manutenção o alterou depois do merge e até 30 dias depois. A taxa é o custo
  desses arquivos ÷ custo de trabalho novo em interface, arquivo a arquivo (um arquivo compartilhado não marca o PR
  inteiro). Só entram PRs mergeados pelo menos 30 dias antes do fim do período (os outros não têm a janela
  completa). O custo da manutenção seguinte soma cada arquivo de cada PR de manutenção uma vez. Com menos de 30 PRs
  de trabalho novo nessa janela a taxa não é publicada. Na base, `rework_share`; sem essa métrica no asset, o
  relatório não mostra a comparação.
- **Tendência**: custo mensal de interface dividido em trabalho novo e manutenção. A frase compara a fatia de
  manutenção da primeira metade do período com a da segunda (com número ímpar de meses, o mês do meio fica fora);
  diferença de até 3 p.p. (`TREND_STABLE_PP`) é "estável". Cada metade precisa de 30 PRs de interface; um mês com
  menos de 30 aparece sem divisão.
- **Plataformas** (`PLATFORMS`): pelo caminho (com o nome do repositório) e pela extensão de cada arquivo que
  terminou em interface, por regra ou override: iOS (`.swift`, `.m`, `.xib`, `ios/`…), Android (`.kt`, `.java`,
  `src/main/res/`, `android/`…), mobile multiplataforma (`.dart`, `mobile/`, `react-native/`, `expo/`), desktop
  (`.qml`, `.xaml`, `desktop/`, `electron/`, `tauri/`), templates de servidor (`.erb`, `.blade.php`, `.cshtml`,
  `.jsp`, `.heex`, `.njk`, `app/views/`…); o resto é web. Mostra custo e fatia de manutenção por plataforma; abaixo
  de 3% do custo de interface a plataforma não aparece; fatia de manutenção só com 30 ou mais PRs de interface na
  plataforma.

## Movimento

O HTML final já contém todos os números e o estado final de cada gráfico. Um script inline (sem rede) liga o
movimento só quando o navegador tem `IntersectionObserver` e não pede movimento reduzido: cada seção sobe ao entrar na
tela, números contam até o valor exato (1,4 s, desaceleração cúbica), barras e colunas crescem, linhas se desenham, os
pontos da base acendem em sequência e o ponto do time floresce; o fundo tem uma luz ambiente que deriva devagar e a
marca no cabeçalho repete a animação da landing. Sem JavaScript, com `prefers-reduced-motion` e na impressão
(`beforeprint` e `@media print`) a página fica no estado final, sem movimento.
