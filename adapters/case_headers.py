"""
The headers a column of test cases is recognised by, in the common languages (qa-ui's intake and the Excel adapter's
inspection). A project's own mapping (excel.cases.fields) always wins: these names are the fallback for a column the
project did not map, and the proposal `adapters.excel.inspect` makes at the onboarding.

    field_of("Resultado Esperado") -> "expected"      norm("Título") -> "titulo"
"""

from __future__ import annotations

import re
import unicodedata

# The headers a column is recognised by when the project does not map it (excel.cases.fields): English first, then
# the same names in Portuguese, Spanish, French, German and Italian. Compared by norm() (case, accents, spaces, '_' and
# '-' ignored), so "Título", "titulo" and "TITULO" are the same header.
HEADERS = {
    "id": ("id", "case", "case id", "test case id", "test id", "tc", "key",
           "caso", "id caso", "id do caso", "id del caso", "cas", "id cas", "identifiant", "fall id", "testfall id"),
    "title": ("title", "name", "summary", "test case", "case title",
              "titulo", "nome", "nombre", "titre", "nom", "titel", "titolo", "resumo", "resumen"),
    "expected": ("expected", "expected result", "expected results",
                 "resultado esperado", "resultados esperados", "resultat attendu", "resultats attendus",
                 "erwartetes ergebnis", "erwartete ergebnisse", "risultato atteso", "risultati attesi"),
    "steps": ("steps", "step", "test steps", "action", "actions",
              "passos", "pasos", "etapes", "schritte", "testschritte", "passi", "acoes", "acciones"),
    "preconditions": ("preconditions", "precondition", "pre conditions", "pre condition",
                      "pre condicoes", "precondicoes", "pre condicao", "pre requisitos", "prerequisitos",
                      "precondiciones", "precondicion", "prerrequisitos", "prerequis", "vorbedingungen",
                      "precondizioni"),
    "role": ("role", "profile", "user role", "perfil", "papel", "rol", "rolle", "ruolo", "profil"),
    "platform": ("platform", "plataforma", "plateforme", "plattform", "piattaforma"),
    "area": ("area", "feature", "funcionalidade", "funcionalidad", "fonctionnalite", "bereich", "funzionalita"),
    "priority": ("priority", "prioridade", "prioridad", "priorite", "prioritat", "priorita"),
    "module": ("module", "test module", "modulo", "modul"),
    "technique": ("technique", "design technique", "tecnica", "technik"),
}
def norm(header) -> str:
    """A header as it is compared: lower case, without accents, spaces, '_' and '-' as one space."""
    text = unicodedata.normalize("NFKD", str(header or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[\s_-]+", " ", text).strip().lower()


_HEADER_OF = {norm(name): field for field, names in HEADERS.items() for name in names}


def field_of(header) -> str | None:
    """The field of the case model a header names, or None."""
    return _HEADER_OF.get(norm(header))
