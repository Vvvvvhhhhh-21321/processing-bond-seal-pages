# PyInstaller onedir bundle for the console automation host.
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_submodules
repository = Path(SPECPATH).resolve().parents[1]
desktop = repository / "desktop"
core_scripts = repository / ".agents/skills/processing-bond-seal-pages/scripts"
sys.path.insert(0, str(desktop))
from bondseal_app.runtime import core_hidden_imports
a = Analysis([str(desktop / "bondseal_app/cli_entry.py")], pathex=[str(desktop), str(core_scripts)], binaries=[], datas=[], hiddenimports=core_hidden_imports() + collect_submodules("win32com.client"), hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=["pytest", "IPython", "jupyter", "matplotlib"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="BondSealCLI", debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True, disable_windowed_traceback=False, argv_emulation=False, target_arch="x86_64", codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="BondSealCLI")
