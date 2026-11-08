@echo off
chcp 65001 >nul
title 温迪在看什么
set "ROOT=%~dp0"
"%~dp0..\tools\Scripts\python.exe" "%ROOT%watch.py"
pause
