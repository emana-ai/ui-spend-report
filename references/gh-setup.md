# Preparar o GitHub CLI (`gh`)

O diagnóstico lê o GitHub só pelo `gh`, com o login de quem roda. Siga os passos na ordem.
Depois de cada correção, rode de novo a checagem:

```
python3 <skill>/scripts/collect.py --org ORG --check
```

O código de saída diz qual passo falhou: `2` sem gh, `3` sem login, `4` faltam escopos,
`5` SSO não autorizado, `6` organização não encontrada ou sem acesso.

## 1. Instalar o gh (código 2)

| Sistema | Comando |
|---|---|
| macOS | `brew install gh` |
| Ubuntu / Debian | `sudo apt update && sudo apt install gh` (se não achar: veja https://github.com/cli/cli/blob/trunk/docs/install_linux.md) |
| Fedora / RHEL | `sudo dnf install gh` |
| Arch | `sudo pacman -S github-cli` |
| Windows | `winget install --id GitHub.cli` (ou `choco install gh`, ou `scoop install gh`) |

Feche e abra o terminal e confira: `gh --version`.

## 2. Entrar na conta (código 3)

```
gh auth login
```

Escolha `GitHub.com`, `HTTPS`, confirme autenticar o Git e entre pelo navegador
(o terminal mostra um código de 8 caracteres para colar em https://github.com/login/device).
Use a conta que enxerga os repositórios da organização.

Confira com `gh auth status`: deve aparecer `Logged in to github.com account <você>`.

## 3. Dar os escopos `read:org` e `repo` (código 4)

```
gh auth refresh -h github.com -s read:org,repo
```

O navegador abre de novo para aprovar. Em `gh auth status`, a linha `Token scopes` precisa ter
`repo` e `read:org` (ou `admin:org`). O diagnóstico só lê; os escopos são os mínimos que o
GitHub oferece para ler PRs de repositórios privados e a lista de membros.

Se você usa `GH_TOKEN`/`GITHUB_TOKEN` com um token fine-grained, ele precisa de acesso de leitura a
"Pull requests", "Contents" (metadados) e "Members" da organização. Para simplificar, rode
`unset GH_TOKEN GITHUB_TOKEN` (Windows: `set GH_TOKEN=` e `set GITHUB_TOKEN=`) e use o login do passo 2.

## 4. Autorizar o token na organização com SSO (código 5)

Se a organização usa SAML SSO, o erro cita "SAML enforcement" ou "SSO". O token do gh precisa
ser autorizado uma vez para essa organização:

1. Abra https://github.com/settings/applications e, na aba **Authorized OAuth Apps**, encontre
   **GitHub CLI**. (Se você usa um token pessoal, vá em https://github.com/settings/tokens.)
2. Para token pessoal: ao lado do token, clique em **Configure SSO** e depois em **Authorize**
   ao lado da organização. Para o GitHub CLI (OAuth): clique no app e em **Grant** / **Request**
   ao lado da organização.
3. Faça o login do provedor de SSO da empresa se ele pedir.
4. Caminho mais curto: rode `gh auth refresh -h github.com -s read:org,repo`; na página de
   aprovação aparece a lista de organizações com SSO e o botão **Authorize** de cada uma.

Confira com `gh api orgs/ORG` (deve devolver os dados da organização, sem erro de SAML).

## 5. Organização não encontrada (código 6)

- Confira o nome exato (o que aparece em `https://github.com/ORG`), sem espaços nem maiúsculas trocadas.
- Confira se a sua conta é membro: `gh api user/memberships/orgs --jq '.[].organization.login'`.
- Se a organização restringe apps OAuth ("OAuth App access restrictions"), um admin precisa aprovar
  o GitHub CLI em Settings → Third-party access da organização.

## Limite de requisições

O script lê o limite da API GraphQL (5.000 pontos por hora) e espera sozinho quando ele baixa.
Uma organização com ~3.000 PRs em 6 meses gasta ~100 pontos. Erros 502 do GitHub são repetidos
com espera crescente; não é preciso fazer nada.
