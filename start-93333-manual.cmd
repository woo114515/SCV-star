@echo off
rem Diagnostic entry only: direct launch also returns -2001 on working 97563.
rem This file is not a verified manual replay launcher.
setlocal
set "SCV_RUNTIME=%~dp0cache\sc2-international-93333"
if not exist "%SCV_RUNTIME%\Versions\Base93333\SC2_x64.exe" (
  echo Missing SC2 Base93333 executable.
  pause
  exit /b 1
)
cd /d "%SCV_RUNTIME%\Support64"
if errorlevel 1 (
  echo Cannot enter Support64 directory.
  pause
  exit /b 1
)
echo Starting SC2 5.0.14.93333. Keep this window open.
start "" /wait "%SCV_RUNTIME%\Versions\Base93333\SC2_x64.exe" -dataDir "%SCV_RUNTIME%/" -dataVersion 446907060311FB1CC29EB31E547BB9FD -displayMode 0 -windowwidth 1280 -windowheight 720
set "SCV_EXIT=%errorlevel%"
echo SC2 process exited. Exit code: %SCV_EXIT%
pause
exit /b %SCV_EXIT%
