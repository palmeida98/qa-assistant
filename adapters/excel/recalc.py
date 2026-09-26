"""
Safe recalculation of a workbook's formulas (technique-excel.md section 2).

Golden rule: never recalculate directly on a path inside a synced or mounted
folder. recalc_safe() always copies the file to a local temporary folder,
recalculates there and only then copies the result back.

Mechanisms:
- "libreoffice" (default): soffice --headless with an isolated profile that
  forces recalculation on open (OOXMLRecalcMode=0) and saves again as .xlsx.
- "excel-com" (optional, Windows with desktop Excel only): COM automation via
  pywin32, run in a child process so that the timeout is effective.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile

MECHANISMS = ("libreoffice", "excel-com")

_SOFFICE_CANDIDATES = [
    r"C:\Program Files\LibreOffice\program\soffice.com",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.com",
    "/usr/bin/soffice",
    "/usr/local/bin/soffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
]

# OOXMLRecalcMode / ODFRecalcMode: 0 = always, 1 = never, 2 = ask.
# Without this LibreOffice may reuse cached values instead of recalculating.
_SOFFICE_PROFILE_XCU = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>0</value></prop></item>
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="ODFRecalcMode" oor:op="fuse"><value>0</value></prop></item>
</oor:items>
"""

# Runs in a child process: COM has no timeout of its own, and an Excel stuck on a
# dialog would block the main process forever.
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
        "LibreOffice (soffice) not found -- install it (e.g. "
        "`winget install TheDocumentFoundation.LibreOffice`) or use the 'excel-com' mechanism."
    )


def _run_with_timeout(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    # outside Windows, in a process group of its own: soffice starts soffice.bin, which inherits the
    # pipes; killing only the parent left communicate() waiting for the stuck child, with no timeout
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=os.name != "nt")
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # soffice.com / Excel start child processes -- kill the whole tree
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False)
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.communicate()
        raise TimeoutError(f"recalculation did not finish in {timeout}s") from None
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
            raise RuntimeError(f"LibreOffice failed (rc={rc}): {stdout.strip()!r} / {stderr.strip()!r}")
        shutil.copy(out_path, path)


def _recalc_excel_com(path: str, timeout: int) -> None:
    if os.name != "nt":
        raise RuntimeError("the 'excel-com' mechanism exists only on Windows")
    rc, _stdout, stderr = _run_with_timeout(
        [sys.executable, "-c", _COM_SCRIPT, os.path.abspath(path)], timeout
    )
    if rc != 0:
        raise RuntimeError(f"Excel COM failed (rc={rc}): {stderr.strip()[-500:]!r}")


def recalc_safe(path: str, mechanism: str = "libreoffice", timeout: int = 60) -> dict:
    """Recalculates the formulas of `path` in a local temporary copy and, if
    all goes well, replaces the original with the result. Returns
    {"ok": True, "mechanism": ...} or {"ok": False, "mechanism": ..., "error": ...};
    on error the original is left untouched."""
    if mechanism not in MECHANISMS:
        return {"ok": False, "mechanism": mechanism, "error": f"unknown mechanism (valid: {MECHANISMS})"}
    fn = _recalc_libreoffice if mechanism == "libreoffice" else _recalc_excel_com
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = os.path.join(tmp, os.path.basename(path))
        shutil.copy(path, tmp_path)
        try:
            fn(tmp_path, timeout)
        except Exception as e:  # noqa: BLE001 -- any failure becomes ok=False; the original is untouched
            return {"ok": False, "mechanism": mechanism, "error": str(e)}
        shutil.copy(tmp_path, path)
    return {"ok": True, "mechanism": mechanism}
