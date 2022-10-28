@echo off
set PYTHONPATH=%cd%\..\ail
python -m nuitka --standalone --onefile ..\ail\targetail.py -o ail.exe --include-data-dir=..\ail\lib=lib
