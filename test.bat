@echo off
IF "%~1"=="" (
    pytest tests/ -s -v --durations 0 -W ignore::DeprecationWarning
) ELSE (
    pytest tests/ -s -v --durations 0 -W ignore::DeprecationWarning -k %~1
)