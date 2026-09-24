---
name: registar-bug
description: Regista ou atualiza um bug a partir de um caso Fail, procurando duplicados primeiro.
---
1. Le o formato de bug em projeto.yaml (formato_bug e, com tracker excel, excel.defeitos) e no CLAUDE.md
   do workspace atual.
2. Procura bugs existentes com area/funcionalidade/sub-area/plataforma/role semelhantes (seccao 9.1).
   Com tracker excel, le a sheet de defeitos pelo adaptador (ExcelAdapter.from_projeto(...).load()),
   sem gravar.
3. Se encontrar um candidato a duplicado, mostra-o e pergunta: atualizar o existente ou criar novo.
4. Escreve: titulo no formato do projeto, ambiente e build, role e plataforma, pre-condicoes,
   passos para reproduzir, esperado -> observado, casos associados. Severidade e prioridade so se
   o utilizador as pedir ou decidir (seccao 9.2).
5. Mostra o bug ao utilizador e pede aprovacao explicita antes de o registar (seccao 9.4).
6. Depois de aprovado, com tracker excel, o bug NAO se escreve direto no workbook: vai no registo leve
   do lote, no item do caso Fail (tecnica-excel.md seccao 4):
   - bug novo -> "defeito_novo": {chaves de excel.defeitos.campos_lote, "severidade", "prioridade"};
     a prioridade tem de estar em excel.defeitos.prioridades;
   - bug existente -> "bug": "<id>".
   Passa ao workbook no proximo /checkpoint (adapters.excel.aplicar).
7. Com outro tracker, prepara o bug no formato desse tracker e so o envia depois de aprovado.
