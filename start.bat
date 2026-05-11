@echo off
chcp 65001 >nul
echo ===================================================
echo     Запуск нарезки трейлеров (FFmpeg + Python)
echo ===================================================
echo.

:: Проверка установки Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ОШИБКА] Python не найден! 
    echo Установите Python с официального сайта (python.org^) и обязательно поставьте галочку "Add Python to PATH" при установке.
    echo.
    pause
    exit /b
)

:: Проверка и установка PyYAML
python -c "import yaml" >nul 2>&1
if %errorlevel% neq 0 (
    echo [ИНФО] Библиотека PyYAML не найдена. Начинаю установку...
    pip install pyyaml
)

:: Запуск скрипта
echo.
echo [ИНФО] Запускаем скрипт обработки...
python cut_and_insert.py -c config.yaml

echo.
echo ===================================================
echo     Работа завершена!
echo ===================================================
pause
