@echo off
cd /d "%~dp0"
echo Building Whisperer.exe - this can take 20-45 minutes...
echo.
pyinstaller --noconfirm whisperer.spec
echo.
if exist "dist\Whisperer\Whisperer.exe" (
    echo SUCCESS: dist\Whisperer\Whisperer.exe
    echo.
    echo Next step: build the installer with Inno Setup:
    echo   iscc installer.iss
) else (
    echo Build did not produce dist\Whisperer\Whisperer.exe - check errors above.
)
pause
