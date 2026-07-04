"""Generate icon.png from haitun.ico for electron-builder packaging."""

import sys
from pathlib import Path

from PIL import Image

SRC = Path(".github/inno-setup/haitun.ico")
DST = Path("src/psi_agent/gateway/electron/assets/icon.png")

DST.parent.mkdir(parents=True, exist_ok=True)

img = Image.open(SRC)
img = img.resize((512, 512), Image.Resampling.LANCZOS)
img.save(DST)
print(f"Icon saved: {DST} ({img.size})")
