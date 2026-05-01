#!/bin/bash
echo "=============================================="
echo "  A009-QingLong 项目初始化"
echo "=============================================="

echo "[1/4] 检查 Python 依赖..."
python3 -c "import psutil; print('  - psutil: OK')" || echo "  - psutil: 需要安装"
python3 -c "import PIL; print('  - Pillow: OK')" || echo "  - Pillow: 需要安装"
python3 -c "import mutagen; print('  - mutagen: OK')" || echo "  - mutagen: 需要安装"
python3 -c "import cv2; print('  - opencv-python: OK')" || echo "  - opencv-python: 需要安装"
python3 -c "import zhdate; print('  - zhdate: OK')" || echo "  - zhdate: 需要安装"
python3 -c "import chinese_calendar; print('  - chinese-calendar: OK')" || echo "  - chinese-calendar: 需要安装"

echo ""
echo "[2/4] 检查系统依赖..."
command -v ffmpeg >/dev/null 2>&1 && echo "  - ffmpeg: OK" || echo "  - ffmpeg: 需要安装"

echo ""
echo "=============================================="
echo "  初始化完成！"
echo "=============================================="
