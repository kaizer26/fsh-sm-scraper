@echo off
setlocal
echo ========================================
echo   Auto Push ke GitHub - FASIH Scraper
echo ========================================
echo.

:: Menambahkan semua file yang berubah
git add .

:: Meminta input pesan commit
set "commit_msg=Update script otomatis"
set /p "commit_msg=Masukkan pesan commit (atau tekan Enter untuk default '%commit_msg%'): "

:: Melakukan commit
git commit -m "%commit_msg%"

:: Mendorong (push) ke branch main di GitHub
echo.
echo Sedang mengunggah ke GitHub...
git push origin main

echo.
echo Push selesai!
pause
endlocal
