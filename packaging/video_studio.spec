# PyInstaller spec: one-folder build (fast start, no unpacking to %TEMP% on every run).
# Build with packaging\build.ps1, which creates an isolated build venv first.
from pathlib import Path

ROOT = Path(SPECPATH).parent
PKG = ROOT / "video_studio"

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    datas=[
        (str(PKG / "blender" / "scene_builder.py"), "blender"),
        *[(str(f), "helpers") for f in sorted((PKG / "helpers").glob("*_helper.py"))],
        (str(PKG / "studio_ui" / "index.html"), "studio_ui"),
        (str(PKG / "assets" / "fonts"), "assets/fonts"),
        (str(PKG / "assets" / "english_words.txt"), "assets"),
    ],
    hiddenimports=["video_studio.editor", "video_studio.studio", "video_studio.engines.hyperframes", "video_studio.brain",
                   "video_studio.learn", "video_studio.setup", "video_studio.engines.sense", "video_studio.engines.style",
                   "video_studio.engines.styledna", "video_studio.shorts", "video_studio.vocab", "video_studio.watch", "video_studio.engines.arranger", "video_studio.dub", "video_studio.story", "video_studio.broll"],
    excludes=["tkinter", "unittest", "pydoc_data", "matplotlib", "scipy", "torch", "cv2", "pandas", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="video-studio",
    console=True,
    icon=None,
    version=str(ROOT / "packaging" / "version_info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="VideoStudio", strip=False, upx=False)
