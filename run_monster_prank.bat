@echo off
where pythonw >nul 2>&1
if not errorlevel 1 (
    start "" pythonw "%~dp0monster_prank.py"
) else (
    python "%~dp0monster_prank.py"
)
