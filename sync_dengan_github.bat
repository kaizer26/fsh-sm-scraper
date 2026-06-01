@echo off
setlocal
echo ===============================================
echo   Sync GitHub - Push ^& Pull - FASIH Scraper
echo ===============================================
echo.

:: 1. Add all changes
echo Menambahkan perubahan lokal...
git add .

:: 2. Pre-filled commit message
set "commit_msg=Tambah fitur History Email Broadcast dan sheet Settings pada scrape excel"
echo Menggunakan pesan commit: "%commit_msg%"

:: 3. Commit
echo.
echo Melakukan commit...
git commit -m "%commit_msg%"

:: 4. Push to origin main
echo.
echo Sedang mengunggah (push) perubahan ke GitHub...
git push origin main

:: 5. Pull from origin main
echo.
echo Sedang mengunduh (pull) perubahan terbaru dari GitHub...
git pull origin main

echo.
echo Sinkronisasi selesai!
pause
endlocal
