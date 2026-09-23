# One-file console worker copied beside both app entry points.
from pathlib import Path
repository = Path(SPECPATH).resolve().parents[1]
core_scripts = repository / ".agents/skills/processing-bond-seal-pages/scripts"
worker = core_scripts / "bond_seal_pages/word_worker.py"
a = Analysis([str(worker)], pathex=[str(core_scripts)], binaries=[], datas=[], hiddenimports=["pythoncom", "pywintypes", "win32timezone", "win32com.client"], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=["pytest", "IPython", "jupyter", "matplotlib"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, name="BondSealWordWorker", debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True, disable_windowed_traceback=False, argv_emulation=False, target_arch="x86_64", codesign_identity=None, entitlements_file=None)
