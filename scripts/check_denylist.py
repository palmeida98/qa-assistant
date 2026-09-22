#!/usr/bin/env python3
"""Bloqueia commits que contenham nomes de clientes/empresas da denylist pessoal.
A lista fica em ~/.qa-denylist.txt (uma entrada por linha), fora do repositorio.
"""
import sys
from pathlib import Path

DENYLIST_PATH = Path.home() / ".qa-denylist.txt"

def load_denylist():
    if not DENYLIST_PATH.exists():
        return []
    lines = DENYLIST_PATH.read_text(encoding="utf-8").splitlines()
    return [l.strip().lower() for l in lines if l.strip() and not l.strip().startswith("#")]

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
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
        except Exception:
            continue
        for term in denylist:
            if term in text:
                print(f"BLOQUEADO: '{term}' encontrado em {filename}")
                found = True

    if found:
        print("\nCommit recusado: remove estas referencias antes de continuar.")
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
