---
name: ui-spend-report
description: >-
  UI Spend Report: mede, a partir dos PRs mergeados no GitHub da organização, quanto do
  tempo e do dinheiro do time de engenharia vai para interface (telas, componentes, estilos, i18n,
  e-mails) e quanto disso é manutenção do que já existe. Gera um HTML local com os números. Use quando
  pedirem "UI Spend Report", "ui spend report", "diagnóstico de interface", "diagnóstico", "quanto do time vai para interface", "quanto
  gastamos com front-end", "quanto custa manter as telas", "how much engineering time goes to UI".
---

# UI Spend Report

Produz `ui-spend-report-<org>-<data>.html` (pt-BR, autocontido, sem rede) e `ui-spend-report-<org>.json`
com quatro números: % do tempo de engenharia em interface, custo mensal disso, % que é manutenção e
custo anualizado da manutenção. Tudo roda na máquina do usuário; nenhum dado sai dela.

`<skill>` abaixo é o diretório deste arquivo. Use `python3` (no Windows, `python` ou `py -3`).
Só biblioteca padrão do Python 3.8+ e o `gh`. Nada para instalar além disso.

Para instalar via HTTPS, rode `git clone https://github.com/emana-ai/ui-spend-report ~/.claude/skills/ui-spend-report`.

## Quando usar

O usuário (em geral CTO ou líder de engenharia) quer ver o número do próprio time, numa
organização do GitHub. Não serve para GitLab/Bitbucket (v1 só GitHub).

## 1. Pré-requisitos

Pergunte o nome da organização primeiro (passo 2) e rode:

```
python3 <skill>/scripts/collect.py --org ORG --check
```

Saída 0 = pronto. Qualquer outro código: abra `<skill>/references/gh-setup.md`, ache o passo
do código (2 sem gh, 3 sem login, 4 faltam escopos, 5 SSO, 6 organização), guie o usuário
**um passo por vez** com o comando do sistema dele (macOS, Linux ou Windows), espere ele
confirmar e rode `--check` de novo. Repita até passar. Os comandos-chave:

- instalar: `brew install gh` / `sudo apt install gh` / `winget install --id GitHub.cli`
- entrar: `gh auth login`
- escopos: `gh auth refresh -h github.com -s read:org,repo`
- SSO: github.com/settings → token ou app GitHub CLI → **Configure SSO → Authorize** na organização
- conferir: `gh auth status`

Comandos de login abrem o navegador: peça para o usuário rodar no terminal dele (`! gh auth login`
no Claude Code) em vez de rodar você mesmo.

## 2. Perguntas (uma mensagem, com os padrões)

1. Organização do GitHub (obrigatório).
2. Período: padrão últimos 6 meses completos.
3. Custo mensal médio por pessoa de engenharia, **com encargos** (obrigatório; sem padrão).
4. Moeda: padrão BRL.
5. Horas por mês: padrão 160.
6. Opcional: repositórios para incluir ou excluir.
7. Opcional, se a organização for open source: quem conta como time
   (`members` ou `min-prs:3`; padrão `all` para organização privada).

Não invente o custo mensal. Se o usuário não souber, ofereça rodar com um valor de referência
que ele escolha e deixe claro no resumo que o valor é dele.

## 3. Comandos, em ordem

Use um diretório de saída por organização (padrão `./ui-spend-report-ORG`).

```
python3 <skill>/scripts/collect.py --org ORG --out ui-spend-report-ORG [--months 6] [--repos a,b] [--exclude-repos c]
python3 <skill>/scripts/classify.py --out ui-spend-report-ORG
# → trate ambiguous.json (passo 4), depois rode classify.py de novo
python3 <skill>/scripts/report.py --out ui-spend-report-ORG --monthly-cost 25000 --currency BRL --hours 160 --authors all
```

- `collect.py` mostra progresso; organizações grandes levam minutos. Ele respeita o limite da
  API sozinho. Rodar de novo com os mesmos parâmetros usa o cache (offline).
- Organização enorme (dezenas de milhares de PRs no período): ofereça `--max-repos N` (os N
  repositórios com mais PRs) ou `--max-prs N` por repositório, e diga isso no resumo.
- Mudar custo, moeda, horas ou `--authors` só exige rodar `report.py` de novo.

## 4. Arquivos ambíguos

`classify.py` grava `ambiguous.json`: diretórios cujos arquivos nenhuma regra classificou, com
linhas, extensões, caminhos e títulos de PR de exemplo, do maior para o menor.

1. Exclua primeiro artefatos de build. Revise os grupos pelo peso limitado por arquivo e diretório, até que o restante ambíguo seja ruído.
2. Para cada um, decida a área pelo caminho e pelos títulos: `interface`, `backend`, `infra`,
   `tests`, `data`, `docs` ou `other`. Interface = o que o usuário final vê ou o que só existe
   para a tela: componentes, telas, estilos, rotas de front-end, estado e chamadas de API do
   front-end, i18n/copy, validação de formulário no cliente, design system, app mobile, e-mails.
   Na dúvida entre duas áreas, use `other`. `tests` só para suítes de integração/e2e: testes
   unitários já seguem sozinhos a área do código que testam.
3. Grave `ui-spend-report-ORG/overrides.json`: `{"repo/caminho/": "area", ...}`. Pode usar um
   prefixo mais curto que o do grupo quando todo o subdiretório for da mesma área.
4. Confira no resumo as principais extensões e repositórios por área, inclusive arquivos que as regras já classificaram. Para corrigir uma regra use, por exemplo, `{"app-*/*.kt": {"force": "interface"}}`. Para retirar arquivos do score use `{"app-*/bundles/*": "exclude"}`. Rode de novo e confira o restante.

Você só escolhe áreas de diretórios. **Nunca escreva números**: todo número do relatório vem de
`report.py`. Não edite o HTML nem o JSON gerados.

## 5. Apresentar o resultado

1. Abra o HTML (`open` no macOS, `xdg-open` no Linux, `start` no Windows).
2. Resuma em 4 linhas, com os números exatos que `report.py` imprimiu:
   - % do tempo de engenharia em interface;
   - custo mensal de interface;
   - % da interface que foi manutenção;
   - custo anualizado da manutenção de interface.
3. Apresente a prioridade com maior custo observado e a posição do time na base. Cite o tamanho do grupo de comparação. Use as recomendações selecionadas pelo script.
4. Se o usuário discordar de uma área, ajuste `overrides.json` (ou as regras em
   `scripts/defaults.py`) e rode `report.py` de novo.

## Privacidade

- Só metadados: título, rótulos, autor, data, caminho e contagem de linhas de cada arquivo.
  Nunca conteúdo de arquivo ou diff.
- Os logins dos autores ficam só no cache local e na memória, para dividir o custo. O relatório
  mostra apenas agregados por área, repositório, mês e diretório: nenhuma tabela, gráfico ou nome
  por pessoa. Não mostre nem comente dados por pessoa no chat, mesmo se pedirem.
- Nada é enviado para fora: o `gh` fala só com a API do GitHub e o HTML não faz requisições.
- O cache fica em `ui-spend-report-ORG/cache/`; o usuário pode apagá-lo quando quiser.

## Referências

- `references/gh-setup.md`: instalação, login, escopos e SSO do `gh`, passo a passo.
- `references/method.md`: método completo (exclusões, regras de área, score, custo, determinismo).
- `scripts/defaults.py`: todos os parâmetros e regras.
