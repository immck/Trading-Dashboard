@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
rem 端口由服务自己挑（Hyper-V 会动态保留端口段），启动后自动打开浏览器
set TD_OPEN=1

rem 依次尝试 py 启动器 / python / DSH 内置解释器
where py >nul 2>nul
if %errorlevel%==0 (
    py server.py
    goto :eof
)
where python >nul 2>nul
if %errorlevel%==0 (
    python server.py
    goto :eof
)
set "DSHPY=C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if exist "%DSHPY%" (
    "%DSHPY%" server.py
    goto :eof
)

echo 未找到 Python，请安装 Python 3.10+ 或修改本脚本里的解释器路径
pause
