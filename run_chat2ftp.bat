@echo off
REM Runs the app straight from Python - no exe needed. Use this to check the
REM app itself works before blaming the build.
py chat2ftp.py
if errorlevel 1 pause
