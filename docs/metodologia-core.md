# Metodologia Core

> **Camada 1 — Metodologia universal de QA**
> Versão 2.0 · 2026-09-22 · Substitui a versão usada no Claude.ai Projects.
>
> Este documento aplica-se a **todos** os projetos e a **todas** as empresas. Não contém, nem pode conter, informação específica de um projeto, cliente ou empresa. Os exemplos usam um projeto fictício, a **Loja Demo**.

---

## 0. Como usar este documento

- O Claude lê este documento como referência permanente (importado pelo `CLAUDE.md` do repositório `qa-assistant`).
- O que está aqui é **o método**: o que se faz, por que ordem e com que critérios.
- O **como tecnicamente** está nos módulos de teste (camada 2), por exemplo `tecnica-browser.md`.
- O **o quê de cada projeto** está no workspace do projeto (camada 4): `CLAUDE.md`, `projeto.yaml` e `contexto.md`.
- Quando uma regra deste documento remete para "a regra do projeto", o valor está no `projeto.yaml`. Se não estiver definido, **perguntar** e não assumir.

---

## 1. Arquitetura

### 1.1 Camadas

| Camada | Conteúdo | Onde vive |
|---|---|---|
| **Motor** | `qa_core` (modelo de dados, pipeline, validação, CLI `qa`), skills, subagentes e hooks | Repositório pessoal `qa-assistant` |
| **1. Metodologia** | Este documento | `qa-assistant/docs/` |
| **2. Módulos de teste** | Técnicas por tipo de teste (`tecnica-browser.md`, `modulos/acessibilidade.md`, …) | `qa-assistant/docs/` |
| **3. Adaptadores** | Conversão entre o modelo interno e as ferramentas de cada empresa (Excel, Azure DevOps, Jira, Figma) | `qa-assistant/adapters/` |
| **4. Workspace** | Contexto do projeto, ambientes, roles, workbook, execuções, histórico, credenciais | `qa-workspaces/<empresa>/<projeto>/` |

### 1.2 Fronteira de privacidade

A linha entre a camada 3 e a camada 4 é uma fronteira rígida.

- **Nunca** entram no repositório `qa-assistant`: nomes de clientes ou empresas, URLs de ambientes, nomes de contas, credenciais, workbooks, resultados, bugs, capturas de ecrã ou qualquer exemplo que permita identificar um projeto real.
- As credenciais vivem **apenas** no `.env` do workspace e são referidas pelo nome da variável (ex.: `LOJA_BO_ADMIN_USER`), nunca pelo valor.
- Ao extrair uma heurística ou um exemplo de um projeto real para a framework, **anonimizar primeiro** (ver §17).
- O guarda de privacidade (detect-secrets + denylist, que também lê o conteúdo de ficheiros Office e os nomes dos ficheiros) bloqueia commits que violem esta regra. Um bloqueio do guarda nunca se contorna: corrige-se o conteúdo.

### 1.3 Hierarquia de fontes de verdade

Quando duas fontes estão em conflito, prevalece a de número mais baixo:

1. Requisitos explícitos e atuais do projeto (indicados pelo utilizador ou pela equipa na sessão)
2. Contexto do projeto (`CLAUDE.md` e `projeto.yaml` do workspace)
3. Dados dos casos de teste (modelo interno / workbook do projeto)
4. Esta metodologia
5. Módulos de teste (camada 2)
6. Código versionado, validado pelos testes da framework
7. Artefactos históricos (apenas como referência)

Se o conflito não se resolver objetivamente com esta hierarquia, **parar e perguntar** antes de qualquer alteração com consequências.

### 1.4 Regra de generalização

Ao surgir um requisito novo, decidir onde pertence:

| O requisito aplica-se a… | Vai para… |
|---|---|
| Todos os projetos, e é de método | Este documento |
| Todos os projetos, e é técnico (automação, ferramenta) | O módulo de teste correspondente |
| Vários projetos, mas não todos | Um componente configurável (opção no `projeto.yaml`, adaptador ou módulo), **nunca** um caso especial escrito numa camada partilhada |
| Um único projeto | O workspace desse projeto |

Distinguir sempre **capacidade da framework** de **detalhe de implementação de um projeto**. Um workaround que funcionou num projeto não é universal até ser demonstrado noutro.

---

## 2. Modelo de dados

O modelo interno é independente do formato de qualquer empresa. Os workbooks Excel, o Azure DevOps e o Jira são formatos de entrada e saída, tratados pelos adaptadores.

| Entidade | Campos mínimos |
|---|---|
| **Projeto** | id, nome, ambientes, roles, plataformas, módulos ativos, tracker, formato de bug |
| **Funcionalidade** | id, área, sub-área, nome, roles aplicáveis, risco (probabilidade × impacto) |
| **Caso de teste** | id, funcionalidade, título, pré-condições, passos, resultado esperado, role, plataforma, módulo, técnica de desenho, prioridade, origem (requisito / Figma / exploratório / bug) |
| **Lote** | id, contexto de agrupamento (área, funcionalidade, plataforma, role, fluxo), lista ordenada de casos |
| **Execução** | id, lote, ambiente, versão/build testada, data, executor (agente/humano) |
| **Resultado** | caso, execução, estado, nota curta, bug associado, evidência (quando aplicável) |
| **Bug** | id interno, id no tracker, área, funcionalidade, sub-área, plataforma, role, esperado, observado, passos, estado |
| **Heurística** | padrão anonimizado, tipo de ecrã/componente, módulo, técnica que o deteta |

Estados possíveis de um **Resultado**: `Pass`, `Fail`, `N/A`, `Blocked`, `Por testar`. O estado provisório `Dúvida` pode existir durante a sessão, mas nunca pode chegar a um relatório final (ver §8.5).

O ID de um caso é **estável**: não muda quando o caso é editado, movido ou exportado. Os adaptadores mantêm o mapeamento entre o ID interno e o ID de cada ferramenta externa.

---

## 3. Ciclo de trabalho

```
Onboarding → Análise → Desenho de casos → Planeamento de lotes → Execução
    → Classificação → Gestão de bugs → Validação → Checkpoint / Relatório
```

Cada etapa tem uma skill correspondente no Claude Code:

| Etapa | Skill |
|---|---|
| Onboarding | `/onboarding-projeto` |
| Análise | `/analisar-funcionalidade` |
| Desenho de casos | `/gerar-casos` |
| Planeamento de lotes | `/planear-lote` |
| Execução e classificação | `/executar-lote` (browser), `/testar-api` (API), `/auditar-acessibilidade` |
| Gestão de bugs | `/registar-bug` |
| Checkpoint | `/checkpoint` |
| Validação e relatório | `/fim-sessao` |
| Acompanhamento (a qualquer momento) | `/painel` |

---

## 4. Onboarding de um projeto

1. Criar a pasta `qa-workspaces/<empresa>/<projeto>/`.
2. Correr `/onboarding-projeto`, que copia o `contexto-template.md` e cria o `projeto.yaml`.
3. Preencher **apenas** informação específica do projeto:
   - ambientes (URL e finalidade; identificar explicitamente qual é o ambiente de testes);
   - roles/perfis e diferenças de acesso;
   - nomes das variáveis de credenciais (os valores vão para o `.env`);
   - documentação de referência (requisitos, especificações, Figma);
   - estrutura do workbook ou do tracker;
   - formato obrigatório de bug;
   - regra para bugs resolvidos (`remover` ou `arquivar`);
   - política de evidência (`texto` ou `texto+screenshot`);
   - módulos de teste ativos;
   - exceções e regras próprias do projeto.
4. Confirmar que o `.env` está no `.gitignore` e que `historico/` está protegido.
5. Verificar que nenhuma informação do projeto foi escrita nas camadas universais.
6. Criar o `contexto.md` inicial (ver §12.2).
7. Importar os casos existentes com o adaptador do projeto e validar o round-trip (importar → exportar → comparar).

Informação que já está no workspace **não se volta a perguntar nem a pesquisar**, salvo se houver evidência de que está desatualizada ou inconsistente. Se faltar uma informação necessária para continuar, identificar exatamente o que falta e perguntar.

---

## 5. Análise e desenho de casos

### 5.1 Análise

Para cada funcionalidade, identificar:

- as roles que lhe acedem e o que cada uma pode ou não fazer;
- as entradas, os estados, as regras de negócio e as integrações;
- o risco: probabilidade de falha × impacto para o utilizador ou para o negócio;
- as lacunas de especificação. Estas **não se preenchem com suposições**: registam-se como pergunta ou como nota de "especificação em falta".

### 5.2 Técnicas de desenho

Cada caso gerado indica a técnica que o originou:

| Técnica | Quando usar |
|---|---|
| Partição de equivalência | Campos e entradas com classes de valores válidos e inválidos |
| Valores-limite | Limites numéricos, de tamanho, de datas ou de quantidade |
| Tabela de decisão | Várias condições de negócio combinadas |
| Transição de estados | Entidades com ciclo de vida (encomenda, pedido, conta) |
| Pairwise | Muitos parâmetros independentes combinados |
| Matriz de roles | Cada ação × cada perfil |
| Error guessing | Heurísticas acumuladas (§17) |
| Exploratório (charter) | Áreas novas, pouco especificadas ou de alto risco |

### 5.3 Regras

- Um caso tem **um** resultado esperado verificável. Se tiver vários, divide-se.
- O resultado esperado vem da especificação, não do comportamento atual da aplicação.
- Os casos gerados pelo assistente são **propostas**. Só entram na biblioteca depois de aprovados pelo utilizador.
- A prioridade de um caso deriva do risco da funcionalidade, salvo indicação contrária do projeto.

---

## 6. Planeamento em lote

Os casos **não** se executam necessariamente pela ordem em que aparecem no documento.

Agrupar por **área + funcionalidade + plataforma + role + fluxo**, e dentro de cada grupo:

- executar seguidos os casos que partilham login, ambiente ou estado da aplicação;
- completar todos os casos possíveis de um contexto antes de mudar para outro;
- executar primeiro os fluxos que criam estado reutilizável por outros casos;
- ordenar os grupos por risco quando o tempo disponível não chega para tudo.

**Objetivo:** minimizar logins repetidos, navegação redundante, mudanças de ambiente, trocas de contexto e processamento repetido.

A otimização **nunca** pode reduzir a cobertura nem alterar a lógica de um caso.

---

## 7. Execução e registo

### 7.1 Registo leve

Durante a sessão, os resultados são escritos num registo leve em `runs/` (um ficheiro por lote, ex. `runs/lote_20260922_01.json`), e não diretamente no workbook ou no tracker. O ficheiro é `{"resultados": [ … ]}` e cada resultado contém, no mínimo:

```json
{"tc": "CHK-013", "estado": "Fail", "resultado_obtido": "cupão expirado aceite", "bug": "BUG-41", "ts": "2026-09-22T14:03:00"}
```

- `estado` usa o vocabulário do projeto; `resultado_obtido` é o que se observou (não o esperado copiado); `bug` é um bug **já existente** associado ao caso.
- Um bug **novo** vai como `defeito_novo` (campos na técnica do tracker, ex. `tecnica-excel.md` §4), só depois de aprovado pelo utilizador (§9).
- Casos em `Dúvida` podem ficar no registo durante a sessão, mas são reclassificados antes de o lote passar para o workbook ou tracker (§8.5): o adaptador recusa um lote com estados fora do vocabulário do projeto.

### 7.2 Checkpoints

Os resultados passam para o modelo e para os ficheiros principais em **checkpoints**:

- a cada 15–20 casos;
- antes de qualquer operação que possa fazer perder trabalho;
- no fim da sessão.

Cada checkpoint preserva os estados, os resultados, os bugs novos ou atualizados e o ponto exato onde a execução está.

### 7.3 Evidência

| Resultado | Evidência |
|---|---|
| Pass | Nenhuma além do registo |
| Fail | Suficiente para reproduzir: URL, role, passos executados, dados usados, esperado → observado, mensagens relevantes |
| N/A | Justificação curta |
| Blocked | Motivo e dependência que bloqueia |

Por defeito a evidência é **textual**. Screenshots ou vídeo só quando o `projeto.yaml` o exigir. Não gerar evidência redundante.

---

## 8. Regras de decisão

### 8.1 Pass

O comportamento observado corresponde ao resultado esperado definido no caso.

### 8.2 Fail

Existe uma diferença **objetiva** entre **esperado → observado**.

Não é Fail só porque:

- o comportamento parece estranho;
- existe uma preferência diferente;
- a implementação é diferente da que o tester imaginava;
- há uma possível melhoria não especificada;
- existe uma suspeita sem evidência suficiente.

Estas situações registam-se como **observação** ou **sugestão**, fora dos resultados.

### 8.3 N/A

O caso não pode ser executado legitimamente no contexto atual: funcionalidade não disponível para a role, não disponível no ambiente, plataforma não aplicável, ou pré-condição impossível por definição.

N/A **nunca** serve para esconder um problema funcional.

### 8.4 Blocked

O caso devia ser executado, mas não pode prosseguir por causa de: ambiente indisponível, dependência externa, bug bloqueante, dados de teste inexistentes, ou limitação técnica da ferramenta.

Blocked **não** se converte automaticamente em Fail nem em N/A.

### 8.5 Dúvida

Se houver dúvida sobre o requisito ou sobre o resultado esperado, **não assumir**:

1. marcar o caso como `Dúvida` no registo leve, com a pergunta concreta;
2. continuar com os casos que não dependem dessa resposta;
3. apresentar todas as dúvidas ao utilizador de uma vez, no próximo checkpoint;
4. reclassificar com base na resposta.

Um relatório final nunca contém casos em `Dúvida`.

### 8.6 Resumo

| Situação | Estado |
|---|---|
| Observado = esperado | Pass |
| Observado ≠ esperado, com evidência | Fail |
| Não se aplica a este contexto | N/A |
| Devia correr mas não pode | Blocked |
| Não se sabe o que é esperado | Dúvida → perguntar |

---

## 9. Gestão de bugs

### 9.1 Antes de criar

1. Procurar bugs existentes relacionados.
2. Comparar, no mínimo: área, funcionalidade, sub-área, comportamento observado, plataforma e role (quando relevante).
3. Se o problema já existe, **atualizar o bug existente**.
4. Só criar um bug novo quando houver evidência de que é um problema distinto.
5. Quando não for claro se é duplicado, propor ao utilizador e deixá-lo decidir.

### 9.2 Conteúdo mínimo

- título no formato definido pelo projeto;
- ambiente e versão/build;
- role e plataforma;
- pré-condições e passos para reproduzir;
- **esperado → observado**;
- casos de teste associados;
- sugestão de severidade e prioridade **só quando pedida**. A decisão é do utilizador.

A estrutura do bug respeita sempre o formato do projeto. Não se altera sem necessidade.

### 9.3 Reteste

| Resultado do reteste | Ação |
|---|---|
| Continua a reproduzir | Manter e atualizar o bug |
| Resolvido | Aplicar a regra do projeto (`remover` da lista ativa ou `arquivar`) |
| Não foi possível validar | Manter o estado atual e registar o motivo |

### 9.4 Envio para o tracker

O assistente prepara o bug. O envio para o tracker externo só acontece **depois de aprovado** pelo utilizador.

---

## 10. Casos duplicados

Se o mesmo caso aparece mais do que uma vez:

- identificar o duplicado;
- não o executar novamente sem necessidade: reutilizar o resultado quando o contexto é equivalente;
- sinalizar a duplicação para limpeza da documentação.

Se aparece em contextos diferentes (outra role, plataforma ou ambiente), confirmar se o contexto muda realmente o teste antes de o considerar duplicado.

---

## 11. Consistência entre documentos e outputs

Quando existem vários documentos, sheets, relatórios ou ferramentas:

- comparar a informação relevante entre eles;
- identificar inconsistências;
- determinar a fonte de verdade (§1.3);
- atualizar os restantes para ficarem consistentes.

Nunca corrigir em silêncio quando há ambiguidade sobre qual versão está certa.

Verificar especialmente: IDs de casos, nomes de funcionalidades, estados, resultados, bugs, descrições, roles/permissões, ambientes, métricas, totais, fórmulas e referências cruzadas.

---

## 12. Checkpoints, continuidade e recuperação

### 12.1 Regras

- A execução tem de resistir a interrupções.
- Ao retomar: ler primeiro o `contexto.md` e o último registo em `runs/`, identificar o que já foi executado e continuar a partir do último ponto válido.
- Não repetir trabalho sem necessidade. Não reconstruir o progresso do zero.
- O git do workspace (quando existir) serve de ponto de restauro: um commit por checkpoint.

### 12.2 Estrutura do `contexto.md`

```markdown
# Contexto de sessão — <projeto>
Atualizado: <data e hora>

## Estado atual
## Ponto de execução (último lote, último caso)
## Trabalho concluído
## Decisões tomadas (e quem as tomou)
## Dúvidas em aberto
## Problemas conhecidos / bloqueios
## Trabalho pendente
## Estado da validação
```

O `contexto.md` é o estado **temporário** da sessão. Não substitui o `CLAUDE.md` do projeto nem o `projeto.yaml`. Uma decisão que passa a ser regra permanente sobe para um desses ficheiros.

---

## 13. Artefactos históricos

Lotes concluídos, relatórios de execuções passadas e exportações arquivadas são **imutáveis**, em todos os projetos:

- vivem em `historico/`, protegido por permissões do Claude Code;
- nunca se alteram, regeneram ou sobrescrevem;
- servem apenas como referência e como base de validação (§15).

Só se alteram com instrução explícita do utilizador para esse artefacto específico.

---

## 14. Ferramentas e ambientes de execução

### 14.1 Reutilizar primeiro

Antes de criar um script ou uma ferramenta nova:

1. procurar no `qa_core`, nas skills e nos adaptadores existentes;
2. ler a documentação respetiva;
3. perceber o fluxo atual;
4. reutilizar ou estender;
5. só criar algo novo quando o existente não serve, e explicar porquê.

Uma ferramenta que funciona não se substitui sem motivo.

### 14.2 Identificar o ambiente

No início de cada sessão, confirmar e, se houver diferenças, registar:

- que ferramentas estão disponíveis (Claude Code, MCPs ligados, browser, CLI `qa`);
- onde estão os ficheiros (repositório, workspace);
- que scripts e skills podem ser usados;
- as limitações do ambiente.

Não assumir que uma ferramenta disponível num ambiente existe noutro.

---

## 15. Alterações à framework

Qualquer alteração ao `qa_core`, aos adaptadores, às skills ou às camadas universais segue a **validação antes de adoção**:

1. Identificar o comportamento atual.
2. Alterar primeiro a especificação (Markdown), depois o código.
3. Correr os testes da framework, em especial:
   - **teste golden**: um lote histórico real (lido do workspace via `QA_GOLDEN_DIR`) tem de produzir uma saída idêntica à guardada;
     - hoje existe o golden **fictício** do adaptador Excel (`python scripts/golden_check.py`: fixture, lote e resultado esperado em `tests/golden/`, escritos à mão). O golden com lote histórico real via `QA_GOLDEN_DIR` ainda não está implementado; até lá, uma alteração que afete um projeto real valida-se também por comparação direta numa cópia do workbook desse projeto (nunca no original);
   - **teste de round-trip**: importar → exportar sem perda.
4. Investigar todas as diferenças relevantes.
5. Confirmar que cada diferença é intencional.
6. Só então adotar (commit).

Uma alteração não é "segura" só porque parece logicamente correta.

---

## 16. Qualidade dos outputs e fim de sessão

### 16.1 Critérios

Todos os outputs devem ser consistentes com os resultados reais, rastreáveis aos casos de teste, sem duplicações, organizados e fáceis de continuar numa sessão futura.

### 16.2 Fim de sessão (`/fim-sessao`)

1. **Guardar:** último checkpoint, com todos os resultados preservados.
2. **Bugs:** novos adicionados, existentes atualizados, resolvidos tratados segundo a regra do projeto.
3. **Cross-check:** casos ↔ resultados ↔ bugs ↔ relatórios ↔ totais.
4. **Dúvidas:** nenhum caso em `Dúvida` sem ter sido apresentado ao utilizador.
5. **Resumo** (formato abaixo).
6. **Contexto:** `contexto.md` atualizado.

Não declarar uma execução concluída enquanto existirem inconsistências conhecidas que possam alterar a interpretação dos resultados.

### 16.3 Resumo de sessão

```markdown
## Resumo — <projeto> — <data>
Ambiente: <ambiente> · Build: <versão>

| Pass | Fail | N/A | Blocked | Total executado | Por testar |
|------|------|-----|---------|-----------------|------------|

"Executado" é todo o caso fora de `Por testar`: **Total executado = Pass + Fail + N/A + Blocked**, e a % executada divide pelo total de casos. Um `N/A` conta como executado (foi analisado e classificado), tal como um `Blocked`. A mesma definição vale para o painel e para qualquer resumo do workbook.

Bugs novos: …
Bugs atualizados: …
Bugs resolvidos/removidos: …
Bloqueios: …
Inconsistências encontradas: …
Dúvidas em aberto: …
Próximos passos: …
```

---

## 17. Heurísticas

Cada bug relevante pode gerar uma heurística reutilizável noutros projetos.

- Guardar em `qa-assistant/docs/heuristicas.md` **só depois de anonimizar**: sem nomes, URLs, dados ou textos do cliente.
- Formato: *componente/tipo de ecrã → padrão de falha → como testar*.
- Exemplo: *Campo de IBAN → aceita espaços na introdução mas falha a validação no envio → testar com e sem espaços, com minúsculas e com o prefixo do país*.

As heurísticas alimentam a técnica de *error guessing* (§5.2).

---

## 18. Regra geral

O objetivo não é executar o maior número possível de casos. É **maximizar a cobertura e a qualidade, minimizando esforço, tempo, processamento e trabalho repetido**.

Prioridade: **Reutilização → Organização → Consistência → Cobertura → Eficiência**, sem nunca sacrificar a validade dos resultados.

Quando uma dúvida pode alterar significativamente o resultado dos testes, **parar e pedir esclarecimento**. Quando não bloqueia, continuar com a opção mais segura e registar a suposição.

---

## Notas de migração (v1 → v2)

- **Novo:** fronteira de privacidade (§1.2), modelo de dados próprio (§2), técnicas de desenho obrigatórias por caso (§5.2), estado `Dúvida` (§8.5), aprovação antes do envio para o tracker (§9.4), testes golden e round-trip (§15), heurísticas (§17).
- **Alterado:** o código deixa de ser reconstruído em cada sessão. Passa a ser versionado e validado por testes, e continua abaixo das especificações na hierarquia (§1.3, ponto 6).
- **Alterado:** a regra para bugs resolvidos e a política de evidência passam a ser configuráveis por projeto (`projeto.yaml`).
- **Mantido:** execução em lote, registo leve, checkpoints a cada 15–20 casos, regras Pass/Fail/N/A/Blocked, gestão de duplicados, consistência entre documentos, histórico imutável, reutilizar antes de criar.
