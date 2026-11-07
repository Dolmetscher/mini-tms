@echo off
rem Локальная сборка Mini-TMS CAT для Windows x64 (GNU-тулчейн).
rem CI (GitHub Actions) собирает через MSVC и этих шагов не требует.
setlocal
set MINGW_BIN=C:\Users\dolme\tools\mingw-ucrt\ucrt64\bin
set SELFCONTained=%USERPROFILE%\.rustup\toolchains\stable-x86_64-pc-windows-gnu\lib\rustlib\x86_64-pc-windows-gnu\bin\self-contained
set PATH=%MINGW_BIN%;%SELFCONTained%;%PATH%
rem Линкер — self-contained gcc: у msys2 gcc нет CRT-импорт-либ (mingwex/kernel32),
rem а windres из него же требует рабочий gcc для препроцессинга resource.rc.
set CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER=%SELFCONTained%\x86_64-w64-mingw32-gcc.exe
pushd "%~dp0..\web"
call npm run build || goto :err
popd
pushd "%~dp0"
call npx tauri build || goto :err
popd
echo.
echo Artifacts: desktop\src-tauri\target\release\bundle\
goto :eof
:err
popd
exit /b 1
