@echo off
rem Use the signed official switcher; direct engine launch returned -2001.
rem A switcher exit code of zero does not prove the game or replay is ready.
setlocal
set "SCV_RUNTIME=%~dp0cache\sc2-international-93333"
if not exist "%SCV_RUNTIME%\Versions\Base93333\SC2_x64.exe" (
  echo Missing SC2 Base93333 executable.
  pause
  exit /b 1
)
if not exist "%SCV_RUNTIME%\Support64\SC2Switcher_x64.exe" (
  echo Missing official SC2Switcher_x64.exe.
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
start "" /wait "%SCV_RUNTIME%\Support64\SC2Switcher_x64.exe" -dataVersion 446907060311FB1CC29EB31E547BB9FD -displayMode 0 -windowwidth 1280 -windowheight 720
set "SCV_EXIT=%errorlevel%"
echo Switcher exited. Exit code: %SCV_EXIT%
echo The game may still be running. This is NOT the game's exit code.
pause
exit /b %SCV_EXIT%
