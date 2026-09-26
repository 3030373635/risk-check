@echo off
setlocal

rem 只以脚本所在目录为发布根，支持中文和空格路径。
for %%I in ("%~dp0.") do set "APP_ROOT=%%~fI"
set "PYTHON_EXECUTABLE=%APP_ROOT%\runtime\python\python.exe"
set "RISK_AUDIT_APP_ROOT=%APP_ROOT%"
set "PYTHONPATH=%APP_ROOT%\app"
set "PYTHONUTF8=1"
set "PYTHONNOUSERSITE=1"
set "PYTHONDONTWRITEBYTECODE=1"

if not exist "%PYTHON_EXECUTABLE%" (
  echo 发布包不完整：缺少 runtime\python\python.exe
  pause
  exit /b 1
)

rem 不使用 start 或 pythonw，终端关闭时让 Python 一同结束。
"%PYTHON_EXECUTABLE%" -m risk_audit_web.app
set "APP_EXIT_CODE=%ERRORLEVEL%"
if not "%APP_EXIT_CODE%"=="0" pause
exit /b %APP_EXIT_CODE%
