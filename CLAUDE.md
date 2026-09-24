# QA Assistant

Referências:
- @docs/metodologia-core.md — metodologia universal
- @docs/tecnica-browser.md — técnica de automação browser
- @docs/tecnica-excel.md — técnica de automação de workbooks Excel
- docs/tecnica-api.md — técnica de testes de API (ler quando o módulo `api` estiver ativo; `/testar-api`)
- docs/tecnica-acessibilidade.md — técnica de acessibilidade WCAG 2.1 AA (ler quando o módulo `acessibilidade` estiver ativo; `/auditar-acessibilidade`)
- docs/tecnica-mobile.md — técnica mobile com Maestro (ler quando o módulo `mobile` estiver ativo; `/testar-mobile`)
- docs/heuristicas.md — padrões de falha anonimizados para o error guessing (metodologia §17; ler ao gerar casos)
- docs/plano-desenvolvimento.md — roadmap da framework (ler em sessões de desenvolvimento)

## Regras fixas
- Nunca escrever dados de clientes (URLs, contas, nomes, credenciais) neste repositório.
- Seguir a hierarquia de fontes de verdade da metodologia-core (secção 1.3).
- Um workspace de projeto fica sempre fora deste repositório, em qa-workspaces/.
- Antes de qualquer alteração à framework, seguir o processo de validação (metodologia-core secção 15).

## Comandos disponíveis
/onboarding-projeto · /analisar-funcionalidade · /gerar-casos · /planear-lote
/executar-lote · /registar-bug · /checkpoint · /fim-sessao · /painel
/testar-api · /auditar-acessibilidade · /testar-mobile

## Scripts (a partir da raiz do qa-assistant)
- `python scripts/validar_projeto.py <workspace>` — valida o `projeto.yaml` (ambientes, módulos, secções, URLs dentro dos ambientes, Excel, painel; avisa se `.env` e afins não estão no `.gitignore`); 0 ok · 1 problemas
- `python -m adapters.excel.inspecionar <cópia.xlsx>` — propõe a secção `excel:` (onboarding)
- `python -m adapters.excel.aplicar <workspace> <lote.json>` — aplica um lote ao workbook (0 ok · 2 cross-check · 3 recálculo · 4 lote inválido · 5 resumo não bate)
- `python scripts/api_pedido.py <workspace> <MÉTODO> <caminho> …` — um pedido HTTP com evidência redigida (0 ok · 1 código diferente · 2 uso · 3 sem resposta)
- `python scripts/auditar_a11y.py <workspace> [--saida …]` — axe-core nas páginas do `projeto.yaml` (0 ok · 2 configuração/ferramenta · 3 página não auditada); instalar antes `npm ci --prefix tools/a11y`
- `python scripts/testar_mobile.py <workspace> [flows…] [--saida …]` — corre os flows Maestro e liga-os aos casos; cada flow é um caminho ou o ID do caso (0 ok · 2 configuração ou flow inexistente · 3 sem alvo)
- `python scripts/gerar_painel.py <workspace> [--abrir]` — gera (e abre) o `painel.html` do workspace; é o que o `/painel` corre
- `python scripts/sincronizar_skills.py [--aplicar]` — compara (e copia) `.claude/skills/` com `~/.claude/skills/`, a cópia que os workspaces usam; correr com `--aplicar` depois de mudar uma skill (0 sincronizadas · 1 diferenças)
- Testes: `python -m unittest discover -s tests` e `python scripts/golden_check.py` (precisa de LibreOffice); golden real de um workspace: `python scripts/golden_check.py --dir <workspace>/golden/<lote>` (metodologia §15)

## Ambiente
Repositório pessoal. Os workspaces de cada empresa vivem em ../qa-workspaces/<empresa>/<projeto>/.
