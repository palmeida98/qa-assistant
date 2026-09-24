---
name: testar-mobile
description: Testa a app movel (Android/iOS) ou a web em contexto movel do workspace atual com flows Maestro, um flow por caso, seguindo a tecnica-mobile.md.
---
1. Le a tecnica-mobile.md (<qa>/docs/) e, no workspace atual, a seccao mobile: do projeto.yaml
   (plataforma, flows, variaveis) e o contexto.md. <qa> e a raiz do qa-assistant: normalmente
   ../../../qa-assistant; no demo, ../.. . Se a seccao estiver vazia, para e pergunta.
2. Confirma o alvo e o ambiente (seccoes 1 e 3): build de testes instalada ou URL de testes;
   dispositivo/emulador ligado (android: adb devices; ios: simulador). Sem alvo, os casos ficam
   Blocked (ambiente) -- nunca Pass. Regista plataforma, SO, dispositivo e versao da app.
3. Flows em <workspace>/<flows>/, um por caso, com o ID no nome (CT-MOB-003_carrinho.yaml), a terminar
   na assercao do resultado esperado. Se faltarem, escreve-os como PROPOSTA a partir dos casos
   aprovados (seccao 4) e mostra-os antes de os usar. Credenciais so como ${MAESTRO_<VAR>}, com a
   variavel listada em mobile.variaveis; nunca escritas no flow nem passadas com -e.
4. Corre, a partir de <qa>, com o caminho ABSOLUTO do workspace:
   python scripts/testar_mobile.py <workspace> [flows...] --saida runs/mobile_<AAAAMMDD>_<nn>.json
   (cada flow: caminho, relativo ao workspace ou a pasta dos flows, ou so o ID do caso, ex. CT-MOB-003;
   sem flows, corre todos; um flow que nao existe da saida 2 e nada corre)
   - 3 = sem alvo ou o Maestro nao correu: Blocked (ambiente);
   - Fail por instabilidade da ferramenta (elemento que existe e nao foi encontrado, timeout do driver):
     uma nova tentativa desse flow; se repetir, Blocked (ferramenta) com sugestao de teste manual.
5. Classifica pela metodologia seccao 8 e pela tecnica-mobile.md seccao 6. Registo leve no formato da
   metodologia seccao 7.1, com a plataforma e o alvo no resultado_obtido.
6. Fail novo -> evidencia (flow, passo, mensagem do Maestro, alvo) e /registar-bug, so com aprovacao.
7. Nunca envia a app nem os flows para servicos de terceiros (ex.: Maestro Cloud) sem autorizacao do
   projeto. Segue para /checkpoint.
