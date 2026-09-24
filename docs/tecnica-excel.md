# Técnica Excel

> **Camada 2 — Técnica de automação de workbooks Excel**
> Versão 1.1 · 2026-09-23 · Extraída de um `qa_lib.py` real, usado num projeto de cliente. Implementada no adaptador universal `adapters/excel/`.
>
> Agnóstica de projeto: nenhum nome de sheet, coluna ou estado específico de um workbook real vive aqui. Subordinada a `metodologia-core.md`.

---

## 0. Âmbito

Cobre a automação de workbooks Excel usados como fonte de verdade de casos de teste e defeitos (via `openpyxl`), incluindo o recálculo seguro de fórmulas. A implementação é o **adaptador universal** `adapters/excel/base.py` (`ExcelAdapter`), que não contém nenhum nome de sheet, coluna ou estado: cada projeto dá-lhe uma **configuração** com o layout real do seu workbook (ver `adapters/excel/exemplo.yaml`, fictício). Os nomes de sheets, colunas e estados vivem **só** no workspace do projeto. Projetos ainda não migrados podem manter um `scripts/qa_lib.py` próprio no workspace.

A configuração vive na secção `excel:` do `projeto.yaml` do workspace (campos comentados no template `docs/projeto.yaml`) e lê-se com `ExcelAdapter.from_projeto(<workspace>)`. No onboarding, preenche-se inspecionando o workbook real com `python -m adapters.excel.inspecionar <cópia.xlsx>`, que só lê o ficheiro e propõe a secção: o que não consegue detetar com segurança fica como `CONFIRMAR: …` para o utilizador decidir.

## 1. Fluxo de aplicação de um lote

O padrão que funciona na prática, por esta ordem, nunca escrevendo linha a linha durante a execução (metodologia §7.1):

1. **Carregar** o workbook preservando fórmulas (`data_only=False`) — só se lê com `data_only=True` numa cópia à parte, quando se quer apenas os valores já calculados (ex.: conferir totais do Resumo).
2. **Aplicar o lote** a partir do registo leve (metodologia §7.1), atualizando estado, resultado obtido e observações por caso, e criando os defeitos novos que o lote traga.
3. **Regenerar a aba de bugs/defeitos** a partir do estado atual dos casos, em vez de a editar à mão.
4. **Cross-check** (metodologia §11): validar estados válidos, IDs duplicados, referências partidas. Devolve uma lista de problemas (vazia = tudo bem).
5. **Só gravar por cima do original se o cross-check devolver zero problemas.** Se houver problemas, gravar à parte (ex.: `<nome>.REVISTO.xlsx`) para inspeção, sem tocar no ficheiro oficial.
6. **Recalcular fórmulas em segurança** (§2) só depois de gravar.

Este é o mesmo padrão de "validar antes de adotar" da metodologia (§15), aplicado à escrita do workbook em vez de a uma alteração de código.

No adaptador: `load()` → `apply_lote()` → `regenerate_active_defects()` → `save_if_clean()` (inclui o `cross_check()`) → `recalc()`. O `load()` falha logo se a configuração não corresponder ao workbook (sheet ou coluna obrigatória em falta), e o `apply_lote()` verifica todos os IDs de caso antes de escrever o que quer que seja. As colunas opcionais de caso (resultado obtido, observações) só se usam nas sheets onde existem.

Para correr o fluxo completo num workspace: `python -m adapters.excel.aplicar <workspace> <lote.json>` (a partir da raiz do qa-assistant). Sai com 0 (gravado, ou lote já aplicado), 2 (cross-check com problemas: original intacto, `.REVISTO.xlsx` à parte) ou 3 (gravado, mas o recálculo falhou).

## 2. Recalcular fórmulas em segurança

**Regra de ouro, descoberta a doer:** nunca recalcular um workbook a correr diretamente num caminho dentro de uma pasta sincronizada ou montada (pasta conectada do Cowork, OneDrive, uma unidade de rede, qualquer montagem FUSE). O LibreOffice fica pendurado indefinidamente nesse filesystem, de forma 100% reprodutível — não é o ficheiro, é o tipo de montagem.

**Padrão correto:** copiar sempre o ficheiro para uma pasta temporária local antes de recalcular, recalcular lá, e só depois copiar o resultado de volta.

O mecanismo concreto de recálculo depende do ambiente, e **não é portátil por si só**:

| Ambiente | Como recalcular |
|---|---|
| Sessão Claude.ai / Cowork | `recalc.py` da skill `xlsx` interna — só funciona dentro dessas sessões |
| Claude Code local, com LibreOffice instalado (**por omissão**) | `soffice --headless --convert-to xlsx` com um **perfil de utilizador isolado** (`-env:UserInstallation=…`) configurado com `OOXMLRecalcMode=0` (recalcular sempre ao abrir); sem isto o LibreOffice pode manter os valores em cache. Sempre sobre a cópia local, com timeout que mata a árvore de processos |
| Claude Code local, no Windows, com Excel desktop instalado (opcional) | Automação COM (`pywin32`) num processo filho (para o timeout ser efetivo): abrir, `CalculateFullRebuild()`, gravar |

Ao migrar um `qa_lib.py` de projeto de uma sessão Cowork para Claude Code local, **o mecanismo de recálculo tem de ser reescrito** para o ambiente novo — o resto da lógica (carregar, aplicar lote, cross-check) mantém-se.

No adaptador: `adapters/excel/recalc.py`, `recalc_safe(path, mecanismo="libreoffice" | "excel-com", timeout)`, escolhido pela configuração (`recalc:`). Devolve `{"ok": True}` ou `{"ok": False, "erro": …}` e, em caso de erro, o original fica intacto.

Ao regravar o ficheiro, o LibreOffice introduz diferenças sem efeito visual ou funcional (ex.: o byte de transparência das cores passa de `00` para `FF`; referências de autofiltro perdem os `$`; literais de erro em fórmulas já partidas passam a minúsculas). Não se confunda isto com perda de dados — mas também não se afirme "formatação preservada" sem comparar o que foi efetivamente comparado.

## 3. Formatação: evitar o mesmo tipo de bloqueio

**Nunca definir `row_dimensions[...].height` manualmente numa linha que tenha `wrap_text=True` numa coluna larga.** Provoca o mesmo sintoma de bloqueio do LibreOffice que o recálculo em pasta montada. Deixar a altura da linha em automático — não definir nada.

## 4. Formato do lote (registo leve → aplicação)

O registo leve (metodologia §7.1) que um lote de execução produz tem de conter, no mínimo, por caso: o ID do caso, o estado, o resultado obtido (o que se observou de facto, não uma repetição do esperado) e, quando houver um defeito novo, os dados desse defeito (título, descrição, severidade, caso relacionado, referência ao requisito).

No adaptador, o lote é `{"resultados": [item, …]}`, cada item `{"tc", "estado", "resultado_obtido", "defeito_novo", "bug"}`:

- `defeito_novo` é `null` ou um dicionário cujas chaves são mapeadas para colunas pela configuração (`defeitos.campos_lote`); `tc_relacionado` (por omissão o próprio caso), `severidade` e `prioridade` (por omissão a severidade) têm papel próprio.
- `bug` (opcional) é o ID de um defeito **já existente** associado ao caso (reteste, duplicado): acrescenta às observações do caso a nota `notas.defeito_existente` (por omissão `Defeito associado: {id}.`) e não cria defeito.
- Outras chaves (ex. `ts`, `nota`) são ignoradas pelo adaptador.

Antes de escrever o que quer que seja, o adaptador **recusa o lote inteiro** (erro com a lista de problemas) se: o lote estiver vazio; um item não tiver `tc` ou `estado`; o mesmo caso aparecer duas vezes; um estado não estiver em `casos.estados` (inclui `Dúvida`, que se resolve antes de aplicar — metodologia §8.5); um `bug` não existir na sheet de defeitos; ou, havendo lista de `prioridades`, a prioridade de um defeito novo estiver fora dela.

O estado de um caso usa sempre a lista de estados válidos **desse projeto** (definida no `projeto.yaml` ou no adaptador), nunca uma palavra inventada na hora. Diferentes projetos podem usar vocabulários diferentes para os mesmos conceitos (por exemplo, um projeto real observado usa os estados em português "Não Executado / Passou / Falhou / Bloqueado / N/A" para casos, e uma lista mais rica para defeitos: "Aberto / Em Análise / Em Correção / Corrigido / Reaberto / Fechado / Não é um Defeito") — este documento não impõe um vocabulário; impõe que o vocabulário seja explícito e verificado no cross-check (§1, passo 4).

## 5. Consistência entre resultados e defeitos

Ao regenerar a aba de bugs a partir dos casos (em vez de a editar à mão), aplicam-se as mesmas regras de duplicados da metodologia (§9.1, §10): um defeito por problema distinto, atualizar em vez de duplicar, e um defeito cujo estado corresponda a "resolvido" segue a regra do projeto (remover da lista ativa ou arquivar — metodologia §9.3, configurável em `projeto.yaml`).

Um lote **nunca deve poder ser aplicado duas vezes** sem detecção — reaplicar o mesmo lote não pode duplicar notas em observações nem criar defeitos a dobrar. O adaptador verifica isto automaticamente: guarda a impressão digital (SHA-256 do conteúdo) de cada lote aplicado numa sheet de controlo escondida no próprio workbook (por omissão `_qa_lotes`, configurável em `controlo_lotes.sheet`). Reaplicar o mesmo lote devolve `{"aplicado": False, "motivo": …}` e não altera nada.

A impressão digital só reconhece conteúdo **igual**. Um lote editado depois de aplicado (ex.: corrigir uma gralha no resultado obtido) teria outro hash e seria aplicado outra vez: notas repetidas nas observações e defeitos novos em duplicado. Por isso a sheet de controlo guarda também a identidade do lote (`lote_id`; o `aplicar` usa o nome do ficheiro sem extensão), e um lote com um `lote_id` já aplicado mas com conteúdo diferente é **recusado** (código 4 no `aplicar`, nada escrito). Uma correção faz-se num lote novo, com outro nome, só com os casos a corrigir (e com `bug` em vez de `defeito_novo` para um defeito que já foi criado). Sheets de controlo antigas, sem a coluna `lote_id`, ganham-na na aplicação seguinte. Um `scripts/qa_lib.py` de projeto que não faça esta verificação deixa a responsabilidade a quem executa: não reaplicar um lote já processado (o `contexto.md` do workspace regista até onde se chegou — metodologia §12.2).

O cross-check do adaptador verifica também que cada defeito aponta para um caso que existe (só conta como referência o texto que começa por um prefixo de caso configurado).

## 6. Exportação para outras equipas

Quando o workbook oficial não é o formato que outra equipa (ex.: desenvolvimento) precisa de consultar, gera-se uma exportação derivada e descartável a partir do workbook oficial — nunca o contrário. O workbook oficial se mantém como única fonte de verdade (metodologia §1.3); a exportação é sempre regenerável.

## 7. Portabilidade entre ambientes

O `qa_lib.py`/adaptador de cada projeto tende a nascer numa sessão concreta (Claude.ai, Cowork, Claude Code local) e a acumular ali dependências específicas desse ambiente — caminhos de scripts internos, mecanismos de recálculo, bibliotecas só instaladas nesse sítio. Ao trazer esse adaptador para o Claude Code local:

1. Confirmar quais as suas dependências de ambiente (normalmente, o recálculo — §2).
2. Reescrever só essas partes para o novo ambiente.
3. Manter a lógica de negócio (mapeamento de sheets/colunas, regras de cross-check) inalterada, e validar com o teste golden da metodologia (§15) antes de confiar na versão portada.

---

## Origem

Este documento generaliza duas lições e um fluxo de trabalho descobertos durante uma sessão real de testes noutro projeto (sessão Cowork de setembro de 2026), documentados originalmente num `qa_lib.py` e `README.md` específicos desse projeto. O código e os nomes concretos de sheets/colunas/estados desse projeto ficam no respetivo workspace — aqui fica só o que é reutilizável em qualquer projeto com workbooks Excel.
