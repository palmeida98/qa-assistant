# Técnica Mobile

> **Camada 2 — Técnica de testes mobile com Maestro**
> Versão 1.0 · 2026-09-24 · Plano, tarefa 3.3.
>
> Descreve **como** o assistente testa apps móveis (Android/iOS) e a versão web em contexto móvel. É agnóstica de projeto: não contém apps, contas nem regras de nenhum cliente. Os exemplos usam o projeto fictício **Loja Demo**.
>
> Subordinada a `metodologia-core.md` (regras de decisão, registo, bugs). Para a web no browser de secretária, ver `tecnica-browser.md`.

---

## 0. Âmbito

- Apps nativas e híbridas em **Android** e **iOS**, conduzidas por **flows Maestro** (YAML).
- Web em contexto móvel, pelo **Maestro Web** (beta) quando não houver dispositivo; o resultado vale para o fluxo web, **não** para a app nativa.
- Gestos, permissões, deep links, estados offline e background, diferenças entre plataformas.
- **Não** cobre testes de desempenho, bateria nem segurança ativa da app.

---

## 1. Onde correr: emulador, dispositivo ou web

| Alvo | Serve para | Limitações |
|---|---|---|
| **Emulador Android / simulador iOS** | A maior parte dos fluxos funcionais; repetível; CI | Sensores, câmara, notificações push e desempenho não são reais |
| **Dispositivo real** | Gestos finos, câmara, biometria, push, rede real, fabricantes específicos | Menos repetível; precisa de gestão de dispositivos |
| **Maestro Web (beta)** | Fluxos da versão web quando não há dispositivo | Não testa a app nativa; o suporte é beta |

Regra: o registo leve e o bug dizem **sempre** onde correu (plataforma, versão do SO, modelo ou emulador, versão da app). Um Pass no emulador não vale para "dispositivo real" nem o contrário, se o caso o distinguir.

---

## 2. Níveis de execução

| Nível | Ferramenta | Serve para |
|---|---|---|
| **N1 · Agente** | Maestro Studio / comandos pelo agente | Explorar, escrever e afinar flows |
| **N1 · Flows** | `scripts/testar_mobile.py` (corre `maestro test`) | Lotes de casos, cada caso um flow |
| **N2 · Regressão** | Os mesmos flows em CI ou Maestro Cloud | Smoke e regressão |

Os flows vivem **no workspace** (`flows/`), nunca no `qa-assistant` (exceto o demo).

---

## 3. Preparação

1. Ler a secção `mobile:` do `projeto.yaml`: plataforma, `app_id` (ou `url` para web), pasta dos flows, variáveis de credenciais.
2. Confirmar o ambiente: a app instalada é a **build de testes** (não a da loja), ou a URL é a do ambiente de testes. Se parecer produção, parar e perguntar.
3. Confirmar o alvo: `adb devices` (Android) ou `xcrun simctl list` (iOS) mostra o dispositivo/emulador esperado. Sem alvo, os casos nativos ficam **Blocked** (ambiente), nunca Pass.
4. Registar versão da app, SO e dispositivo.

---

## 4. Flows

- **Um flow por caso**, com o ID do caso no nome do ficheiro (`CT-MOB-003.yaml`, ou `CT-MOB-003_carrinho.yaml`): o script liga o resultado ao caso por esse prefixo.
- Cada flow começa num estado conhecido (`launchApp` com `clearState: true` quando o caso o exige) e termina com a **asserção do resultado esperado** do caso (`assertVisible`, `assertNotVisible`, `assertTrue`).
- Localizadores, por esta ordem: `id` (resource-id / accessibilityIdentifier / id no HTML), texto visível, `index` só em último recurso (como `tecnica-browser.md` §4.2).
- Esperas por condição (`extendedWaitUntil`), nunca por tempo fixo.
- **Credenciais**: nunca escritas no flow. O flow usa `${MAESTRO_<VAR>}`; o script põe o valor numa variável de ambiente `MAESTRO_<VAR>` a partir do `.env` (o Maestro disponibiliza aos flows as variáveis com esse prefixo). Nunca `-e VAR=valor` na linha de comandos.
- Dados de teste com o prefixo `QA-AUTO-<data>-` (`tecnica-browser.md` §7).

---

## 5. O que testar em mobile (além do funcional)

| Tema | O que verificar |
|---|---|
| Gestos | toque, toque longo, swipe, scroll até ao elemento, pull-to-refresh |
| Teclado | tipo de teclado certo por campo; o teclado não tapa o campo nem o botão |
| Permissões | primeira vez: pedido com explicação; recusar: a app continua utilizável; aceitar depois nas definições |
| Deep links | cada link abre o ecrã certo, com e sem sessão; link inválido tem tratamento |
| Offline e rede lenta | mensagem clara; nada se perde; retoma quando a rede volta |
| Background e retoma | voltar à app mantém o estado (formulário, carrinho); sessão expirada tratada |
| Orientação e tamanhos | rodar o ecrã; ecrã pequeno e tablet; texto grande do sistema |
| iOS vs Android | botão "voltar" (físico/gesto em Android, navegação em iOS), seletores de data, notificações |

---

## 6. Classificação

As regras da metodologia §8. Particularidades:

- Flow passou até à asserção final → **Pass**.
- Asserção do resultado esperado falhou, reproduzida → **Fail**.
- Falhou antes, por instabilidade de ferramenta (elemento não encontrado que existe, timeout do driver) → **uma** nova tentativa; se repetir, **Blocked** (ferramenta), com sugestão de teste manual.
- Sem dispositivo/emulador ou app por instalar → **Blocked** (ambiente).
- Funcionalidade que não existe numa plataforma, por definição → **N/A** nessa plataforma.

---

## 7. Evidência

- Pass: o relatório do Maestro (JUnit) chega.
- Fail: flow, passo que falhou e a mensagem do Maestro, plataforma/SO/dispositivo/versão da app, esperado → observado. Captura de ecrã só por política do projeto ou se o problema for visual (o Maestro guarda-as em `~/.maestro/tests/`).
- Sem credenciais nem dados pessoais na evidência.

---

## 8. Segurança e limites

- Só builds e ambientes de teste declarados; nunca contas reais de clientes.
- Nunca enviar a app ou os flows para serviços de terceiros (ex.: Maestro Cloud) sem autorização do projeto.
- Sem compras reais, SMS ou chamadas; permissões sensíveis (localização, contactos) só com dados de teste.

---

## 9. Ligação à metodologia

| Metodologia | Nesta técnica |
|---|---|
| §5 desenho de casos | §5 (temas mobile) e §4 (um flow por caso) |
| §7 registo leve | um item por flow; `resultado_obtido` com plataforma e mensagem |
| §8 regras de decisão | §6 |
| §9 bugs | §7 |
| §1.2 privacidade | §4 (credenciais) e §8 |

---

## 10. Checklist por lote

- [ ] Alvo confirmado (dispositivo/emulador ou web) e registado
- [ ] Build de testes / URL de testes confirmada
- [ ] Credenciais só como `${MAESTRO_<VAR>}`
- [ ] Um flow por caso, com o ID no nome
- [ ] Relatório JUnit guardado no workspace
- [ ] Casos sem alvo marcados Blocked, não Pass
