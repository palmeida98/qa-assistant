"""
Recalculo seguro de formulas de um workbook (tecnica-excel.md secao 2).

Regra de ouro: nunca recalcular diretamente num caminho dentro de uma pasta
sincronizada ou montada. recalc_safe() copia sempre o ficheiro para uma pasta
temporaria local, recalcula la e so depois copia o resultado de volta.

Mecanismos:
- "libreoffice" (por omissao): soffice --headless com um perfil isolado que
  forca o recalculo ao abrir (OOXMLRecalcMode=0) e regrava em .xlsx.
- "excel-com" (opcional, so Windows com Excel desktop): automacao COM via
  pywin32, corrida num processo filho para o timeout ser efetivo.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile

MECANISMOS = ("libreoffice", "excel-com")

_SOFFICE_CANDIDATES = [
    r"C:\Program Files\LibreOffice\program\soffice.com",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.com",
    "/usr/bin/soffice",
    "/usr/local/bin/soffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
]

# OOXMLRecalcMode / ODFRecalcMode: 0 = sempre, 1 = nunca, 2 = perguntar.
# Sem isto o LibreOffice pode reaproveitar valores em cache em vez de recalcular.
_SOFFICE_PROFILE_XCU = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>0</value></prop></item>
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="ODFRecalcMode" oor:op="fuse"><value>0</value></prop></item>
</oor:items>
"""

# Corre num processo filho: o COM nao tem timeout proprio, e um Excel pendurado
# num dialogo bloquearia o processo principal para sempre.
_COM_SCRIPT = r"""
import sys
import pythoncom
import win32com.client as win32
pythoncom.CoInitialize()
excel = None
try:
    excel = win32.DispatchEx("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    wb = excel.Workbooks.Open(sys.argv[1])
    excel.CalculateFullRebuild()
    wb.Save()
    wb.Close(SaveChanges=False)
finally:
    if excel is not None:
        excel.Quit()
    pythoncom.CoUninitialize()
"""


def find_soffice() -> str:
    found = shutil.which("soffice")
    if found:
        return found
    for cand in _SOFFICE_CANDIDATES:
        if os.path.exists(cand):
            return cand
    raise FileNotFoundError(
        "LibreOffice (soffice) nao encontrado -- instalar (ex.: "
        "`winget install TheDocumentFoundation.LibreOffice`) ou usar mecanismo 'excel-com'."
    )


def _run_with_timeout(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    # fora do Windows, num grupo de processos proprio: soffice lanca o soffice.bin, que herda os
    # pipes; matar so o pai deixava o communicate() a espera do filho pendurado, sem timeout
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=os.name != "nt")
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # soffice.com / Excel lancam processos filhos -- matar a arvore toda
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False)
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.communicate()
        raise TimeoutError(f"recalculo nao terminou em {timeout}s") from None
    return proc.returncode, stdout, stderr


def _recalc_libreoffice(path: str, timeout: int) -> None:
    soffice = find_soffice()
    with tempfile.TemporaryDirectory() as work:
        profile = os.path.join(work, "profile")
        os.makedirs(os.path.join(profile, "user"))
        with open(os.path.join(profile, "user", "registrymodifications.xcu"), "w", encoding="utf-8") as f:
            f.write(_SOFFICE_PROFILE_XCU)
        outdir = os.path.join(work, "out")
        os.makedirs(outdir)
        cmd = [
            soffice,
            "-env:UserInstallation=" + pathlib.Path(profile).as_uri(),
            "--headless", "--norestore", "--nologo", "--nodefault",
            "--calc", "--convert-to", "xlsx:Calc MS Excel 2007 XML",
            "--outdir", outdir, os.path.abspath(path),
        ]
        rc, stdout, stderr = _run_with_timeout(cmd, timeout)
        out_path = os.path.join(outdir, os.path.splitext(os.path.basename(path))[0] + ".xlsx")
        if rc != 0 or not os.path.exists(out_path):
            raise RuntimeError(f"LibreOffice falhou (rc={rc}): {stdout.strip()!r} / {stderr.strip()!r}")
        shutil.copy(out_path, path)


def _recalc_excel_com(path: str, timeout: int) -> None:
    if os.name != "nt":
        raise RuntimeError("mecanismo 'excel-com' so existe no Windows")
    rc, _stdout, stderr = _run_with_timeout(
        [sys.executable, "-c", _COM_SCRIPT, os.path.abspath(path)], timeout
    )
    if rc != 0:
        raise RuntimeError(f"Excel COM falhou (rc={rc}): {stderr.strip()[-500:]!r}")


def recalc_safe(path: str, mecanismo: str = "libreoffice", timeout: int = 60) -> dict:
    """Recalcula as formulas de `path` numa copia temporaria local e, se tudo
    correr bem, substitui o original pelo resultado. Devolve
    {"ok": True, "mecanismo": ...} ou {"ok": False, "mecanismo": ..., "erro": ...};
    em caso de erro o original fica intacto."""
    if mecanismo not in MECANISMOS:
        return {"ok": False, "mecanismo": mecanismo, "erro": f"mecanismo desconhecido (validos: {MECANISMOS})"}
    fn = _recalc_libreoffice if mecanismo == "libreoffice" else _recalc_excel_com
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = os.path.join(tmp, os.path.basename(path))
        shutil.copy(path, tmp_path)
        try:
            fn(tmp_path, timeout)
        except Exception as e:  # noqa: BLE001 -- qualquer falha vira ok=False; o original fica intacto
            return {"ok": False, "mecanismo": mecanismo, "erro": str(e)}
        shutil.copy(tmp_path, path)
    return {"ok": True, "mecanismo": mecanismo}
