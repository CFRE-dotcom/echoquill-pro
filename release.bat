@echo off
REM ============================================================
REM  One-step release. After you bump version.txt + changelog,
REM  double-click this. It commits everything, tags the version,
REM  and pushes. GitHub then builds EchoQuill.exe + the installer
REM  and publishes the release automatically (see .github\workflows\
REM  build.yml), and installed apps auto-update from it.
REM  You never run build_exe.bat or Inno Setup by hand again.
REM ============================================================
setlocal
cd /d "%~dp0"
set /p EQVER=<version.txt
echo.
echo === Releasing EchoQuill Pro v%EQVER% ===
echo.

REM refuse to reuse a version that already shipped
git rev-parse -q --verify "refs/tags/v%EQVER%" >nul
if %errorlevel%==0 (
    echo ERROR: tag v%EQVER% already exists.
    echo Bump version.txt ^(and echoquill\__init__.py + changelog^) first.
    echo.
    pause
    exit /b 1
)

git add -A
git commit -m "Release v%EQVER%"
git tag v%EQVER%
git push origin main
if errorlevel 1 ( echo. & echo Push of main FAILED - see above. & pause & exit /b 1 )
git push origin v%EQVER%
if errorlevel 1 ( echo. & echo Push of tag FAILED - see above. & pause & exit /b 1 )

echo.
echo ============================================================
echo  Pushed v%EQVER%. GitHub is now building + publishing it.
echo  Watch progress here:
echo    https://github.com/CFRE-dotcom/echoquill-pro/actions
echo  When it finishes, the release appears here:
echo    https://github.com/CFRE-dotcom/echoquill-pro/releases
echo ============================================================
pause
