# Plano de desenvolvimento — qa-assistant

> Fonte de verdade do roadmap da framework. Versão 1.0 · 2026-09-23.
>
> Universal: nenhum nome de cliente, empresa, ambiente ou projeto real entra aqui. O estado de cada piloto real vive no `contexto.md` do respetivo workspace (`qa-workspaces/<empresa>/<projeto>/`), nunca neste ficheiro.

---

## Como usar este plano no Claude Code

1. Abrir o Claude Code na raiz do repositório (`cd $HOME\qa-assistant` e depois `claude`).
2. Pedir uma tarefa de cada vez, pelo número: *"Lê docs/plano-desenvolvimento.md e executa a tarefa 1.1."*
3. Antes de escrever código, o Claude Code descreve o que vai fazer e espera confirmação.
4. Uma tarefa só fica `[x]` quando **todos** os critérios de aceitação estão cumpridos e verificados (não apenas "correu").
5. Um commit por tarefa, com mensagem `<fase>.<tarefa>: <resumo>` (ex.: `1.1: skills analisar-funcionalidade, gerar-casos, planear-lote, checkpoint`). Depois do commit, acrescentar o hash à frente da tarefa.
6. Os guards de privacidade (detect-secrets + denylist) nunca se contornam. Se um commit for bloqueado, corrige-se o conteúdo — nunca o guard.
7. A partir da tarefa 1.5, qualquer alteração em `adapters/` ou `docs/tecnica-excel.md` tem de passar o teste golden antes do commit (metodologia-core §15).
8. Dados de exemplo e de teste são sempre fictícios.

---

## Estado resumido

| Fase | Objetivo | Estado |
|---|---|---|
| 0 | Base: repositório, documentos universais, guards, templates | Concluída |
| 1 | Completar o núcleo: skills em falta, adaptador Excel universal, teste golden | Concluída |
| 2 | Browser automation com Playwright MCP, demo público, piloto real | Em curso (2.1, 2.2, 2.3 e 2.5 concluídas; 2.4 no workspace do piloto, à espera das credenciais) |
| 3 | Novos módulos de técnica: API, acessibilidade, mobile | Concluída |
| 4 | CI da própria framework | Concluída |
| 5 | Interface: painel de progresso por workspace | Concluída |
| 6 | Portefólio: README, demo, decisão sobre visibilidade do repositório | Concluída (repositório fica privado por decisão do utilizador, 24.09.2026) |
| 7 | Fecho: CI no Linux, piloto, golden real, histórico protegido, versões fixas, demo reproduzível, decisões de âmbito, v1.0 | Em curso (7.1 e 7.4 a 7.8 concluídas; 7.3 com o mecanismo pronto; falta o piloto 7.2, que dá o golden real da 7.3, e depois a 7.9) |

Ordem recomendada: 1.1 → 1.2 → 1.3 → 1.4 → 1.5 → 2.x → 3.x → 4 → 5 → 6. O piloto real (2.4) pode avançar em paralelo, no respetivo workspace, a qualquer momento depois de 2.2.

---

## Fase 0 — Base (concluída)

- [x] Repositório pessoal privado `qa-assistant` no GitHub.
- [x] `docs/metodologia-core.md`, `docs/tecnica-browser.md`, `docs/tecnica-excel.md`.
- [x] `CLAUDE.md` raiz com referências e regras fixas.
- [x] `.gitignore` e guards de privacidade no pre-commit: `detect-secrets` + `scripts/check_denylist.py` (denylist pessoal em `~/.qa-denylist.txt`, fora do repositório). Testados nos dois sentidos.
- [x] Templates de workspace: `docs/contexto-template.md`, `docs/projeto.yaml`.
- [x] Skills: `onboarding-projeto`, `executar-lote`, `registar-bug`, `fim-sessao` (em `.claude/skills/` e cópia global em `~/.claude/skills/`).
- [x] Fluxo validado num piloto real: onboarding, importação de workbook e scripts existentes, migração do recálculo para LibreOffice headless, varredura completa de erros sem alterar o ficheiro oficial.

Lições do piloto já incorporadas na framework: recálculo nunca em pasta sincronizada/montada; o mecanismo de recálculo não é portátil entre ambientes; o vocabulário de estados é por projeto; nunca afirmar "validado" sem comparar números.

---

## Fase 1 — Completar o núcleo

### 1.1 Skills em falta — `4e2da2a`
O `CLAUDE.md` lista 8 comandos, mas só existem 4 skills. Criar as que faltam em `.claude/skills/<nome>/SKILL.md`:

- `analisar-funcionalidade` — analisa uma funcionalidade por role/perfil e produz a lista de comportamentos a testar (metodologia-core §5).
- `gerar-casos` — gera casos de teste a partir da análise, com as técnicas da §5 (partição de equivalência, valores-limite, tabelas de decisão, transições de estado, negativos), no formato do tracker do projeto (lido do `projeto.yaml`).
- `planear-lote` — agrupa casos por área + funcionalidade + plataforma + role + fluxo para minimizar logins e mudanças de contexto (§6).
- `checkpoint` — grava o registo leve, atualiza o `contexto.md` no formato da §12.2 e faz commit local no workspace.

Critérios de aceitação:
- [x] `dir .claude\skills` mostra 8 pastas, cada uma com `SKILL.md` e frontmatter `name` + `description`.
- [x] Cada skill cita a secção da metodologia que implementa e não contém nada específico de um projeto.
- [x] Dentro do `claude`, escrever `/` mostra as 8 skills com a etiqueta `(project)`. *(confirmado pelo utilizador, 24.09.2026)*
- [x] Cópia atualizada em `~/.claude/skills/`.
- [x] Commit passa os dois guards.

Nota: acrescentado `registo_leve` ao template `docs/projeto.yaml` (caminho do registo leve usado pelo `checkpoint`; por omissão `runs/`, nunca `historico/`).

### 1.2 Adaptador Excel universal — `158d310`
Criar `adapters/excel/base.py`, generalizando o padrão da `tecnica-excel.md` §1: carregar preservando fórmulas, aplicar lote, regenerar a aba de defeitos, cross-check, gravar só se limpo (senão `.REVISTO.xlsx`), recalcular em segurança.

- Toda a informação de layout (ficheiro, mapa prefixo → sheet, nomes de colunas, estados válidos de casos e defeitos, estados de defeito "fechados") vem de uma configuração, nunca hardcoded.
- `recalc_safe()` com dois mecanismos selecionáveis: LibreOffice headless (por omissão) e Excel via COM (opcional, só Windows). Sempre sobre uma cópia numa pasta temporária local.
- Deteção de lote reaplicado (a `tecnica-excel.md` §5 exige que reaplicar o mesmo lote não duplique notas nem defeitos).

Critérios de aceitação:
- [x] Nenhum nome de sheet, coluna ou estado no código (verificável por grep).
- [x] Funciona com uma configuração fictícia (usada no 1.4).
- [x] Reaplicar o mesmo lote duas vezes não altera o resultado da segunda vez.
- [x] `docs/tecnica-excel.md` atualizado para apontar para o adaptador.

Notas:
- Validado também por equivalência com um `qa_lib.py` real (cópia do workbook, lote sintético): 0 diferenças de valores, fórmulas e estilos. Diferenças intencionais: a sheet de controlo `_qa_lotes` e a verificação extra de "caso relacionado inexistente" no cross-check.
- Único nome de sheet no código: `_qa_lotes`, a sheet de controlo do próprio adaptador (por omissão; configurável).
- Mecanismo `excel-com` implementado mas **não validado** (sem Excel na máquina de desenvolvimento).
- Fora de âmbito: exportação para outras equipas (`tecnica-excel.md` §6) e regra "arquivar" de bugs resolvidos.

### 1.3 Configuração Excel no `projeto.yaml` — `259fbfc` (2.º critério fechado no onboarding do demo, `5afda66`)
Acrescentar ao template `docs/projeto.yaml` uma secção `excel:` com os campos que o adaptador precisa. Atualizar a skill `onboarding-projeto` para preencher esta secção **inspecionando os headers reais do workbook** (nunca adivinhar).

Critérios de aceitação:
- [x] Template com comentários a explicar cada campo.
- [x] `onboarding-projeto` pergunta/inspeciona e preenche `excel:`. *(verificado no onboarding interativo do demo, 2.1)*
- [x] O adaptador lê esta secção sem mais configuração.

Notas:
- `adapters/excel/inspecionar.py` só lê o workbook e propõe a secção; o que não é seguro fica `CONFIRMAR: …` (e faz o `load()` falhar se ficar esquecido).
- Na cópia de um workbook real acertou sheets de casos e prefixos, colunas de ID, estados de caso (pela validação de dados), sheet de defeitos, formato do ID e 6 das 10 colunas da vista; deixou por confirmar a coluna de estado dos defeitos (várias listas de validação), resultado obtido/observações e as colunas ambíguas da vista.
- `ExcelAdapter.from_projeto(workspace)`; blocos opcionais em branco contam como ausentes.

### 1.4 Teste golden — `c0ec70b`
Criar `tests/golden/`:

- `gerar_fixture.py` — gera um workbook fictício pequeno (ex.: 3 sheets de casos, 12 casos, 3 defeitos, uma aba de resumo com fórmulas).
- `lote.json` — um lote fictício que muda estados e cria um defeito.
- `esperado.json` — o estado esperado depois de aplicar o lote (estados, defeitos, totais do resumo).
- `scripts/golden_check.py` — gera a fixture numa pasta temporária, aplica o lote pelo adaptador, recalcula e compara com `esperado.json`.

Critérios de aceitação:
- [x] `python scripts/golden_check.py` passa.
- [x] Alterar de propósito um valor em `esperado.json` faz o teste falhar com uma mensagem clara (e depois repor).
- [x] Nenhum dado real: nomes de casos, defeitos e sheets são genéricos.

Notas:
- `esperado.json` escrito à mão a partir das regras (não gerado pelo adaptador); `{hoje}` é substituído pela data do dia. `--mostrar` imprime o obtido para revisão, mas nunca reescreve o esperado.
- Também verificado: desligar a deteção de lote repetido no adaptador faz o golden falhar; sem LibreOffice o golden sai com código 2 e mensagem clara (simulado). O CI da Fase 4 precisa de instalar o LibreOffice.
- O golden com lote histórico real via `QA_GOLDEN_DIR` (metodologia §15) ainda não existe; nota acrescentada à metodologia.

### 1.5 Golden no pre-commit — `8feece0`
Hook local no `.pre-commit-config.yaml` que corre `golden_check.py` quando mudam ficheiros em `adapters/`, `tests/golden/` ou `docs/tecnica-excel.md`.

Critérios de aceitação:
- [x] Commit que só toca em `docs/metodologia-core.md` não corre o golden.
- [x] Commit que parte o adaptador é bloqueado.

Notas:
- O hook também corre quando muda o próprio `scripts/golden_check.py` (decisão do utilizador, 23.09.2026).
- Validado num branch temporário, apagado no fim: só `metodologia-core.md` → `Skipped`; adaptador partido → `Failed` e commit bloqueado (HEAD inalterado); alteração válida em `tests/golden/` → `Passed`.
- Sem LibreOffice o golden sai com 2 e bloqueia o commit: instalar o LibreOffice, nunca `--no-verify`.

---

## Fase 2 — Browser automation (Playwright MCP)

### 2.1 Workspace de demonstração — `5afda66`
Criar `examples/demo/` dentro do repositório: um workspace completo (`CLAUDE.md`, `projeto.yaml`, `contexto.md`, workbook gerado pela fixture) apontado a um site público de prática de testes (ex.: saucedemo.com ou the-internet.herokuapp.com). Sem credenciais reais: só as contas de demonstração publicadas pelo próprio site, e mesmo essas no `.env` local.

Serve para desenvolver e validar a framework sem tocar em dados de clientes, e é a base do portefólio (Fase 6).

Critérios de aceitação:
- [x] Onboarding do demo feito com `/onboarding-projeto`.
- [x] Nada no demo passa a denylist nem o detect-secrets.

Notas:
- Site: saucedemo.com (contas de demonstração por role). Workbook com 18 casos próprios do site, aprovados pelo utilizador, no formato da fixture do golden (em vez da fixture tal como está); gerado por `examples/demo/gerar_workbook.py`.
- O onboarding revelou duas lacunas, corrigidas: caminhos fixos para o qa-assistant na skill (o demo vive dentro do repositório) e a inspeção não reconhecia uma sheet de defeitos ainda vazia.
- Credenciais só no `.env` local (ignorado pelo git); a palavra-passe de demonstração não aparece em nenhum ficheiro do repositório.
- Resultados esperados ainda não confirmados no browser: confirmar em 2.3.

### 2.2 Playwright MCP no onboarding — `0024c37`
Acrescentar à skill `onboarding-projeto` o registo do Playwright MCP no workspace:
`claude mcp add playwright --scope project -- npx @playwright/mcp@latest`

Critérios de aceitação:
- [x] `.mcp.json` criado no workspace (não no repositório da framework, exceto no demo).
- [x] `/mcp` dentro do `claude` mostra `playwright` ligado. *(confirmado pelo utilizador, 24.09.2026)*
- [x] Um login no site de demonstração funciona pelo MCP. *(confirmado pelo utilizador, 24.09.2026)*

Notas:
- No Windows o comando é `claude mcp add playwright --scope project -- cmd /c npx @playwright/mcp@latest`; sem o `cmd /c` o Claude Code não lança o `npx`. A skill escolhe o comando conforme o sistema.
- `@latest` mantido por agora; fixar a versão na passagem para N2 (2.5). Versão verificada nesta máquina: 0.0.82.
- `claude mcp list` mostra o servidor como `Pending approval` até ser aprovado no primeiro arranque do `claude` no workspace.

### 2.3 Primeiro lote no demo — `b9cfc1b`
Executar 5–10 casos do demo com `/planear-lote` e `/executar-lote`, aplicar pelo adaptador, `/checkpoint`.

Critérios de aceitação:
- [x] Cross-check limpo (e resumo vs contagem direta a bater certo).
- [x] Um defeito fictício registado com `/registar-bug` no formato do `projeto.yaml`: BUG-3 (problem_user, imagens), como `defeito_novo` no lote.
- [x] `contexto.md` do demo atualizado.

Notas:
- Os 8 casos aprovados, agrupados por role (sem sessão → locked_out_user → standard_user → problem_user): 6 Pass, 1 Fail (CT-PROD-005 → BUG-3), 1 Dúvida (CT-LOGIN-006: aparece o login com o erro certo, mas o URL continua `/inventory.html`; o esperado diz "volta à página de login"). A Dúvida fica fora do lote (o adaptador recusa-a) e está no `contexto.md` como pergunta.
- **Diferença de ambiente**: executado com a biblioteca Playwright no Chrome, conduzida pelo agente com scripts temporários (apagados), porque o Playwright MCP não estava disponível na sessão (2.2 por confirmar). Previsto na `tecnica-browser.md` §1.
- Lição confirmada: a 1.ª observação do problem_user veio vazia (o script não esperou pela lista de produtos); não se classificou sobre ela, repetiu-se com espera explícita (`tecnica-browser.md` §4.3).
- Decisão por delegação: BUG-3 escrito sem aprovação individual (o utilizador pediu para avançar sem perguntar); severidade e prioridade Média. Aprovado depois pelo utilizador, com as outras decisões por delegação (24.09.2026).
- CT-LOGIN-006: a Dúvida foi respondida pelo utilizador (conta como Pass mostrar o login com o erro certo, mesmo com o URL /inventory.html); esperado reescrito e caso aplicado no lote `lote_20260924_ui02`.

### 2.4 Piloto num projeto real
*Estado (23.09.2026): preparado no workspace do projeto (Playwright MCP registado, lote de 16 casos só de consulta proposto, capturas fora do git); execução à espera de 2.2/2.3 e das credenciais (2.2 e 2.3 concluídas a 24.09.2026: falta só as credenciais). Detalhe no `contexto.md` desse workspace.*

No workspace do projeto real (fora deste repositório): `.env` preenchido localmente, Playwright MCP registado, lote de 10–20 casos já conhecidos, comparação com os resultados anteriores. Todo o registo fica no `contexto.md` desse workspace.

Aqui só se marca feito e, se surgirem, lições **genéricas** para a metodologia ou técnica (sem nomes).

- [ ] Piloto concluído e comparado.
- [ ] Lições genéricas incorporadas (se houver).

### 2.5 Promoção N1 → N2 — `19f7fc7`
A partir de um caso do demo que passou pelo agente (N1), gerar um teste determinístico Playwright (`.spec.ts`) conforme `tecnica-browser.md` §13.

Critérios de aceitação:
- [x] `npx playwright test` corre e passa localmente (3 testes em `examples/demo/e2e/specs/login.spec.ts`, no Chrome instalado). Um esperado alterado de propósito faz o teste falhar.
- [x] Localizadores seguem a prioridade da `tecnica-browser.md` §4.2: botão por role + nome (nível 1); os campos não têm label, só placeholder (nível 3 — observação de acessibilidade); textos visíveis (nível 4). Sem CSS nem XPath.

Notas:
- Promovidos CT-LOGIN-001, 002 e 003: dois Pass N1 consecutivos (lote 2.3 e uma reexecução a 24.09.2026), dados determinísticos, esperado estável (§13). O ID do caso vai no título do teste; etiqueta `@smoke`.
- `@playwright/test` 1.63.0 fixado com lock; credenciais só por variáveis de ambiente (o `playwright.config.ts` lê o `.env` do demo quando existe).

---

## Fase 3 — Novos módulos de técnica

Cada módulo: um documento em `docs/` (mesma estrutura dos existentes: âmbito, níveis de execução, preparação, verificação, evidência, segurança, ligação à metodologia, checklist) + uma skill + um exemplo no demo.

### 3.1 API — `docs/tecnica-api.md` + `/testar-api` — `5b11b05`
Contratos e schemas, códigos de estado, autenticação e autorização por role, casos negativos, idempotência, paginação, limites. Ferramentas: `request` do Playwright, curl, coleções Postman quando o projeto já as tenha. Evidência: pedido/resposta com segredos redigidos.

Critérios de aceitação (3.1):
- [x] Documento agnóstico de projeto (exemplos só com a Loja Demo), passa os guards.
- [x] Skill `.claude/skills/testar-api` com frontmatter válido (teste `test_skills`), cópia global sincronizada.
- [x] `/` mostra `/testar-api` com a etiqueta `(project)`. *(confirmado pelo utilizador, 24.09.2026)*
- [x] Exemplo executado no demo: área API com 10 casos (restful-booker, esperados da documentação publicada), lote `runs/lote_20260924_api01.json` aplicado ao workbook: 9 Pass, 1 Fail → BUG-1 (500 a um POST sem campos obrigatórios); cross-check limpo, recálculo OK, resumo vs contagem direta bate certo.
- [x] Referência no `CLAUDE.md` (sem `@`: só se lê com o módulo `api` ativo, para não pesar em todas as sessões).

Notas:
- `scripts/api_pedido.py`: um pedido com evidência redigida (cabeçalhos de autenticação, chaves JSON com token/password/secret/api_key, e o valor literal de qualquer variável do `.env` ou segredo da sessão). Tokens guardados com `--guardar-segredo` em `.api-sessao.env` e usados como `{{TOKEN}}`: o agente encadeia pedidos sem nunca ver o token. Verificado: nenhum segredo nos 14 ficheiros de evidência do lote.
- No Git Bash (Windows) um argumento `/booking` chega convertido em `C:/Program Files/Git/booking` (conversão de caminhos do MSYS). O script aceita caminhos sem a barra inicial e explica o erro se receber um caminho convertido; a skill manda escrever sem barra.
- Decisões por delegação (o utilizador pediu para avançar sem perguntar): alvo restful-booker, os 10 casos de API e o BUG-1 (prioridade Média). Registadas no `contexto.md` do demo para revisão.
- 9 testes do `api_pedido.py` com um servidor HTTP local (sem rede).

### 3.2 Acessibilidade — `docs/tecnica-acessibilidade.md` + `/auditar-acessibilidade` — `afbdbe7`
WCAG 2.1 AA. Parte automática com axe-core via Playwright; parte manual guiada (teclado, ordem de foco, leitor de ecrã, contraste, zoom 200%, reflow). Cada falha mapeada ao critério WCAG correspondente e ao nível.

Critérios de aceitação (3.2):
- [x] Documento agnóstico de projeto, passa os guards.
- [x] Skill `.claude/skills/auditar-acessibilidade` com frontmatter válido, cópia global sincronizada.
- [x] `/` mostra `/auditar-acessibilidade` com a etiqueta `(project)`. *(confirmado pelo utilizador, 24.09.2026)*
- [x] Exemplo executado no demo: área Acessibilidade (7 casos). Auditoria automática em 4 páginas (login, produtos, carrinho, checkout-dados; 3 com login): 0 violações WCAG 2.1 A/AA, boas práticas e 1 incompleto de contraste registados; CT-A11Y-006 (2.4.2 A, títulos iguais) Fail → BUG-2. Os manuais CT-A11Y-005 (contraste) e CT-A11Y-007 (teclado) ficam Por testar: precisam do browser pelo agente (Playwright MCP).
- [x] Referência no `CLAUDE.md` (sem `@`, como a API).

Notas:
- `tools/a11y/` (Node, versões fixadas: Playwright 1.63.0, @axe-core/playwright 4.13.0; `npm ci --prefix tools/a11y`) só abre as páginas e corre o axe; usa o Chrome instalado (`channel: chrome`), sem descarregar browsers. `scripts/auditar_a11y.py` lê o `projeto.yaml`, passa as credenciais do login por variáveis de ambiente (nunca argumentos) e mapeia as tags do axe a critérios e níveis; boas práticas → observações, incompletos → revisão manual.
- Uma página redirecionada (ex.: login falhado) é tratada como não auditada: o axe na página de login daria um falso Pass.
- O workbook do demo passou a ser reconstruível (`examples/demo/reconstruir_workbook.py`: gerador + lotes de `runs/` pela ordem de execução, pelo 1.º `ts` de cada lote; pelo nome, os BUG-n trocavam).
- `.env` do demo: contas do saucedemo preenchidas localmente por delegação, confirmadas no código do site.
- 10 testes do `auditar_a11y.py` sem browser (resultado bruto do axe fictício). A parte Node não corre no CI.

### 3.3 Mobile — `docs/tecnica-mobile.md` + `/testar-mobile` — `908e922`
Flows Maestro em YAML, emulador vs. dispositivo real, gestos, permissões, deep links, estados offline/background, diferenças iOS/Android.

Critérios de aceitação (3.3):
- [x] Documento agnóstico de projeto, passa os guards.
- [x] Skill `.claude/skills/testar-mobile` com frontmatter válido, cópia global sincronizada.
- [x] `/` mostra `/testar-mobile` com a etiqueta `(project)`. *(confirmado pelo utilizador, 24.09.2026)*
- [x] Exemplo executado no demo: área Mobile, 3 flows Maestro (login, utilizador bloqueado, carrinho) corridos com `scripts/testar_mobile.py`: 3 Pass. **Em Maestro Web (beta)**, não numa app nativa: esta máquina não tem adb, emulador nem dispositivo. Verificado que um flow com a asserção errada dá Fail.
- [x] Referência no `CLAUDE.md` (sem `@`).

Notas:
- `scripts/testar_mobile.py`: um flow por caso (o ID é o prefixo do nome do ficheiro); credenciais do `.env` em variáveis `MAESTRO_<VAR>` (o Maestro dá-as aos flows como `${MAESTRO_<VAR>}`), nunca `-e` na linha de comandos; lê o JUnit do Maestro e redige as mensagens. Sem alvo (Android sem dispositivo, iOS sem simulador) os casos ficam Blocked e o Maestro nem corre.
- Por fazer, quando houver emulador: um exemplo numa app nativa (ex.: a app de demonstração da Sauce Labs).
- 8 testes do `testar_mobile.py` com o Maestro e o adb simulados (o formato JUnit é o real do Maestro 2.10).

### 3.4 Módulos no `projeto.yaml` — `abfddc4`
Lista fechada de valores válidos em `modulos:` (`funcional-web`, `api`, `acessibilidade`, `mobile`, `exploratorio`) e `onboarding-projeto` a perguntar quais ativar.

Critérios de aceitação (3.4):
- [x] Lista fechada na metodologia §4.1 (módulo → técnica → skill → secção do `projeto.yaml`) e comentada no template.
- [x] `onboarding-projeto` mostra a lista, pergunta quais ativar, preenche as secções dos módulos ativos e acaba com `scripts/validar_projeto.py` sem problemas.
- [x] `scripts/validar_projeto.py` recusa módulos fora da lista e módulos ativos sem secção; o demo passa (0 problemas, 0 avisos). 7 testes.

Notas:
- A `tecnica-browser.md` listava como módulos `smoke`, `roles`, `visual`, `cross-browser` e `formularios`, que não existem: passam a variantes dentro dos módulos, não valores de `modulos:`. Referências antigas a `modulos/*.md` corrigidas.
- Os critérios comuns da Fase 3 (documento, skill, exemplo no demo, `CLAUDE.md`) ficaram verificados em cada tarefa (3.1–3.3); a verificação manual do `/` foi confirmada pelo utilizador (24.09.2026).

---

## Fase 4 — CI da framework

### 4.1 GitHub Actions no `qa-assistant` — `1392eda`, `fa749eb`
Em cada push: `golden_check.py`, `detect-secrets`, lint Python (ruff).

A denylist pessoal não corre no CI (vive só no teu computador, de propósito); continua a ser a barreira no pre-commit local.

Notas:
- `.github/workflows/ci.yml`: jobs `lint` (ruff 0.16.8, fixado), `secrets` (só o hook `detect-secrets` do pre-commit, mesma versão e baseline) e `golden` (instala `libreoffice-calc-nogui`, ~35 s). Python 3.12 no CI; `ruff.toml` com `DTZ` desligado (datas locais intencionais).
- Lint limpo antes de ligar o CI: 32 avisos corrigidos, entre eles o B023 em `apply_lote` (a linha do defeito passou a parâmetro do `put`; não era bug, mas ficava frágil). Golden inalterado.
- No Linux o ruff exige o bit de execução em ficheiros com shebang (`git update-index --chmod=+x`); no Windows não se nota.
- Critério 2 validado num branch temporário apagado no fim: o commit partido foi criado pela API do GitHub (como uma edição no site, sem hooks locais), porque o pre-commit bloqueia-o localmente e o guard não se contorna. Resultado: job `golden` falhado com `apply.novos_defeitos[0]: esperado 'BUG-99', obtido 'BUG-4'`.
- Repositório privado: o badge (no `CLAUDE.md`, até haver README na 6.1) só aparece a quem tem acesso.
- Runners fixados em `ubuntu-24.04` (`3f72fcd`), em vez de `ubuntu-latest`, que passa ao Ubuntu 26 a partir de 19.10.2026 (aviso do GitHub nos jobs). A mudança de versão fica uma decisão explícita.

### 4.2 Regressão do demo — `034d1c4`
Correr os `.spec.ts` do demo (2.5) no CI contra o site público.

Notas:
- Job `demo-e2e`: Chromium do Playwright, `npm ci` e `npx playwright test` em `examples/demo/e2e`. As contas de demonstração vêm dos secrets do repositório `DEMO_SAUCE_*`; sem eles o job emite um aviso e não corre os testes (não falha).
- A 1.ª execução (24.09.2026) foi antes de os secrets existirem e só deu o aviso. Reexecutado o job depois de os criar: `3 passed` contra o site público.

Critérios de aceitação (da 4.1):
- [x] Badge verde no GitHub.
- [x] Um commit que parte o golden aparece como falhado no CI.

Nota: a regressão de projetos reais corre no workspace ou no CI da empresa desse projeto, nunca neste repositório.

---

## Fase 5 — Interface

### 5.1 Painel por workspace — `e6cc04f`
`scripts/gerar_painel.py <caminho-do-workspace>` lê o workbook (pelo adaptador) e o `contexto.md` e gera `painel.html` dentro do workspace: progresso por área e estado, defeitos abertos por severidade, último checkpoint e próximos passos. O HTML gerado fica no workspace (no `.gitignore`), nunca no repositório.

Notas:
- Só lê o workbook (`load()`, nunca grava). Método novo no adaptador, `defeitos_abertos()`, reutilizado pela `regenerate_active_defects()`; golden inalterado.
- O significado dos estados vem da secção opcional `painel:` do `projeto.yaml` (`pendente | ok | falha | bloqueado | na`), validada contra `excel.casos.estados`; sem ela, o 1.º estado conta como pendente. A `onboarding-projeto` pergunta-a e põe o `painel.html` no `.gitignore` do workspace.
- Do `contexto.md` mostra a linha `Atualizado:` e as secções ponto de execução, trabalho pendente, dúvidas e bloqueios (Markdown mínimo convertido no próprio script). Os números vão também num bloco JSON (`#numeros`) no HTML.
- Só `tracker: excel` por agora; outro tracker sai com código 2 e mensagem clara.
- Validação (24.09.2026): no demo, 0/18 executados, igual ao `progress_summary()` (ainda sem execuções). Na fixture do golden com o lote aplicado e recalculado, os números do painel batem com a Summary recalculada pelo LibreOffice (por área e total), com o `esperado.json` e com a ordem da vista de ativos; sem `src`/`href` externos nem `<link>`/`@import`. A verificação foi manual (script no scratchpad); se o painel mudar muito, vale passá-la a teste no CI.

### 5.2 Skill `/painel` — `a016109`
Gera e abre o painel do workspace atual.

Critérios de aceitação (da 5.2):
- [x] `.claude/skills/painel/SKILL.md` com frontmatter válido, cópia global em `~/.claude/skills/`, referida no `CLAUDE.md` e na metodologia §3.
- [x] Seguindo os passos da skill no demo, o painel é gerado e abre no browser (`gerar_painel.py --abrir`); com um tracker que não é Excel sai com 2 sem gerar nada.
- [x] Dentro do `claude`, escrever `/` mostra `/painel` com a etiqueta `(project)`. *(confirmado pelo utilizador, 24.09.2026)*

Notas:
- `--abrir` usa o módulo `webbrowser` com o URI do ficheiro local: igual em Windows, macOS e Linux, sem aspas de shell. O script passa a imprimir o estado do resumo do workbook, que a skill resume.
- A skill proíbe publicar ou enviar o painel: num projeto real tem dados do cliente (metodologia §1.2).
- Encontrado ao testar: o frontmatter da `fim-sessao` tinha YAML inválido desde a fase 0 (`: ` na descrição sem aspas) e o Claude Code mostrava a 1.ª linha do corpo como descrição. Corrigido; `tests/test_skills.py` valida o frontmatter de todas as skills e que cada uma está no `CLAUDE.md`.

Critérios de aceitação (da 5.1):
- [x] Painel do demo gerado e coerente com o workbook (totais batem).
- [x] Funciona offline, um único ficheiro HTML.

---

## Fase 6 — Portefólio

### 6.1 README — `00278ee`
Arquitetura em camadas, fronteira de privacidade, como começar (onboarding de um workspace novo), demo, módulos disponíveis.

Notas: `README.md` com as quatro camadas, a fronteira de privacidade e os guards, o fluxo de trabalho (diagrama mermaid), o onboarding de um projeto novo, a tabela de módulos, o demo, testes/CI e requisitos. O badge do CI passou do `CLAUDE.md` para o README (como previsto na 4.1).

### 6.2 Visibilidade do repositório
Decidir se o repositório passa a público. Antes disso, obrigatório:
- [x] Varrer **todo o histórico** com detect-secrets e com a denylist (não só o estado atual), porque os primeiros commits foram feitos antes de os guards existirem.
- [x] Rever `examples/demo/` à procura de qualquer dado não fictício.
- [x] Decisão do utilizador sobre a visibilidade: **fica privado por agora** (utilizador, 24.09.2026). A rever quando o portefólio for para uso público.

Verificações (24.09.2026, 52 commits, 219 versões de ficheiros extraídas de todos os ramos):
- detect-secrets em todas as versões, **refeito em modo UTF-8** (a 1.ª varredura, em cp1252, saltava ficheiros com certos caracteres — ver registo de 24.09): 1 achado, a linha `password: DEMO_BOOKER_PASSWORD` da versão antiga do `projeto.yaml` do demo, que é o nome de uma variável e não um segredo (fora os hashes de integridade dos `package-lock.json`, excluídos). Confirmado que a varredura lê os ficheiros, com um segredo plantado (apanhado).
- Denylist (texto, nomes e conteúdo de ficheiros Office) em todas as versões: 0 achados. Confirmado com um termo real da lista plantado (bloqueado).
- Nenhum `.env` nem `.api-sessao.env` alguma vez no histórico; as palavras-passe de demonstração não aparecem em nenhuma versão; sem caminhos locais nem o nome de utilizador da máquina (só o nome do repositório no GitHub, no badge).
- `examples/demo/`: só sites públicos de prática (saucedemo.com, restful-booker), contas de demonstração publicadas pelos próprios sites referidas pelo nome da variável, dados de teste com prefixo `QA-AUTO-`, bugs dos sites de prática.

---

## Fase 7 — Fecho da framework

Saída da revisão geral 2 (24.09.2026, `a57ed19`): o que falta para dar o qa-assistant por concluído (v1.0). Ordem recomendada: 7.1 → 7.2 → 7.3; 7.4, 7.5 e 7.6 em paralelo; 7.7 decidido já, porque muda o que as outras têm de cobrir.

### 7.1 CI verde depois da revisão geral 2 — `1efa40f`
A correção do timeout do recálculo (grupo de processos, fora do Windows) só se verifica no Linux; na máquina de desenvolvimento não havia Docker ativo nem WSL com distribuição.
- [x] CI de `a57ed19` verde nos 5 jobs (o `tests/test_recalc.py` corre no job `testes`, em Ubuntu).

Notas:
- Run 36053764682 (push de `a57ed19` + `1efa40f`, 24.09.2026): demo-e2e, lint, secrets, testes e golden verdes. No job `testes` (Ubuntu) correram os 117 testes; passaram `test_timeout_mata_a_arvore_de_processos` (timeout efetivo com um neto pendurado, pelo grupo de processos), `test_acentos_chegam_intactos_por_um_pipe` e `test_gitignore_num_workspace_com_git`.

### 7.2 Piloto num projeto real (é a 2.4)
- [ ] `.env` do workspace do piloto preenchido pelo utilizador.
- [ ] `python scripts/validar_projeto.py <workspace>` sem problemas **com as regras novas** (URLs dentro dos ambientes, `.gitignore`); o que aparecer corrige-se no workspace, nunca aqui.
- [ ] Lote de 16 casos executado e comparado com os resultados anteriores (fecha a 2.4).
- [ ] Lições genéricas incorporadas, anonimizadas (metodologia §1.4, §17).
- [ ] Reavaliar o subagente `executor-web` (7.7): se os lotes do piloto encheram o contexto principal, criá-lo; senão, fica o subagente genérico.
- [ ] Confirmar a proteção de `historico/` (7.4): a edição de um ficheiro em `historico/` é recusada no `claude` aberto no workspace.
- [ ] Para a 7.3: **antes** de aplicar o lote no `/checkpoint`, copiar o workbook para `<workspace>/golden/<lote>/antes.xlsx`; depois do checkpoint revisto, copiar o workbook para `depois.xlsx`, o lote (com o nome original) e escrever `golden.yaml` com `agora` = o `aplicado_em` da sheet `_qa_lotes` para esse lote.

### 7.3 Golden com lote histórico real (`QA_GOLDEN_DIR`, metodologia §15) — mecanismo em `e74c281`
- [x] `golden_check.py` aceita uma pasta de workspace (`QA_GOLDEN_DIR` ou `--dir`) com workbook de partida, lote e esperado, que ficam **só no workspace**.

Notas (1.º critério, adiantado sem o piloto por decisão do utilizador, 24.09.2026):
- Pasta `<workspace>/golden/<lote>/` com `antes.xlsx`, o `lote_*.json` com o nome original, `depois.xlsx` (saída guardada e revista) e `golden.yaml` (`agora` obrigatório; `etiqueta` e `reportado_por` opcionais). Uma pasta com várias destas corre-as todas. O `projeto.yaml` é o do workspace (a pasta acima mais próxima), com a configuração **atual**.
- Aplica pelo mesmo `aplicar` do `/checkpoint` (com `--agora`, 7.6) a uma cópia de `antes.xlsx` numa pasta temporária, compara todas as células de todas as sheets (fórmula e valor calculado) com `depois.xlsx` e confirma que reaplicar não muda nada. O esperado nunca é gerado pelo script. Códigos: 0 igual · 1 diferenças (listadas por célula) · 2 pasta incompleta, sem `projeto.yaml` acima ou sem LibreOffice.
- O fictício corre sempre primeiro: com `QA_GOLDEN_DIR` no ambiente, o hook do `pre-commit` não o perde.
- `tests/test_golden_real.py` (dados fictícios, workspace temporário): passa com o `depois` certo, falha a apontar a célula alterada, deteta outro instante nas datas escritas, corre várias pastas e a variável de ambiente, recusa pastas incompletas. No CI, no job `golden`.
- Especificado na metodologia §15.
- [ ] Golden real do piloto a passar; a nota de "ainda não implementado" sai da metodologia §15.

### 7.4 `historico/` protegido de facto (metodologia §4 passo 4, §13) — `3563f24`
Hoje a metodologia promete a proteção por permissões do Claude Code, mas nada a cria.
- [x] O `/onboarding-projeto` cria o `.claude/settings.json` do workspace com `deny` de escrita e edição em `historico/**`.
- [x] O `validar_projeto` avisa quando o workspace não o tem; teste.

Notas:
- Template `docs/workspace/settings.json` com `"deny": ["Edit(/historico/**)"]`. Sintaxe confirmada na documentação de permissões do Claude Code: o `/` inicial ancora na pasta do workspace (settings de projeto); uma regra `Edit` cobre todas as ferramentas que editam ficheiros e os comandos Bash reconhecidos (`sed`, `tee`, `>`); uma regra `Write(...)` com caminho é ignorada. Não cobre scripts que abram ficheiros por conta própria (metodologia §13).
- O `/onboarding-projeto` copia o template, ou acrescenta a regra a um `settings.json` que já exista; o demo tem o mesmo ficheiro em `examples/demo/.claude/`.
- `validar_projeto`: aviso sem a regra (aceita `/historico/**`, `./historico/**` ou `historico/**`, em `settings.json` ou `settings.local.json`); 2 testes novos, e o demo continua com 0 problemas e 0 avisos.
- Por confirmar no piloto (7.2): abrir o `claude` no workspace e ver a edição de um ficheiro em `historico/` recusada.

### 7.5 Versões fixas — `0a61bc6`
- [x] `@playwright/mcp` com versão fixa no `.mcp.json` do demo e no comando do `/onboarding-projeto` (hoje `@latest`).
- [x] `requirements.txt` com versões exatas de `openpyxl` e `pyyaml` (uma atualização silenciosa pode mudar o golden); atualizar é uma decisão com golden a passar.

Notas:
- `@playwright/mcp@0.0.82` (a mais recente a 24.09.2026; `npx @playwright/mcp@0.0.82 --version` confirmado). Regra na técnica browser §1.
- `openpyxl==3.1.5`, `et-xmlfile==2.0.0` (dependência do openpyxl) e `pyyaml==6.0.3`: as mesmas que o CI já instalava, por isso o golden não muda.
- `tests/test_guards.py`: dependências Python só com `==`, e uma só versão numérica do `@playwright/mcp` no demo e na skill (um `@latest` esquecido faz o teste falhar).

### 7.6 Demo reproduzível — `ced85fc`
A reconstrução põe datas do dia (defeitos novos, resumo da vista, `_qa_lotes`, propriedades do ficheiro) e o `.xlsx` aparece sempre alterado no git.
- [x] Data de cada lote fixada pelo 1.º `ts` do lote na reconstrução (opção no `aplicar`, sem mudar o comportamento por omissão).
- [x] Teste no CI: as células do workbook versionado são iguais às do gerador + lotes.

Notas:
- `aplicar --agora AAAA-MM-DDTHH:MM:SS` (atributo `agora` do `ExcelAdapter`): etiqueta por omissão, data de report dos defeitos novos, data do resumo da vista de ativos e `aplicado_em` usam esse instante; sem a opção, o relógio (golden inalterado). Especificado na técnica Excel §1.
- `reconstruir_workbook.py [workspace]` passa o 1.º `ts` de cada lote e aceita outra pasta (o teste reconstrói numa pasta temporária, nunca no demo).
- `tests/test_demo_reproduzivel.py`: `Agora` (sem LibreOffice: o mesmo instante dá as mesmas células; as datas escritas são as do instante) e `Demo` (reconstrução = workbook versionado, célula a célula, em todas as sheets; no job `golden`). O `Demo` falhou contra o workbook anterior (datas do dia da reconstrução) e passa com o regenerado; duas reconstruções seguidas dão as mesmas células.
- O `.xlsx` continua a mudar byte a byte a cada reconstrução (propriedades do ficheiro gravadas pelo openpyxl e pelo LibreOffice); o critério é o das células.

### 7.7 Decisões de âmbito (utilizador) — `3ffbb62`
Recomendações aplicadas por decisão do utilizador (24.09.2026).
- [x] `qa_core` / CLI `qa`: **não se constrói**; os adaptadores e os scripts são o motor. Metodologia §1.1 e §2 reescritas (o modelo interno é o vocabulário comum, não uma base de dados à parte).
- [x] Subagente `executor-web`: **não se constrói agora**; usa-se o subagente genérico com as instruções do `/executar-lote` (técnica browser §11). Reavaliado no piloto (7.2).
- [x] Trackers `jira` e `azure-devops`: **registo manual** até um projeto real precisar de adaptador. Nova metodologia §4.2 (tabela por tracker), comentário no template, passo 7a no `/checkpoint`, e o `validar_projeto` avisa quando o tracker não tem adaptador (teste novo).

### 7.8 Arrumação (opcional) — `799e068`
- [x] `scripts/comum.py` com a leitura do `projeto.yaml` e do `.env` (hoje o `testar_mobile` e o `auditar_a11y` importam do `api_pedido`), sem mudar comportamento.

Notas:
- `comum.py`: `ler_projeto`, `ler_env`, `ler_env_workspace` e o `Redator` (com as constantes de redação que ele usa). Usado pelo `api_pedido`, `auditar_a11y`, `testar_mobile`, `gerar_painel` e `validar_projeto`; o `api_pedido` continua a exportar os mesmos nomes. -90 linhas nos scripts.
- Comportamento igual: os testes existentes passam sem mudanças (só o `test_testar_mobile` importa o `Redator` do `comum`); `tests/test_comum.py` novo (leitura do `.env` e do `projeto.yaml`, mensagens de erro dos scripts mantidas, uma só implementação). Demo corrido de novo: validação, painel, API, acessibilidade e mobile iguais.
- Visto de passagem, sem mudar (fora do âmbito): `testar_mobile.py` com um flow que não é um ficheiro (ex.: `CT-MOB-001` em vez do caminho) dá `Blocked` "o flow nao correu" em vez de erro de uso (código 2). Corrigido a seguir, à parte (ver registo de alterações).

### 7.9 Versão 1.0
- [ ] 7.1 a 7.6 feitas e decisões da 7.7 escritas nos documentos.
- [ ] README com o estado final; tag `v1.0`; tabela "Estado resumido" com todas as fases concluídas.

---

## Registo de alterações a este plano

| Data | Alteração |
|---|---|
| 2026-09-23 | Versão inicial |
| 2026-09-24 | Revisão geral (`994fa8b`): denylist passa a ler ficheiros Office e nomes de ficheiros (antes um `.xlsx` de cliente passava o guard; histórico verificado, limpo); adaptador valida o lote inteiro antes de escrever (`validar_lote`, código 4 no `aplicar`) e aceita `bug` existente; um só formato de registo leve (metodologia §7.1 = técnica-excel §4); 27 testes `unittest` no CI e no pre-commit (inclui a verificação do painel da 5.1); ruff no pre-commit; skills alinhadas com o fluxo. |
| 2026-09-24 | Lote editado depois de aplicado: antes era reaplicado (hash diferente) e duplicava notas e defeitos; agora a sheet de controlo guarda o `lote_id` (nome do ficheiro) e o adaptador recusa-o (código 4). Verificado: o `main` não pode ser protegido (repositório privado, plano Free: API 403); o CI é aviso depois do push, não barreira — a decidir na 6.2. |
| 2026-09-24 | Resumo vs contagem direta: bloco opcional `excel.aba_resumo`; `verificar_resumo()` compara os valores recalculados do resumo com a contagem caso a caso (por área, estado, total e linha de total); código 5 no `aplicar`, alerta no painel, parte do golden. Os totais nunca se leem do resumo. Detetados nos testes: fórmula a contar a coluna errada e caso fora do intervalo fixo (o demo usa `A2:A200`). |
| 2026-09-24 | Definição de "executado" (decisão do utilizador): todo o caso fora de `Por testar`; N/A e Blocked contam como executados; % executada = executados / total. Escrita na metodologia §16.3; fórmula do resumo do demo alinhada (antes excluía os N/A) e workbook regenerado (só essas fórmulas e a nota mudaram). |
| 2026-09-24 | Verificação da % executada do resumo (`aba_resumo.coluna_percentagem` + `estado_por_testar`): compara com (total − por testar) / total, fração ou pontos percentuais, tolerância estrita por omissão (0,001 pp); o painel recusa um `estado_por_testar` diferente do seu estado pendente. Demo configurado. |
| 2026-09-24 | detect-secrets: o do CI (Linux, Python 3.12) marcou `password: <NOME_VAR>` num mapa YAML inline que o local (Windows, Python 3.14) deixa passar, com a mesma versão e baseline; segredos óbvios são apanhados nos dois. Corrigido o conteúdo (chaves `var_utilizador`/`var_palavra_passe`); causa exata por apurar. Nos workspaces sem CI, o guard local é a única barreira. |
| 2026-09-24 | **Causa da diferença do detect-secrets encontrada**: a versão 1.5.0 abre os ficheiros com `open()` sem encoding; no Windows (cp1252) um ficheiro UTF-8 com certos caracteres (ex.: "Á", bytes C3 81) dá `UnicodeDecodeError` e é **saltado sem aviso**, como se fosse binário (6 ficheiros do repositório, incluindo a metodologia). Não era o Python 3.14 nem o YAML. Corrigido: hook local com a mesma versão em modo UTF-8 (`python -X utf8`), mesmo id para o CI; `tests/test_guards.py` impede o retrocesso. A varredura do histórico da 6.2 foi refeita em UTF-8: 1 achado, o falso positivo já conhecido (nome de variável na versão antiga do `projeto.yaml` do demo). |
| 2026-09-24 | CI: runners fixados em `ubuntu-24.04` (`3f72fcd`) antes da migração do `ubuntu-latest` para o Ubuntu 26 (19.10.2026); os 5 jobs passaram (run 36047318268). Nota na 4.1. |
| 2026-09-24 | Revisão geral 2 (teste de tudo → análise → correções → reteste): **inspetor** deixava a sheet de defeitos sem proposta quando todos os defeitos estavam abertos (vista com os mesmos IDs; acontecia no demo), agora desempata pela linha do cabeçalho e pelo n.º de colunas, com `tests/test_inspecionar.py` (não havia testes); **consola**: no Windows, a saída lida por pipe vinha em cp1252 e os acentos chegavam trocados ao Claude Code, agora todos os pontos de entrada escrevem em UTF-8 (`tests/test_consola.py`); **validar_projeto**: ambiente sem URL passava (era o caso do template), URLs de `api`/`acessibilidade` fora dos ambientes declarados ou de produção passam a problema, e avisa se `.env`/`.api-sessao.env`/`painel.html` não estão no `.gitignore` de um workspace com git; **recálculo**: no Linux o timeout só matava o `soffice` e a espera ficava presa ao filho, agora mata o grupo de processos (`tests/test_recalc.py`); `api_pedido --saida` relativo ao workspace, como nos outros scripts; `scripts/sincronizar_skills.py` para a cópia global das skills (antes manual, sem verificação); hook `testes` do pre-commit também para skills, `CLAUDE.md`, demo e template; docs alinhadas com o que existe (`qa_core`, CLI `qa` e `executor-web` marcados como planeados; códigos 4 e 5 do `aplicar` na técnica Excel); `docs/heuristicas.md` criado com as heurísticas dos 3 bugs do demo. 95 → 117 testes. |
| 2026-09-24 | Revisão geral 2 em `a57ed19`. Fase 7 (fecho da framework) acrescentada com o que falta para a v1.0: CI no Linux, piloto, golden real, `historico/` protegido, versões fixas, demo reproduzível, decisões de âmbito. |
| 2026-09-24 | 7.1 concluída: CI verde no Linux depois da revisão geral 2 (run 36053764682), com o timeout do recálculo verificado em Ubuntu. |
| 2026-09-24 | 7.4 (`3563f24`), 7.5 (`0a61bc6`) e 7.6 (`ced85fc`) concluídas: `historico/` protegido no `.claude/settings.json` do workspace, versões fixas (Playwright MCP e dependências Python), demo reproduzível com teste no job `golden`. |
| 2026-09-24 | 7.7 concluída (`3ffbb62`), decisões do utilizador: sem `qa_core`/CLI `qa`; `executor-web` adiado e reavaliado no piloto; `jira` e `azure-devops` em registo manual (metodologia §4.2). A 7.2 ganhou os passos de reavaliar o `executor-web` e de confirmar a proteção de `historico/`. |
| 2026-09-24 | 7.8 concluída (`799e068`): `scripts/comum.py` com a leitura do `projeto.yaml` e do `.env` e a redação, sem mudança de comportamento. A 1.ª tentativa de commit foi bloqueada pelo detect-secrets (falso positivo: os nomes das constantes de redação numa nota deste plano); corrigido o texto, sem allowlist. |
| 2026-09-24 | `testar_mobile.py`: um flow indicado que não existe passa a ser erro de uso (código 2, nada corrido, com sugestões dos flows do mesmo caso), em vez de `Blocked` "o flow nao correu", que parecia um problema de ambiente. Cada flow pode ser um caminho (pasta atual, workspace ou pasta dos flows) ou o ID do caso (o único `<ID>_*.yaml`). 3 testes novos; skill `/testar-mobile` e `CLAUDE.md` atualizados. |
| 2026-09-24 | 7.9 pedida pelo utilizador: os critérios dela ainda não se cumprem (faltam 7.2 e 7.3). Por decisão do utilizador, adiantou-se o 1.º critério da 7.3, o golden real com `--dir`/`QA_GOLDEN_DIR` (`e74c281`), e a v1.0 fica para depois do piloto. |
