# Técnica Browser

> **Camada 2 — Técnica de automação browser**
> Versão 2.0 · 2026-09-22 · Substitui a versão usada no Claude.ai Projects.
>
> Descreve **como** o assistente conduz um browser para testar aplicações web. É agnóstica de projeto: não contém URLs, contas, roles ou regras de nenhum cliente. Os exemplos usam o projeto fictício **Loja Demo**.
>
> Subordinada a `metodologia-core.md`: as regras de decisão, registo, bugs e checkpoints estão lá e não se repetem aqui.

---

## 0. Âmbito

- Serve de base a todos os módulos web: `funcional-web`, `smoke`, `roles`, `exploratorio`, `acessibilidade`, `visual`, `cross-browser` e `formularios`. Cada módulo acrescenta apenas o que lhe é próprio.
- **Não** cobre apps móveis nativas. Essas são uma camada separada (`modulos/mobile.md`).
- Os valores concretos (URLs, roles, variáveis de credenciais, exceções) vêm do workspace: `CLAUDE.md` e `projeto.yaml`.

---

## 1. Níveis de execução

Usar sempre **o nível mais baixo que permite verificar o resultado esperado**.

| Nível | Ferramenta | Serve para | Não serve para |
|---|---|---|---|
| **N0 · Estático** | Pedido HTTP (fetch) | Disponibilidade da página, código HTTP, `title`, meta tags, links e âncoras, CTAs e rodapé presentes no HTML | Nada que dependa de JavaScript, login ou interação |
| **N1 · Agente** | Playwright MCP | Execução de lotes, fluxos com login, formulários, estados dinâmicos, exploração | Regressão repetida em grande volume |
| **N2 · Determinístico** | Specs Playwright (TypeScript) | Regressão e smoke que correm sozinhos e em CI | Funcionalidades novas ou ainda instáveis |

Regras:

- Subir de N0 para N1 assim que o resultado esperado envolva comportamento dinâmico ou interativo.
- Um caso passa de N1 para N2 segundo os critérios da §13.
- Se o Playwright MCP não estiver disponível no ambiente, usar o browser disponível (por exemplo, Claude in Chrome) e **registar a diferença de ambiente** (metodologia §14.2).

---

## 2. Preparação da sessão

Antes do primeiro caso de um lote:

1. Ler o `projeto.yaml`: ambientes, roles, módulos ativos, política de evidência.
2. **Confirmar o ambiente.** O URL a abrir tem de ser um dos ambientes de teste declarados no `projeto.yaml`. Se o URL não coincidir, ou se parecer produção, **parar e perguntar**. Um link desatualizado é suficiente para testar o sistema errado.
3. Confirmar que as variáveis de credenciais necessárias existem (verificar só que existem, sem mostrar o valor).
4. Registar a versão/build testada, se a aplicação a mostrar (rodapé, página "Sobre", cabeçalho de resposta).
5. Verificar que os dados de teste de que o lote depende existem (§7). Se não existirem, os casos afetados ficam `Blocked`.

---

## 3. Autenticação e roles

- As credenciais vêm **exclusivamente** de variáveis de ambiente. Nunca se escrevem em registos, notas, bugs, relatórios ou mensagens, nem mascaradas parcialmente.
- **Um login por role e por lote.** A ordem dos lotes (metodologia §6) deve permitir executar todos os casos de uma role antes de mudar para outra.
- Quando a ferramenta o permitir, guardar e reutilizar o estado de sessão (cookies/storage) por role, para evitar repetir o login.
- Ao mudar de role: fazer logout explícito ou usar um contexto de browser novo. Nunca assumir que a sessão anterior terminou.
- Sessão expirada a meio de um lote: fazer login outra vez **uma vez** e repetir o passo. Se voltar a falhar, os casos seguintes dessa role ficam `Blocked`, com o motivo.
- CAPTCHA, 2FA ou verificação por email/SMS: **não contornar**. Pedir ao utilizador que complete o passo, ou marcar como `Blocked` se ele não estiver disponível.

---

## 4. Leitura da página e localização de elementos

### 4.1 Ler a estrutura, não a imagem

- Preferir o **snapshot de acessibilidade** (árvore de roles e nomes acessíveis) à captura de ecrã. É mais rápido, mais barato e mais fiável.
- Para ler conteúdo, extrair só a região relevante em vez da página inteira.
- A captura de ecrã só se usa quando o aspeto visual **é** o resultado esperado (layout, cor, sobreposição) ou quando o projeto o exige como evidência.

### 4.2 Prioridade de localizadores

1. Role + nome acessível (ex.: botão "Finalizar compra")
2. Label associada ao campo
3. Placeholder
4. Texto visível
5. `data-testid` ou equivalente
6. Seletor CSS estável

Nunca usar XPath posicional nem seletores gerados automaticamente (classes com hash, índices como `div:nth-child(7)`).

Quando um elemento só é localizável pelos níveis 5–6, isso pode indicar um problema de acessibilidade. Registar como observação para o módulo `acessibilidade`, não como Fail do caso funcional.

### 4.3 Esperas

- Esperar por uma **condição** (elemento visível, texto presente, pedido de rede concluído, URL alterado), nunca por um tempo fixo.
- Em SPAs, confirmar que a vista mudou (título, heading ou URL) antes de verificar o conteúdo.
- Um timeout conta como sinal de ambiente (§9), não como resultado do caso.

---

## 5. Interação

| Situação | Técnica |
|---|---|
| Campos de texto | Depois de escrever, ler o valor do campo e confirmar que corresponde ao que se pretendia (máscaras, autocompletar e limites de tamanho alteram o valor) |
| Selects e comboboxes | Selecionar pelo texto visível da opção. Em comboboxes personalizadas: abrir, esperar pela lista, escolher e confirmar o valor mostrado |
| Datas | Preferir escrever no campo. Usar o seletor de datas só se o caso testar o próprio seletor |
| Uploads | Usar apenas ficheiros de teste do workspace (`dados/`), nunca documentos reais |
| Downloads | Confirmar que o download começou e, se o caso o exigir, o nome, o tipo e o tamanho do ficheiro |
| Modais e diálogos | Tratar explicitamente os diálogos nativos (alert/confirm) antes de continuar. Um diálogo por tratar bloqueia o browser |
| Novas abas/janelas | Mudar explicitamente para a aba nova e voltar à original no fim |
| iframes | Entrar no frame antes de interagir. Registar se o conteúdo vier de terceiros |
| Paginação e scroll infinito | Percorrer até encontrar o elemento ou até ao fim declarado. Nunca concluir "não existe" só com base na primeira página |
| Ações destrutivas (apagar, cancelar, pagar) | Só em ambiente de testes e só quando o caso o exige (§12) |

---

## 6. Verificação

- Comparar o observado com o **resultado esperado escrito no caso**, e não com o que parece razoável.
- **Texto:** normalizar espaços. Ignorar maiúsculas/minúsculas ou acentos só se a especificação o permitir.
- **Estado:** visível, ativo/inativo, selecionado, só de leitura.
- **Navegação:** URL e título ou heading da vista de destino.
- **Persistência:** quando o caso afirma que algo foi guardado, recarregar a página ou reabrir o registo e confirmar.
- **Mensagens:** confirmar o texto e o tipo (erro, sucesso, aviso) e que a mensagem aparece junto do elemento certo.
- **Sinais secundários:** erros na consola JavaScript e pedidos de rede falhados (4xx/5xx) registam-se como **observações**. Só dão Fail se o resultado esperado os cobrir ou se causarem uma diferença observável no comportamento.

---

## 7. Dados de teste

- Nunca usar dados pessoais reais. Gerar dados fictícios válidos para o formato local (nomes, moradas, NIF, IBAN e telefones de teste).
- Tudo o que o assistente cria leva um **prefixo identificável**, por exemplo `QA-AUTO-20260922-`, para se distinguir dos dados de outras pessoas e ser fácil de limpar.
- Registar no registo leve as entidades criadas (tipo + identificador) para permitir a limpeza.
- Não alterar nem apagar dados que não foram criados pelo assistente, salvo se o caso o exigir e o projeto o permitir.
- Limpar no fim do lote, quando o projeto o permitir, ou deixar a lista do que ficou criado no `contexto.md`.

---

## 8. Estado e isolamento entre casos

- Cada caso declara as pré-condições de que precisa. Antes de o executar, confirmar que se verificam.
- Quando um caso deixa a aplicação num estado que afeta o seguinte (carrinho cheio, filtro ativo, rascunho guardado), repor o estado ou ordenar o lote para aproveitar esse estado de forma intencional.
- Dependências entre casos ficam explícitas no planeamento do lote. Se o caso A falhar, os casos que dependem dele ficam `Blocked` (bloqueados por A), e não Fail.

---

## 9. Robustez: distinguir aplicação, ambiente e ferramenta

Antes de classificar um problema, identificar a sua origem:

| Origem | Sinais | Classificação |
|---|---|---|
| **Aplicação** | Comportamento reproduzível e diferente do esperado | Fail |
| **Ambiente** | Timeouts, 502/503, serviço externo em baixo, dados em falta | Blocked (motivo: ambiente) |
| **Ferramenta** | O elemento existe mas a automação não consegue interagir (canvas, controlos nativos, drag-and-drop complexo) | Blocked (motivo: limitação da ferramenta). Sugerir teste manual |

Política de nova tentativa:

- Erros de tipo ambiente: **uma** nova tentativa após uma breve pausa.
- Se passar na segunda tentativa: `Pass`, com a nota "instável na 1.ª tentativa". Se a instabilidade se repetir em várias execuções, registar como observação para a equipa.
- Uma falha da aplicação **não** se repete à procura de um Pass. Reproduz-se uma vez para confirmar e classifica-se.

---

## 10. Evidência

Segue a política do `projeto.yaml` (metodologia §7.3). No browser, um **Fail** regista:

- URL no momento da falha;
- role e plataforma (browser + viewport);
- passos efetivamente executados (não os passos do caso copiados);
- dados introduzidos, sem credenciais;
- esperado → observado, com o texto exato do que apareceu;
- erros de consola e pedidos falhados relevantes;
- captura de ecrã só se a política do projeto for `texto+screenshot`, ou se o problema for visual.

---

## 11. Eficiência da execução

- Reutilizar a sessão e a navegação dentro do lote. Ir diretamente por URL quando o caso não testa a navegação em si.
- Não tirar snapshots completos repetidos da mesma página sem ter havido mudança.
- Em lotes grandes, a execução corre num **subagente** (`executor-web`), que devolve ao contexto principal apenas o registo leve: ID, resultado, nota curta e bug.
- Agrupar verificações da mesma página, quando vários casos a partilham, sem misturar os resultados de cada caso.

---

## 12. Segurança e limites

- Só se testam ambientes declarados no workspace, com autorização da equipa responsável.
- Nunca realizar pagamentos reais, enviar comunicações a pessoas reais nem alterar configurações de contas partilhadas (password, email, 2FA), salvo se o caso o exigir num ambiente preparado para isso.
- Nunca contornar controlos de segurança (CAPTCHA, rate limiting, bloqueio de conta).
- Testes de segurança ativos estão fora desta técnica. O módulo `seguranca` faz apenas scans passivos, e só com autorização expressa.
- Se uma ação puder ter efeitos fora do ambiente de testes, **parar e perguntar**.

---

## 13. Passagem para regressão (N1 → N2)

Um caso é candidato a spec Playwright quando:

- deu `Pass` em pelo menos **duas** execuções consecutivas, sem instabilidade;
- os dados de que precisa são criáveis ou determinísticos;
- os localizadores usados são de nível 1–4 (§4.2);
- o resultado esperado é estável (a funcionalidade não está em desenvolvimento ativo).

Na conversão:

- um ficheiro de spec por funcionalidade;
- o ID do caso no título do teste (ex.: `CHK-014 · Quantidade no limite (99)`), para os resultados se importarem para o modelo;
- etiquetas por módulo e por prioridade (`@smoke`, `@roles`, `@a11y`);
- credenciais e URLs lidos de variáveis de ambiente, nunca escritos na spec;
- as specs vivem no workspace do projeto (ou no repositório da empresa), **não** no `qa-assistant`.

---

## 14. Pontos de ligação com outros módulos

| Módulo | O que acrescenta a esta técnica |
|---|---|
| `acessibilidade` | Injeção do axe-core + verificações de teclado, foco e nomes acessíveis |
| `visual` | Screenshots de referência e comparação com os frames do Figma |
| `cross-browser` | O mesmo lote em Chromium, Firefox, WebKit e viewports móveis |
| `formularios` | Geração sistemática de valores-limite e classes de equivalência por campo |
| `api` | Pedidos diretos à API na mesma sessão, para preparar dados ou verificar persistência (`tecnica-api.md`, `scripts/api_pedido.py`) |

---

## 15. Checklist por lote

**Antes**
- [ ] Ambiente confirmado contra o `projeto.yaml`
- [ ] Variáveis de credenciais presentes para as roles do lote
- [ ] Dados de teste necessários disponíveis
- [ ] Build/versão registada
- [ ] Lote ordenado por role e contexto

**Durante**
- [ ] Um login por role
- [ ] Registo leve atualizado a cada caso
- [ ] Checkpoint a cada 15–20 casos
- [ ] Dúvidas marcadas, sem classificação forçada

**Depois**
- [ ] Entidades criadas registadas ou limpas
- [ ] Observações secundárias (consola, rede, acessibilidade) separadas dos resultados
- [ ] Candidatos a N2 identificados
- [ ] Controlo passado para `/fim-sessao`

---

## Notas de migração (v1 → v2)

- **Novo:** níveis de execução N0/N1/N2 (§1), confirmação obrigatória do ambiente (§2), classificação por origem aplicação/ambiente/ferramenta (§9), dados de teste com prefixo e limpeza (§7), critérios de passagem para regressão (§13), pontos de ligação com os outros módulos (§14).
- **Alterado:** o browser principal passa a ser o Playwright MCP no Claude Code. O Claude in Chrome fica como alternativa, com a diferença registada.
- **Alterado:** a evidência passa a ser textual por defeito. Screenshots só por política do projeto ou quando o problema é visual.
- **Mantido:** verificação estática para validação estrutural leve (agora N0), escalada para browser completo quando há comportamento dinâmico, e testes feitos diretamente pelo agente.
