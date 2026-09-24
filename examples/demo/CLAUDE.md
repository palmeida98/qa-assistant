# demo-saucedemo — demo

Workspace de demonstração do qa-assistant, dentro do próprio repositório (`examples/demo/`).
Serve para desenvolver e validar a framework sem dados de clientes, e é a base do portefólio.
Tudo aqui é fictício ou público.

Referencias universais:
- @../../docs/metodologia-core.md
- @../../docs/tecnica-browser.md
- @../../docs/tecnica-excel.md

## Ambientes
- Teste: https://www.saucedemo.com/ — site público de prática de testes. Não há produção.
- API (módulo `api`): https://restful-booker.herokuapp.com — API pública de prática; documentação em `/apidoc` (fonte do esperado). Não há produção.
- Respeitar os dois sites: sem testes de carga nem de segurança ativos; ritmo normal de utilizador.

## Roles / perfis
Contas de demonstração publicadas pelo próprio site (comportamento documentado na página de login):
- `standard_user` — comportamento normal; é a referência do resultado esperado
- `locked_out_user` — conta bloqueada
- `problem_user` — erros visuais e funcionais intencionais
- `performance_glitch_user` — respostas lentas
- `error_user` — erros em algumas ações
- `visual_user` — diferenças visuais

Variáveis de credenciais (valores só no `.env` local, nunca aqui):
- `DEMO_SAUCE_<ROLE>_USER` para cada role (ex.: `DEMO_SAUCE_STANDARD_USER`)
- `DEMO_SAUCE_PASSWORD` (a mesma palavra-passe de demonstração para todas as contas)
- API: `DEMO_BOOKER_USER` e `DEMO_BOOKER_PASSWORD` (conta admin publicada na documentação da API). O token vai só para `.api-sessao.env` (ignorado pelo git), via `api_pedido.py --guardar-segredo`.

## Documentacao de referencia
- Não há especificação: o workbook `workbook/plano-demo.xlsx` (casos aprovados a 23.09.2026) é a fonte de verdade.
- O resultado esperado de cada caso é o comportamento normal do site (`standard_user`), com as mensagens da interface em inglês. Se um esperado não bater certo com o site, o caso fica em `Dúvida`, não em `Fail`.

## Estrutura do workbook / tracker
- Tracker: o próprio workbook Excel; layout na secção `excel:` do `projeto.yaml` (lido pelo adaptador `adapters/excel/`).
- `workbook/plano-demo.xlsx` (gerado por `gerar_workbook.py`):
  - **Resumo** — totais por área e estado (fórmulas; não editar à mão)
  - **Login**, **Produtos**, **Checkout** — casos `CT-LOGIN-…`, `CT-PROD-…`, `CT-CHK-…`
  - **API** — casos `CT-API-…` (restful-booker; `/testar-api`)
  - **Acessibilidade** — casos `CT-A11Y-…` (WCAG 2.1 AA; `/auditar-acessibilidade`)
  - **Defeitos** — registo completo (`BUG-{n}`), incluindo resolvidos
  - **Defeitos Ativos** — vista regenerada pelo adaptador (só abertos)
- Estados de caso: `Por testar`, `Pass`, `Fail`, `Blocked`, `N/A` (os da metodologia §2)
- Estados de defeito: `Aberto`, `Em análise`, `Em correção`, `Corrigido`, `Reaberto`, `Fechado`, `Não é defeito` (fechados: `Corrigido`, `Fechado`, `Não é defeito`)
- Registo leve de sessão: `runs/` (`registo_leve` no `projeto.yaml`)
- O workbook é reconstruível: `python examples/demo/reconstruir_workbook.py` (a partir da raiz do qa-assistant) gera-o de novo e reaplica os lotes de `runs/` pela ordem de execução. Usar sempre que se acrescentam áreas ou casos ao `gerar_workbook.py`, nunca editar o workbook à mão.

## Formato de bug
- ID: `BUG-{n}` (o adaptador calcula o próximo número)
- Campos: colunas da sheet Defeitos — Título, Descrição (passos, esperado → observado), Área, Caso, Role, Severidade, Prioridade, Estado, Reportado por, Data
- Severidade e Prioridade: `Crítica`, `Alta`, `Média`, `Baixa`
- Bugs resolvidos: **remover** da vista Defeitos Ativos; o registo completo fica em Defeitos

## Regras especificas deste projeto
- Política de evidência: **texto** (screenshot só quando o problema é visual).
- Módulos ativos: `funcional-web`, `exploratorio`, `api`, `acessibilidade`.
  - `api`: o saucedemo não tem API; o módulo usa a API de prática restful-booker (secção `api:` do `projeto.yaml`).
  - `acessibilidade`: auditoria automática com `scripts/auditar_a11y.py` (páginas na secção `acessibilidade:` do `projeto.yaml`) e manual guiada.
- Nada do demo pode passar a denylist nem o detect-secrets: nem a palavra-passe de demonstração vai para o repositório.
