@echo off
rem Starts Skill Review in a minimized window and opens it in your browser.
rem Close the "Skill Review" window (or use Settings > Stop the app) to stop it.
cd /d "%~dp0"
start "Skill Review" /min py -3 server.py --open
