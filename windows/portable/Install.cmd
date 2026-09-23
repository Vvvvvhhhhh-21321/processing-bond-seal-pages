@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install.ps1"
if errorlevel 1 (
  echo 安装失败。请检查上面的提示。
  pause
  exit /b 1
)
pause
