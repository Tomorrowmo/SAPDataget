@echo off
REM SAPDataget one-click launcher (double-click friendly).
REM Runs start.ps1 with ExecutionPolicy Bypass so it works even if PS scripts are blocked.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
pause
