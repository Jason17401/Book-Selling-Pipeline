@echo off
rem Double-click me: reads the Shopee reports you put in data\listings\shopee\import, then makes the Shopee
rem bulk-upload file (and photos) for every set that is ready. Upload that file in Shopee Seller Center.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m pipeline shopee --open
pause
