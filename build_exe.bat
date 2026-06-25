@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo === 目标检测标注工具 - 打包单文件 EXE ===

where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 python，请先激活 conda/venv 环境。
    exit /b 1
)

echo [1/4] 检查依赖...
python -m pip install -q -r requirements.txt
python -m pip install -q pyinstaller

if not exist "app.ico" (
    echo [2/4] 生成默认 app.ico ...
    python -c "from PIL import Image,ImageDraw; s=[(16,16),(32,32),(48,48),(256,256)]; imgs=[]; \
for w,h in s: \
 im=Image.new('RGBA',(w,h),(0,120,215,255)); d=ImageDraw.Draw(im); m=max(2,w//8); \
 d.rounded_rectangle([m,m,w-m-1,h-m-1],radius=max(1,m//2),fill=(255,255,255,230)); \
 imgs.append(im); \
 imgs[0].save('app.ico',format='ICO',sizes=[(i.size[0],i.size[1]) for i in imgs])"
) else (
    echo [2/4] 使用现有 app.ico
)

echo [3/4] PyInstaller 打包（首次较慢，请耐心等待）...
python -m PyInstaller --noconfirm --clean build.spec
if errorlevel 1 (
    echo [错误] 打包失败。
    exit /b 1
)

echo [4/4] 完成: dist\AnnotationTool.exe
if exist app.ico copy /y app.ico dist\app.ico >nul
echo.
echo 说明:
echo   - 配置文件 config.json 会写在 exe 同目录
echo   - 请将 app.ico 放在 exe 同目录以显示窗口图标（打包时已嵌入 exe 图标）
echo.
pause
