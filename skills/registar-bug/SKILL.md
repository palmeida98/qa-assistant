---
name: registar-bug
description: Regista ou atualiza um bug a partir de um caso Fail, procurando duplicados primeiro.
---

1. Le o formato de bug em projeto.yaml (workspace atual).
2. Procura bugs existentes com area/funcionalidade/sub-area/plataforma semelhantes.
3. Se encontrar um candidato a duplicado, mostra-o e pergunta: atualizar ou criar novo.
4. Escreve: esperado -> observado, passos para reproduzir, ambiente, role.
5. Segue metodologia-core.md seccao 9.
6. Pede aprovacao explicita antes de enviar para o tracker (seccao 9.4).
