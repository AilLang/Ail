@echo off
set PYTHONPATH=%cd%\..
python -m nuitka --standalone --onefile ..\ail\targetail.py -o ail.exe --include-data-dir=..\ail\lib=lib
