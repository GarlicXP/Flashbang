@echo off
echo Installing dependencies...
pip install pynput pycaw pystray pillow pywin32 pyinstaller

echo Building executable...
pyinstaller --onefile --windowed --name FlashLight --add-data "flashlight.wav;." FlashLight.py

echo Build complete. The executable is in the "dist" folder.
pause