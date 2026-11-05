@echo off
rem Launch the coverage helper GUI. Arguments are passed through (start.cmd example.c --func example)
setlocal
pushd "%~dp0"
where uv >nul 2>&1
if errorlevel 1 (
  echo uv is required: https://docs.astral.sh/uv/getting-started/installation/
  popd
  pause
  exit /b 1
)
uv run --locked python branch_gui.py %*
set "tool_exit=%errorlevel%"
popd
if not "%tool_exit%"=="0" pause
exit /b %tool_exit%
