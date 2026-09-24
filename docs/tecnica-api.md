# Técnica API

> **Camada 2 — Técnica de testes de API (HTTP/REST)**
> Versão 1.0 · 2026-09-24 · Plano, tarefa 3.1.
>
> Descreve **como** o assistente testa uma API HTTP. É agnóstica de projeto: não contém URLs, contas, tokens nem regras de nenhum cliente. Os exemplos usam o projeto fictício **Loja Demo**.
>
> Subordinada a `metodologia-core.md`: as regras de decisão, registo, bugs e checkpoints estão lá e não se repetem aqui.

---

## 0. Âmbito

- APIs HTTP (REST/JSON na maioria dos casos): contratos, códigos de estado, autenticação e autorização por role, validação de entradas, idempotência, persistência, paginação e limites.
- Serve o módulo `api` e é usada pelo módulo `funcional-web` para preparar dados ou confirmar persistência (`tecnica-browser.md` §14).
- **Não** cobre testes de carga nem testes de segurança ativos (fuzzing, injeção, força bruta). Ver §11.
- Os valores concretos (URL base, variáveis de credenciais, contrato) vêm do workspace: secção `api:` do `projeto.yaml`.

---

## 1. Níveis de execução

Usar sempre **o nível mais baixo que permite verificar o resultado esperado**.

| Nível | Ferramenta | Serve para | Não serve para |
|---|---|---|---|
| **N0 · Pedido isolado** | `scripts/api_pedido.py` (ou `curl`) | Um pedido, um código e um corpo a verificar; disponibilidade; contrato de um endpoint | Fluxos com estado entre pedidos |
| **N1 · Sequência pelo agente** | `api_pedido.py` encadeado pelo agente | Fluxos com estado (criar → ler → alterar → apagar), autenticação, matriz de roles, persistência | Regressão repetida em grande volume |
| **N2 · Determinístico** | Specs Playwright (`request`) no workspace | Regressão e smoke em CI | Endpoints novos ou instáveis |

O `api_pedido.py` existe para que **nenhum segredo chegue à evidência**: substitui `{{VARIAVEL}}` pelo valor do `.env` só no momento do pedido e redige a saída (§9). Coleções Postman que o projeto já tenha usam-se como fonte de casos e dados; não se reescrevem.

---

## 2. Preparação da sessão

1. Ler a secção `api:` do `projeto.yaml`: URL base do ambiente de testes, tipo de autenticação, variáveis de credenciais por role, contrato.
2. **Confirmar o ambiente.** A URL base tem de ser a do ambiente de testes declarado. Se parecer produção, ou não coincidir, **parar e perguntar**.
3. Confirmar que as variáveis de credenciais existem no `.env` (só que existem; nunca mostrar o valor).
4. Identificar a **fonte de verdade do contrato**, pela hierarquia da metodologia §1.3: especificação aprovada (OpenAPI, documentação publicada) → casos existentes → comportamento observado (nunca fonte de esperado).
5. Um pedido de disponibilidade (health check, se existir) antes do lote. Se falhar, o lote fica `Blocked` (ambiente).

---

## 3. Autenticação e autorização por role

- Credenciais e tokens vêm **só** de variáveis de ambiente; nunca aparecem em registos, notas, bugs ou mensagens (§9).
- Obter um token por role e por lote, e reutilizá-lo enquanto for válido.
- Para cada operação protegida, a matriz mínima é:

| Pedido | Esperado típico (confirmar na especificação) |
|---|---|
| sem credenciais | recusado: 401 (ou 403) |
| credenciais inválidas ou token expirado | recusado: 401 |
| role sem permissão | recusado: 403 (ou 404, se o projeto esconder a existência) |
| role com permissão | aceite |
| recurso de **outro** utilizador (IDOR) | recusado, sem revelar dados |

- Um pedido recusado confirma-se também pelo efeito: o recurso fica **inalterado** (ler de novo com uma role autorizada).

---

## 4. Contrato e schemas

Verificar, por endpoint e por código de resposta documentado:

- **Código de estado** exatamente o documentado (201 não é 200).
- **`Content-Type`** da resposta.
- **Campos obrigatórios** presentes, com o **tipo** certo (número não é texto), formato (datas, IDs) e valores de enumerações válidos.
- **Eco dos dados**: o que se criou ou alterou volta igual ao enviado, salvo campos que o servidor calcula.
- Campos a mais não são Fail, salvo se o contrato os proibir (registar como observação).

Quando a especificação e a implementação divergem, o esperado é o da especificação (metodologia §5.3). Quando a especificação **não diz** (ex.: não documenta respostas de erro), não se inventa: o caso só entra se houver outra fonte objetiva (norma HTTP, RFC 9110, requisito geral do projeto) e essa fonte fica escrita no caso; senão vai como pergunta (metodologia §5.1).

---

## 5. Casos negativos

Por endpoint que recebe dados, pelas técnicas da metodologia §5.2:

- campos obrigatórios em falta, um a um (partição de equivalência);
- tipos errados (texto num número, número num booleano);
- valores-limite (vazio, tamanho máximo, datas impossíveis, negativos);
- corpo vazio, JSON malformado, `Content-Type` errado (415), método não permitido (405), recurso inexistente (404).

Regra de base da norma HTTP: um erro de entrada do cliente responde **4xx**; um **5xx** a uma entrada inválida é Fail (o servidor não validou), mesmo que a especificação não documente o erro. O caso cita a fonte (RFC 9110 §15.5 e §15.6).

---

## 6. Idempotência, efeitos e persistência

- `GET`, `HEAD`: sem efeitos; repetir dá o mesmo resultado.
- `PUT`, `DELETE`: idempotentes; repetir não cria nem estraga nada (o código da repetição pode diferir, ex. 404 num segundo `DELETE`; confirmar na especificação).
- `POST`: repetir cria outro recurso, salvo se o contrato prever chave de idempotência (testá-la quando existir).
- `PATCH`: altera só os campos enviados; os outros ficam iguais.
- **Persistência**: quando o caso afirma que algo foi criado, alterado ou apagado, confirmar com um `GET` a seguir (e não só pelo código da resposta).

---

## 7. Listas, paginação, filtros e limites

- Filtros: o resultado contém o que devia (o recurso criado no teste) e não contém o que não devia.
- Paginação: primeira página, última, página vazia além do fim, tamanho máximo; nenhum item repetido ou perdido entre páginas.
- Ordenação: verificar a ordem pedida, não a que parece natural.
- Limites de taxa (rate limiting): confirmar que existem só se a especificação os documentar, com o **menor** número de pedidos que o demonstre. Nunca martelar a API (§11).

---

## 8. Verificação e classificação

As regras de decisão são as da metodologia §8. Particularidades de API:

| Situação | Estado |
|---|---|
| Código, corpo e efeito iguais ao esperado | Pass |
| Código ou corpo diferente do documentado, com evidência | Fail |
| 5xx a uma entrada inválida | Fail (§5) |
| Timeout, 502/503/504, DNS, TLS | Blocked (ambiente); uma nova tentativa (`tecnica-browser.md` §9) |
| Endpoint não disponível para a role, por definição | N/A |
| Especificação omissa sobre o esperado | Dúvida → pergunta |

O tempo de resposta só é critério quando a especificação o fixar; caso contrário, fica como observação.

---

## 9. Evidência (com segredos redigidos)

Um **Fail** regista pedido e resposta suficientes para reproduzir:

- método, URL (sem tokens na query string), cabeçalhos relevantes, corpo enviado;
- código, cabeçalhos relevantes e corpo recebido (truncado se for grande);
- tempo de resposta e data/hora;
- esperado → observado.

**Redação obrigatória**, feita pelo `api_pedido.py` antes de qualquer coisa ser escrita:

- cabeçalhos `Authorization`, `Proxy-Authorization`, `Cookie`, `Set-Cookie`, `X-Api-Key` e afins → `«redigido»`;
- chaves JSON cujo nome indique uma credencial (token, palavra-passe, segredo, chave de API; a lista exata está em `CHAVE_SECRETA` no script) são redigidas;
- qualquer ocorrência literal do valor de uma variável do `.env` → `«NOME_DA_VARIAVEL»`.

Nunca copiar para a evidência o valor de um token "só para mostrar que existia": regista-se que existe e o tamanho.

---

## 10. Dados de teste

- Dados fictícios, com o prefixo identificável da sessão (`QA-AUTO-<data>-`), como no browser (`tecnica-browser.md` §7).
- Registar no registo leve os recursos criados (tipo + ID) e apagá-los no fim do lote quando o projeto o permitir; o que ficar, fica listado no `contexto.md`.
- Nunca alterar nem apagar recursos que o teste não criou.

---

## 11. Segurança e limites

- Só a URL base declarada como ambiente de testes, com autorização da equipa responsável.
- Sem testes de carga, de stress nem de volume; sem fuzzing massivo; sem força bruta de credenciais.
- Respeitar limites de taxa: pausas entre pedidos quando a API os anunciar.
- Testes de autorização (§3) fazem-se só com as contas de teste declaradas, nunca com contas de pessoas reais.
- Se um pedido puder ter efeitos fora do ambiente de testes (e-mails, pagamentos, integrações externas), **parar e perguntar**.

---

## 12. Passagem para regressão (N1 → N2)

Os mesmos critérios de `tecnica-browser.md` §13. Na spec: `request` do Playwright, um ficheiro por recurso, o ID do caso no título, URL base e credenciais de variáveis de ambiente. As specs vivem no workspace, não no `qa-assistant`.

---

## 13. Ligação à metodologia

| Metodologia | Nesta técnica |
|---|---|
| §5 desenho de casos | §4 contrato, §5 negativos, §3 matriz de roles |
| §7 registo leve | um item por caso, com `resultado_obtido` = código + resumo do corpo |
| §8 regras de decisão | §8 |
| §9 bugs | evidência §9, sempre redigida |
| §1.2 privacidade | §9 e §11 |

---

## 14. Checklist por lote

**Antes**
- [ ] URL base confirmada contra o `projeto.yaml` (ambiente de testes)
- [ ] Variáveis de credenciais presentes para as roles do lote
- [ ] Fonte de verdade do contrato identificada
- [ ] Health check OK

**Durante**
- [ ] Todos os pedidos pelo `api_pedido.py` (evidência redigida)
- [ ] Persistência confirmada com `GET` quando o caso o afirma
- [ ] Recursos criados registados
- [ ] Omissões da especificação registadas como Dúvida, sem esperado inventado

**Depois**
- [ ] Recursos de teste apagados ou listados no `contexto.md`
- [ ] Nenhum segredo no registo leve, nos bugs ou no `contexto.md`
- [ ] Candidatos a N2 identificados
