#!/usr/bin/env python3
"""Bloqueia commits que contenham nomes de clientes/empresas da denylist pessoal.
A lista fica em ~/.qa-denylist.txt (uma entrada por linha), fora do repositorio;
QA_DENYLIST aponta para outra lista (usado nos testes).

Verifica o caminho de cada ficheiro e o seu texto. Os ficheiros Office (.xlsx,
.docx, .pptx, ...) sao ZIP comprimidos: o texto le-se de dentro do ZIP, senao um
workbook de cliente passava sem ser visto.
"""
import os
import sys
import zipfile
from pathlib import Path

DENYLIST_PATH = Path(os.environ.get("QA_DENYLIST") or Path.home() / ".qa-denylist.txt")
OFFICE_ZIP = {".xlsx", ".xlsm", ".xltx", ".docx", ".dotx", ".pptx", ".potx"}


def load_denylist():
    if not DENYLIST_PATH.exists():
        return []
    lines = DENYLIST_PATH.read_text(encoding="utf-8-sig").splitlines()
    return [l.strip().lower() for l in lines if l.strip() and not l.strip().startswith("#")]


def file_text(path: Path) -> str:
    if path.suffix.lower() in OFFICE_ZIP and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            return "\n".join(z.read(n).decode("utf-8", errors="ignore") for n in z.namelist())
    return path.read_text(encoding="utf-8", errors="ignore")


def main():
    denylist = load_denylist()
    if not denylist:
        return 0

    found = False
    for filename in sys.argv[1:]:
        path = Path(filename)
        if not path.is_file():
            continue
        try:
            text = file_text(path).lower()
        except (OSError, zipfile.BadZipFile):  # ficheiro ilegivel nao tem texto a verificar
            text = ""
        name = filename.replace("\\", "/").lower()
        for term in denylist:
            if term in name:
                print(f"BLOQUEADO: '{term}' no nome/caminho {filename}")
                found = True
            if term in text:
                print(f"BLOQUEADO: '{term}' encontrado em {filename}")
                found = True

    if found:
        print("\nCommit recusado: remove estas referencias antes de continuar.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
