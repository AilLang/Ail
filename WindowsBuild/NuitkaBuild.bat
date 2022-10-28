@echo off
python -m nuitka --standalone ..\ail\targetail.py -o ail.exe --include-data-dir=..\ail\lib=lib
