# PyInstaller build definition. Run with: pyinstaller --clean mpops.spec
from PyInstaller.utils.hooks import collect_data_files


datas = collect_data_files("app", includes=["resources/*.png"])

a = Analysis(
    ["app/main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="LunaTech-3D-Ops",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
