@echo off
cd /d "%~dp0"
python -m venv .venv
call .venv\Scripts\activate
python -m pip install -U pip
pip install -r requirements.txt pyinstaller
python -c "from PIL import Image; im=Image.open('assets/jujune_icon.png').convert('RGBA'); im.save('assets/jujune.ico', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])"
pyinstaller --noconfirm jujune.spec
echo.
echo Built: dist\Jujune.exe
echo Keep it running in the tray while you play.
pause
