@echo off
setlocal
pushd "%~dp0"
where uv >nul 2>&1
if errorlevel 1 (
  echo uv is required. Install uv, then run this launcher again.
  echo https://docs.astral.sh/uv/getting-started/installation/
  popd
  pause
  exit /b 1
)
uv run --locked decision-tool-gui %*
set "tool_exit=%errorlevel%"
popd
if not "%tool_exit%"=="0" pause
exit /b %tool_exit%
