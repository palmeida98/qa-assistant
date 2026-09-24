# Heurísticas

> **Camada 1 — Heurísticas reutilizáveis** (metodologia-core.md §17)
>
> Padrões de falha anonimizados, que alimentam a técnica de *error guessing* (§5.2) ao gerar casos. Só entram aqui **depois de anonimizar**: sem nomes, URLs, dados, textos ou capturas de nenhum cliente. As três primeiras vêm dos bugs do demo (`examples/demo/`), encontrados em sites públicos de prática.

Formato: **componente / tipo de ecrã → padrão de falha → como testar** (módulo · origem).

| Componente / ecrã | Padrão de falha | Como testar | Módulo · origem |
|---|---|---|---|
| Endpoint de criação (POST) | Um campo obrigatório em falta devolve **5xx** em vez de 4xx (a entrada inválida rebenta no servidor em vez de ser recusada) | Um pedido por campo obrigatório, cada um sem esse campo, e outro com o corpo vazio; esperado 400/422 com a indicação do campo (RFC 9110 para 4xx vs 5xx) | `api` · demo, BUG-1 |
| Páginas de uma aplicação web (sobretudo SPA) | Todas as páginas têm o mesmo `<title>` (o nome da aplicação): a navegação muda o conteúdo mas não o título (WCAG 2.4.2 A) | Em cada vista principal, comparar o `document.title` com o heading da vista; títulos iguais em vistas diferentes = Fail | `acessibilidade` · demo, BUG-2 |
| Lista ou grelha de itens com imagem | Todos os itens mostram a mesma imagem (URL da imagem fixo em vez de vir do item) | Comparar o `src` das imagens entre itens diferentes da mesma lista, não só se a imagem carrega; repetir com cada role, porque o defeito pode ser só de uma | `funcional-web` · demo, BUG-3 |
| Campo de IBAN | Aceita espaços na introdução mas falha a validação no envio | Testar com e sem espaços, com minúsculas e com o prefixo do país | `funcional-web` · exemplo da metodologia §17 |
