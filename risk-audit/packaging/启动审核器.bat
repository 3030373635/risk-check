@echo off
chcp 65001 >nul
setlocal

rem 只以脚本所在目录为发布根，支持中文和空格路径。
for %%I in ("%~dp0.") do set "APP_ROOT=%%~fI"
set "PYTHON_RUNTIME_ROOT=%APP_ROOT%\runtime\python"
set "PYTHON_EXECUTABLE=%PYTHON_RUNTIME_ROOT%\python.exe"
set "RISK_AUDIT_APP_ROOT=%APP_ROOT%"
set "PYTHONPATH=%APP_ROOT%\app"
set "PYTHONUTF8=1"
set "PYTHONNOUSERSITE=1"
set "PYTHONDONTWRITEBYTECODE=1"

if not exist "%PYTHON_EXECUTABLE%" (
  echo 发布包不完整：缺少 runtime\python\python.exe
  echo 请完整解压 ZIP 后，再从解压目录启动。
  pause
  exit /b 1
)

rem 在调用 Python 前检查核心 DLL，避免 Windows 只弹出无法定位原因的系统错误。
for %%F in (python311.dll vcruntime140.dll vcruntime140_1.dll) do (
  if not exist "%PYTHON_RUNTIME_ROOT%\%%F" (
    echo 发布包不完整：缺少 runtime\python\%%F
    echo 请完整解压 ZIP；如已完整解压，请检查安全软件的隔离记录。
    pause
    exit /b 1
  )
)

rem 不使用 start 或 pythonw，终端关闭时让 Python 一同结束。
"%PYTHON_EXECUTABLE%" -m risk_audit_web.app
set "APP_EXIT_CODE=%ERRORLEVEL%"
if not "%APP_EXIT_CODE%"=="0" pause
exit /b %APP_EXIT_CODE%
