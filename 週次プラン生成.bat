@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo ============================================================
echo   スポカフェ 週次定型プラン生成
echo ============================================================
echo.

rem 担当者を選択
echo 担当者を選択してください:
echo   [1] 鈴村
echo   [2] 鈴木
echo   [3] その他
set /p OWNER_SEL="  番号を入力 (1/2/3): "

if "%OWNER_SEL%"=="1" set OWNER=鈴村
if "%OWNER_SEL%"=="2" set OWNER=鈴木
if "%OWNER_SEL%"=="3" (
    set /p OWNER="  名前を入力: "
)
if not defined OWNER set OWNER=未設定

echo.
echo 活動時間帯（デフォルト: 17:00〜21:00）
set /p W_START="  開始時刻 (空Enter=17:00): "
set /p W_END="  終了時刻 (空Enter=21:00): "

if "%W_START%"=="" set W_START=17:00
if "%W_END%"=="" set W_END=21:00

echo.
echo 担当: %OWNER%  時間帯: %W_START%〜%W_END%
echo.

python _scripts\プラン週次生成.py --auto --owner %OWNER% --window_start %W_START% --window_end %W_END%

echo.
echo 生成完了。_output\route_plans\ フォルダを確認してください。
pause
