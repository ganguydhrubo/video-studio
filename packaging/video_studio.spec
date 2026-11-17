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
        *[(str(f), "studio_ui") for f in sorted((PKG / "studio_ui").glob("*.js"))],
        (str(PKG / "assets" / "fonts"), "assets/fonts"),
        (str(PKG / "assets" / "english_words.txt"), "assets"),
        (str(PKG / "assets" / "provider-icons" / "LICENSE"), "licenses/provider-icons"),
        (str(PKG / "assets" / "app-icon.ico"), "assets"),
        (str(PKG / "assets" / "takebloom-logo.png"), "assets"),
    ],
    hiddenimports=["video_studio.editor", "video_studio.studio", "video_studio.machine", "video_studio.library", "video_studio.updates", "video_studio.photos", "video_studio.restyle", "video_studio.cutout", "video_studio.autopilot", "video_studio.pictures", "video_studio.understand", "video_studio.engines.hyperframes", "video_studio.brain", "video_studio.speech", "video_studio.matting",
                   "video_studio.learn", "video_studio.setup", "video_studio.engines.sense", "video_studio.engines.style",
                   "video_studio.engines.styledna", "video_studio.shorts", "video_studio.vocab", "video_studio.watch", "video_studio.engines.arranger", "video_studio.dub", "video_studio.story", "video_studio.broll",
                   "video_studio.encoders", "video_studio.speed"],
    excludes=["tkinter", "unittest", "pydoc_data", "matplotlib", "scipy", "torch", "cv2", "pandas", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="video-studio",
    console=True,
    icon=str(ROOT / "branding" / "app-icon.ico"),
    version=str(ROOT / "packaging" / "version_info.txt"),
)
gui = Analysis([str(ROOT / "packaging" / "gui_entry.py")], pathex=[str(ROOT)],
               hiddenimports=["video_studio.launcher", "video_studio.taskbar"], excludes=a.excludes, noarchive=False)
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True,
              name="VideoStudio", console=False, icon=str(ROOT / "branding" / "app-icon.ico"),
              version=str(ROOT / "packaging" / "version_info.txt"))
coll = COLLECT(exe, gui_exe, a.binaries, a.datas, gui.binaries, gui.datas,
               name="VideoStudio", strip=False, upx=False)
