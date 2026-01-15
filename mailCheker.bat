@echo off
REM Активируем виртуальное окружение
call .\vnf\Scripts\activate.bat

REM Запускаем скрипт Python
python main.py&"

REM Ждем 10 секунд
timeout /t 10

REM Деактивируем виртуальное окружение (опционально)
deactivate