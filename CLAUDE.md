# QA Assistant

[![CI](https://github.com/palmeida98/qa-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/palmeida98/qa-assistant/actions/workflows/ci.yml)

Referências:
- @docs/metodologia-core.md — metodologia universal
- @docs/tecnica-browser.md — técnica de automação browser
- @docs/tecnica-excel.md — técnica de automação de workbooks Excel
- docs/plano-desenvolvimento.md — roadmap da framework (ler em sessões de desenvolvimento)

## Regras fixas
- Nunca escrever dados de clientes (URLs, contas, nomes, credenciais) neste repositório.
- Seguir a hierarquia de fontes de verdade da metodologia-core (secção 1.3).
- Um workspace de projeto fica sempre fora deste repositório, em qa-workspaces/.
- Antes de qualquer alteração à framework, seguir o processo de validação (metodologia-core secção 15).

## Comandos disponíveis
/onboarding-projeto · /analisar-funcionalidade · /gerar-casos · /planear-lote
/executar-lote · /registar-bug · /checkpoint · /fim-sessao

## Scripts (a partir da raiz do qa-assistant)
- `python -m adapters.excel.inspecionar <cópia.xlsx>` — propõe a secção `excel:` (onboarding)
- `python -m adapters.excel.aplicar <workspace> <lote.json>` — aplica um lote ao workbook (0 ok · 2 cross-check · 3 recálculo · 4 lote inválido · 5 resumo não bate)
- `python scripts/gerar_painel.py <workspace>` — gera o `painel.html` do workspace
- Testes: `python -m unittest discover -s tests` e `python scripts/golden_check.py` (precisa de LibreOffice)

## Ambiente
Repositório pessoal. Os workspaces de cada empresa vivem em ../qa-workspaces/<empresa>/<projeto>/.
