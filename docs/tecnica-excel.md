# Técnica Excel

> **Camada 2 — Técnica de automação de workbooks Excel**
> Versão 1.0 · 2026-09-22 · Extraída de um `qa_lib.py` real, usado num projeto de cliente.
>
> Agnóstica de projeto: nenhum nome de sheet, coluna ou estado específico de um workbook real vive aqui. Subordinada a `metodologia-core.md`.

---

## 0. Âmbito

Cobre a automação de workbooks Excel usados como fonte de verdade de casos de teste e defeitos (via `openpyxl`), incluindo o recálculo seguro de fórmulas. Cada projeto tem o seu próprio adaptador (`adapters/excel/<projeto>.py` ou, enquanto não migrado, um `scripts/qa_lib.py` no workspace) que mapeia o layout real do seu workbook para este padrão — os nomes de sheets, colunas e estados vivem **só** no workspace do projeto.

## 1. Fluxo de aplicação de um lote

O padrão que funciona na prática, por esta ordem, nunca escrevendo linha a linha durante a execução (metodologia §7.1):

1. **Carregar** o workbook preservando fórmulas (`data_only=False`) — só se lê com `data_only=True` numa cópia à parte, quando se quer apenas os valores já calculados (ex.: conferir totais do Resumo).
2. **Aplicar o lote** a partir do registo leve (metodologia §7.1), atualizando estado, resultado obtido e observações por caso, e criando os defeitos novos que o lote traga.
3. **Regenerar a aba de bugs/defeitos** a partir do estado atual dos casos, em vez de a editar à mão.
4. **Cross-check** (metodologia §11): validar estados válidos, IDs duplicados, referências partidas. Devolve uma lista de problemas (vazia = tudo bem).
5. **Só gravar por cima do original se o cross-check devolver zero problemas.** Se houver problemas, gravar à parte (ex.: `<nome>.REVISTO.xlsx`) para inspeção, sem tocar no ficheiro oficial.
6. **Recalcular fórmulas em segurança** (§2) só depois de gravar.

Este é o mesmo padrão de "validar antes de adotar" da metodologia (§15), aplicado à escrita do workbook em vez de a uma alteração de código.

## 2. Recalcular fórmulas em segurança

**Regra de ouro, descoberta a doer:** nunca recalcular um workbook a correr diretamente num caminho dentro de uma pasta sincronizada ou montada (pasta conectada do Cowork, OneDrive, uma unidade de rede, qualquer montagem FUSE). O LibreOffice fica pendurado indefinidamente nesse filesystem, de forma 100% reprodutível — não é o ficheiro, é o tipo de montagem.

**Padrão correto:** copiar sempre o ficheiro para uma pasta temporária local antes de recalcular, recalcular lá, e só depois copiar o resultado de volta.

O mecanismo concreto de recálculo depende do ambiente, e **não é portátil por si só**:

| Ambiente | Como recalcular |
|---|---|
| Sessão Claude.ai / Cowork | `recalc.py` da skill `xlsx` interna — só funciona dentro dessas sessões |
| Claude Code local, com LibreOffice instalado | `soffice --headless --convert-to xlsx --calc-recalc <ficheiro>`, sempre sobre a cópia em `/tmp` (ou equivalente local) |
| Claude Code local, no Windows, com Excel instalado | Automação COM (`win32com.client`), abrindo o ficheiro, forçando `Application.CalculateFull()` e gravando |

Ao migrar um `qa_lib.py` de projeto de uma sessão Cowork para Claude Code local, **o mecanismo de recálculo tem de ser reescrito** para o ambiente novo — o resto da lógica (carregar, aplicar lote, cross-check) mantém-se.

## 3. Formatação: evitar o mesmo tipo de bloqueio

**Nunca definir `row_dimensions[...].height` manualmente numa linha que tenha `wrap_text=True` numa coluna larga.** Provoca o mesmo sintoma de bloqueio do LibreOffice que o recálculo em pasta montada. Deixar a altura da linha em automático — não definir nada.

## 4. Formato do lote (registo leve → aplicação)

O registo leve (metodologia §7.1) que um lote de execução produz tem de conter, no mínimo, por caso: o ID do caso, o estado, o resultado obtido (o que se observou de facto, não uma repetição do esperado) e, quando houver um defeito novo, os dados desse defeito (título, descrição, severidade, caso relacionado, referência ao requisito).

O estado de um caso usa sempre a lista de estados válidos **desse projeto** (definida no `projeto.yaml` ou no adaptador), nunca uma palavra inventada na hora. Diferentes projetos podem usar vocabulários diferentes para os mesmos conceitos (por exemplo, um projeto real observado usa os estados em português "Não Executado / Passou / Falhou / Bloqueado / N/A" para casos, e uma lista mais rica para defeitos: "Aberto / Em Análise / Em Correção / Corrigido / Reaberto / Fechado / Não é um Defeito") — este documento não impõe um vocabulário; impõe que o vocabulário seja explícito e verificado no cross-check (§1, passo 4).

## 5. Consistência entre resultados e defeitos

Ao regenerar a aba de bugs a partir dos casos (em vez de a editar à mão), aplicam-se as mesmas regras de duplicados da metodologia (§9.1, §10): um defeito por problema distinto, atualizar em vez de duplicar, e um defeito cujo estado corresponda a "resolvido" segue a regra do projeto (remover da lista ativa ou arquivar — metodologia §9.3, configurável em `projeto.yaml`).

Um lote **nunca deve poder ser aplicado duas vezes** sem detecção — reaplicar o mesmo lote não pode duplicar notas em observações nem criar defeitos a dobrar. Se o adaptador não verificar isto automaticamente, é responsabilidade de quem executa não reaplicar um lote já processado (o `contexto.md` do workspace regista até onde se chegou — metodologia §12.2).

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
