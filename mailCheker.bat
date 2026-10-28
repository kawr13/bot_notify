@echo off
setlocal

if exist ".\env\Scripts\python.exe" (
    ".\env\Scripts\python.exe" main.py
) else (
    echo Python virtual environment not found in .\env\Scripts\python.exe
    exit /b 1
)

endlocal
