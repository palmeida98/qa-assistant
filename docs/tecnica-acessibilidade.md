# Técnica Acessibilidade

> **Camada 2 — Técnica de auditoria de acessibilidade web (WCAG 2.1 AA)**
> Versão 1.0 · 2026-09-24 · Plano, tarefa 3.2.
>
> Descreve **como** o assistente audita a acessibilidade de uma aplicação web. É agnóstica de projeto: não contém URLs, contas nem regras de nenhum cliente. Os exemplos usam o projeto fictício **Loja Demo**.
>
> Subordinada a `metodologia-core.md` (regras de decisão, registo, bugs) e a `tecnica-browser.md` (preparação, autenticação, localizadores, evidência no browser).

---

## 0. Âmbito

- Norma de referência: **WCAG 2.1, nível AA** (inclui o nível A), salvo se o `projeto.yaml` indicar outra (`acessibilidade.norma`).
- Duas partes, sempre as duas:
  - **automática**: axe-core via Playwright, página a página (§2);
  - **manual guiada**: teclado, foco, leitor de ecrã, contraste, zoom e reflow (§3). A parte automática deteta uma fração dos problemas; **"0 violações do axe" não quer dizer "acessível"**.
- Cada falha fica mapeada ao **critério WCAG** (ex. 1.4.3) e ao **nível** (A/AA).
- **Não** cobre certificação formal nem auditorias de conformidade legal: o resultado é evidência de testes, não uma declaração de conformidade.

---

## 1. Níveis de execução

| Nível | Ferramenta | Serve para |
|---|---|---|
| **N0 · Estático** | HTML da página | `lang`, `title`, estrutura de headings, `alt` em imagens estáticas |
| **N1 · Automático** | `scripts/auditar_a11y.py` (axe-core + Playwright, em `tools/a11y/`) | Varrimento das páginas declaradas, com e sem login |
| **N1 · Manual guiado** | Playwright MCP ou browser, pelo agente com o utilizador | Teclado, foco, leitor de ecrã, zoom, reflow (§3) |
| **N2 · Determinístico** | Specs Playwright com `@axe-core/playwright` no workspace | Regressão: "a página X continua sem violações A/AA" |

---

## 2. Parte automática (axe-core)

1. As páginas a auditar vêm da secção `acessibilidade:` do `projeto.yaml` (nome, URL, se precisa de login). Só ambientes de teste declarados.
2. Correr, a partir da raiz do qa-assistant:
   `python scripts/auditar_a11y.py <workspace> [--saida runs/a11y_<data>.json]`
   O script abre cada página no browser configurado (o Chrome instalado, por omissão), faz o login declarado quando a página o exige (credenciais do `.env`, passadas ao browser por variáveis de ambiente, nunca na linha de comandos) e corre o axe-core com as regras da norma.
3. O relatório separa, por página:
   - **violações WCAG** (tags `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`): cada uma com regra, critério(s), nível, impacto do axe, número de elementos e os primeiros seletores;
   - **boas práticas** (regras do axe sem critério WCAG): observações, não Fail;
   - **incompletos** (o axe não conseguiu decidir, ex. contraste sobre imagens): vão para a revisão manual (§3), não para Fail nem Pass.
4. O impacto do axe (`critical`, `serious`, `moderate`, `minor`) é uma sugestão de severidade; a decisão é do utilizador (metodologia §9.2).

---

## 3. Parte manual guiada

Por página ou fluxo, com o browser (Playwright MCP ou manual), verificar e registar:

| Verificação | Critérios | Como |
|---|---|---|
| Tudo operável só com teclado, sem armadilhas | 2.1.1, 2.1.2 | `Tab`/`Shift+Tab`, `Enter`, `Espaço`, setas; sair de cada componente |
| Ordem de foco lógica | 2.4.3 | A ordem do `Tab` segue a ordem visual e de leitura |
| Foco visível | 2.4.7 | Cada elemento focado tem indicador visível |
| Nomes, funções e estados acessíveis | 4.1.2, 1.3.1 | Snapshot de acessibilidade: cada controlo com nome e função certos |
| Leitor de ecrã (amostra) | 1.3.1, 4.1.2, 4.1.3 | NVDA/VoiceOver num fluxo principal; mensagens de estado anunciadas |
| Contraste de texto e de componentes | 1.4.3, 1.4.11 | Os incompletos do axe + estados (hover, foco, desativado) |
| Zoom a 200% | 1.4.4 | Sem perda de conteúdo nem de função |
| Reflow a 320 px de largura | 1.4.10 | Sem scroll horizontal no conteúdo (exceto tabelas, mapas) |
| Espaçamento de texto | 1.4.12 | Sem cortes com o espaçamento aumentado |
| Erros de formulário identificados e com sugestão | 3.3.1, 3.3.3 | Mensagem em texto, associada ao campo |
| Idioma e título da página | 3.1.1, 2.4.2 | `lang` certo; `title` descreve a página |
| Alternativas a imagens | 1.1.1 | `alt` descreve a função, ou vazio se decorativa |

Cada verificação manual é um caso (ou um passo de um caso) com um esperado verificável, como qualquer outro.

---

## 4. Casos e classificação

- Um caso automático típico: *"A página X não tem violações WCAG 2.1 A/AA detetadas pelo axe-core"*. Técnica: error guessing assistido por ferramenta.
- **Pass**: 0 violações WCAG na página (os incompletos ficam para a parte manual).
- **Fail**: pelo menos uma violação WCAG; o `resultado_obtido` lista as regras e os critérios.
- **Blocked**: a página não abriu, o login falhou, ou o browser não arrancou (ambiente/ferramenta).
- Boas práticas sem critério WCAG: observação, nunca Fail.

---

## 5. Bugs

- **Um defeito por problema distinto** (regra do axe + causa), não por elemento: se a mesma regra falha em 12 botões pela mesma causa, é um defeito com os 12 seletores (os primeiros no texto, o total indicado).
- A mesma regra em várias páginas, com a mesma causa (ex. componente partilhado): um defeito, com as páginas listadas (metodologia §9.1, §10).
- Título com o critério: *"[1.4.3 AA] Contraste insuficiente no preço dos produtos"*.
- Evidência: regra, critério e nível, página, seletores, o `failureSummary` do axe, e o link da regra (`helpUrl`). Sem capturas, salvo se a política do projeto o pedir ou o problema for visual (contraste, foco).

---

## 6. Preparação e segurança

- Ambiente confirmado contra o `projeto.yaml` (`tecnica-browser.md` §2).
- Credenciais só do `.env`; nunca em argumentos, relatórios ou bugs.
- O relatório do axe inclui excertos de HTML (truncados): num projeto real podem conter dados; ficam no workspace, nunca no repositório `qa-assistant`.
- Ritmo normal: uma página de cada vez, sem varrer sites inteiros por crawler.

---

## 7. Instalação da ferramenta

Uma vez por máquina, a partir da raiz do qa-assistant:

```
npm ci --prefix tools/a11y
```

Instala as versões fixadas em `tools/a11y/package.json`. Usa o Chrome instalado (`acessibilidade.browser: chrome`); em máquinas sem Chrome, `chromium` com `npx --prefix tools/a11y playwright install chromium`.

---

## 8. Ligação à metodologia

| Metodologia | Nesta técnica |
|---|---|
| §5 desenho de casos | §4 (casos automáticos) e §3 (manuais) |
| §7 registo leve | um item por caso; `resultado_obtido` = regras e critérios |
| §8 regras de decisão | §4 |
| §9 bugs | §5 |
| §1.2 privacidade | §6 |

---

## 9. Checklist

- [ ] Páginas e norma confirmadas no `projeto.yaml`
- [ ] Ferramenta instalada (`npm ci --prefix tools/a11y`)
- [ ] Parte automática corrida em todas as páginas declaradas
- [ ] Incompletos do axe revistos na parte manual
- [ ] Parte manual feita nos fluxos principais (pelo menos teclado, foco e zoom)
- [ ] Cada falha com critério WCAG e nível
- [ ] Um defeito por problema distinto
