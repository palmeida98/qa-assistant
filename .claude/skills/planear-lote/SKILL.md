---
name: planear-lote
description: Agrupa e ordena casos de teste em lotes por area, funcionalidade, plataforma, role e fluxo, para minimizar logins e mudancas de contexto sem reduzir cobertura (metodologia-core.md seccao 6).
---

1. Le do workspace atual o projeto.yaml (roles, ambientes, modulos ativos) e o contexto.md
   (ponto de execucao, bloqueios conhecidos, decisoes de ambito).
2. Seleciona os casos candidatos (ex.: por executar, reteste de bugs corrigidos). Exclui os que estao
   fora de ambito por decisao registada e indica porque.
3. Agrupa por area + funcionalidade + plataforma + role + fluxo (seccao 6).
4. Dentro de cada grupo:
   - seguidos os casos que partilham login, ambiente ou estado da aplicacao;
   - primeiro os fluxos que criam estado reutilizavel por outros casos;
   - dependencias entre casos explicitas (tecnica-browser.md seccao 8).
5. Um login por role e por lote (tecnica-browser.md seccao 3).
6. Se o tempo nao chegar para tudo, ordena os grupos por risco.
7. Verifica que os dados de teste de cada lote existem. Os casos sem dados ficam Blocked, com o motivo.
8. A otimizacao nunca reduz cobertura nem altera a logica de um caso: confirma que o numero de casos
   planeados e igual ao numero de casos selecionados.
9. Apresenta o plano (lotes, ordem, role, casos, dependencias) e espera confirmacao antes de /executar-lote.
