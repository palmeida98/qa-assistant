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
| 2 | Browser automation com Playwright MCP, demo público, piloto real | Em curso (2.1 concluída) |
| 3 | Novos módulos de técnica: API, acessibilidade, mobile | Por fazer |
| 4 | CI da própria framework | Em curso (4.1 concluída; 4.2 depende da 2.5) |
| 5 | Interface: painel de progresso por workspace | Em curso (5.1 concluída) |
| 6 | Portefólio: README, demo, decisão sobre visibilidade do repositório | Por fazer |

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

### 2.2 Playwright MCP no onboarding — `0024c37` (critérios 2 e 3 por confirmar pelo utilizador)
Acrescentar à skill `onboarding-projeto` o registo do Playwright MCP no workspace:
`claude mcp add playwright --scope project -- npx @playwright/mcp@latest`

Critérios de aceitação:
- [x] `.mcp.json` criado no workspace (não no repositório da framework, exceto no demo).
- [ ] `/mcp` dentro do `claude` mostra `playwright` ligado. *(verificação do utilizador numa sessão nova em `examples/demo/`: aprovar o servidor e correr `/mcp`)*
- [ ] Um login no site de demonstração funciona pelo MCP. *(verificação do utilizador, na mesma sessão, com `standard_user` lido do `.env`)*

Notas:
- No Windows o comando é `claude mcp add playwright --scope project -- cmd /c npx @playwright/mcp@latest`; sem o `cmd /c` o Claude Code não lança o `npx`. A skill escolhe o comando conforme o sistema.
- `@latest` mantido por agora; fixar a versão na passagem para N2 (2.5). Versão verificada nesta máquina: 0.0.82.
- `claude mcp list` mostra o servidor como `Pending approval` até ser aprovado no primeiro arranque do `claude` no workspace.

### 2.3 Primeiro lote no demo
Executar 5–10 casos do demo com `/planear-lote` e `/executar-lote`, aplicar pelo adaptador, `/checkpoint`.

Critérios de aceitação:
- [ ] Cross-check limpo.
- [ ] Um defeito fictício registado com `/registar-bug` no formato do `projeto.yaml`.
- [ ] `contexto.md` do demo atualizado.

### 2.4 Piloto num projeto real
*Estado (23.09.2026): preparado no workspace do projeto (Playwright MCP registado, lote de 16 casos só de consulta proposto, capturas fora do git); execução à espera de 2.2/2.3 e das credenciais. Detalhe no `contexto.md` desse workspace.*

No workspace do projeto real (fora deste repositório): `.env` preenchido localmente, Playwright MCP registado, lote de 10–20 casos já conhecidos, comparação com os resultados anteriores. Todo o registo fica no `contexto.md` desse workspace.

Aqui só se marca feito e, se surgirem, lições **genéricas** para a metodologia ou técnica (sem nomes).

- [ ] Piloto concluído e comparado.
- [ ] Lições genéricas incorporadas (se houver).

### 2.5 Promoção N1 → N2
A partir de um caso do demo que passou pelo agente (N1), gerar um teste determinístico Playwright (`.spec.ts`) conforme `tecnica-browser.md` §13.

Critérios de aceitação:
- [ ] `npx playwright test` corre e passa localmente.
- [ ] Localizadores seguem a prioridade da `tecnica-browser.md` §4.2.

---

## Fase 3 — Novos módulos de técnica

Cada módulo: um documento em `docs/` (mesma estrutura dos existentes: âmbito, níveis de execução, preparação, verificação, evidência, segurança, ligação à metodologia, checklist) + uma skill + um exemplo no demo.

### 3.1 API — `docs/tecnica-api.md` + `/testar-api`
Contratos e schemas, códigos de estado, autenticação e autorização por role, casos negativos, idempotência, paginação, limites. Ferramentas: `request` do Playwright, curl, coleções Postman quando o projeto já as tenha. Evidência: pedido/resposta com segredos redigidos.

### 3.2 Acessibilidade — `docs/tecnica-acessibilidade.md` + `/auditar-acessibilidade`
WCAG 2.1 AA. Parte automática com axe-core via Playwright; parte manual guiada (teclado, ordem de foco, leitor de ecrã, contraste, zoom 200%, reflow). Cada falha mapeada ao critério WCAG correspondente e ao nível.

### 3.3 Mobile — `docs/tecnica-mobile.md` + `/testar-mobile`
Flows Maestro em YAML, emulador vs. dispositivo real, gestos, permissões, deep links, estados offline/background, diferenças iOS/Android.

### 3.4 Módulos no `projeto.yaml`
Lista fechada de valores válidos em `modulos:` (`funcional-web`, `api`, `acessibilidade`, `mobile`, `exploratorio`) e `onboarding-projeto` a perguntar quais ativar.

Critérios de aceitação (para cada módulo):
- [ ] Documento agnóstico de projeto, passa os guards.
- [ ] Skill funcional e visível com `/`.
- [ ] Exemplo executado no demo.
- [ ] Referência acrescentada ao `CLAUDE.md`.

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

### 4.2 Regressão do demo
Correr os `.spec.ts` do demo (2.5) no CI contra o site público.

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

### 5.2 Skill `/painel`
Gera e abre o painel do workspace atual.

Critérios de aceitação (da 5.1):
- [x] Painel do demo gerado e coerente com o workbook (totais batem).
- [x] Funciona offline, um único ficheiro HTML.

---

## Fase 6 — Portefólio

### 6.1 README
Arquitetura em camadas, fronteira de privacidade, como começar (onboarding de um workspace novo), demo, módulos disponíveis.

### 6.2 Visibilidade do repositório
Decidir se o repositório passa a público. Antes disso, obrigatório:
- [ ] Varrer **todo o histórico** com detect-secrets e com a denylist (não só o estado atual), porque os primeiros commits foram feitos antes de os guards existirem.
- [ ] Rever `examples/demo/` à procura de qualquer dado não fictício.

---

## Registo de alterações a este plano

| Data | Alteração |
|---|---|
| 2026-09-23 | Versão inicial |
| 2026-09-24 | Revisão geral (`994fa8b`): denylist passa a ler ficheiros Office e nomes de ficheiros (antes um `.xlsx` de cliente passava o guard; histórico verificado, limpo); adaptador valida o lote inteiro antes de escrever (`validar_lote`, código 4 no `aplicar`) e aceita `bug` existente; um só formato de registo leve (metodologia §7.1 = técnica-excel §4); 27 testes `unittest` no CI e no pre-commit (inclui a verificação do painel da 5.1); ruff no pre-commit; skills alinhadas com o fluxo. |
| 2026-09-24 | Lote editado depois de aplicado: antes era reaplicado (hash diferente) e duplicava notas e defeitos; agora a sheet de controlo guarda o `lote_id` (nome do ficheiro) e o adaptador recusa-o (código 4). Verificado: o `main` não pode ser protegido (repositório privado, plano Free: API 403); o CI é aviso depois do push, não barreira — a decidir na 6.2. |
| 2026-09-24 | Resumo vs contagem direta: bloco opcional `excel.aba_resumo`; `verificar_resumo()` compara os valores recalculados do resumo com a contagem caso a caso (por área, estado, total e linha de total); código 5 no `aplicar`, alerta no painel, parte do golden. Os totais nunca se leem do resumo. Detetados nos testes: fórmula a contar a coluna errada e caso fora do intervalo fixo (o demo usa `A2:A200`). |
| 2026-09-24 | Definição de "executado" (decisão do utilizador): todo o caso fora de `Por testar`; N/A e Blocked contam como executados; % executada = executados / total. Escrita na metodologia §16.3; fórmula do resumo do demo alinhada (antes excluía os N/A) e workbook regenerado (só essas fórmulas e a nota mudaram). |
