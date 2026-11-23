@echo off
rem Daily Quickmart crawl from this PC (Quickmart blocks GitHub's servers).
rem Run by the Windows scheduled task "PriceWatch Quickmart" at 10:00; also fine to double-click.
rem Uses the database connection in .env. Output goes to logs\quickmart.log (last runs appended).

cd /d "%~dp0"
if not exist logs mkdir logs
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
set PY=%LOCALAPPDATA%\anaconda3\python.exe

echo ===== %date% %time% ===== >> logs\quickmart.log
"%PY%" -m pricewatch.run --only quickmart >> logs\quickmart.log 2>&1
rem Item list from the retailers' monthly files (folders in config\settings.json; unchanged files are cached),
rem then match every retailer's website products to it (GitHub's 06:00 crawl of Foodplus and Naivas is done by now).
"%PY%" -m pricewatch.items >> logs\quickmart.log 2>&1
"%PY%" -m pricewatch.link >> logs\quickmart.log 2>&1
"%PY%" -m pricewatch.refresh >> logs\quickmart.log 2>&1
echo ===== finished %date% %time% ===== >> logs\quickmart.log
