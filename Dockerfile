# =============================================================================
# 青龙面板定制镜像 - A009 项目专用
# 基于 whyour/qinglong:latest 构建
# =============================================================================
FROM whyour/qinglong:latest

ENV DEBIAN_FRONTEND=noninteractive

# 系统依赖安装
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libavcodec-extra \
    libsm6 libxext6 libxrender-dev libgomp1 \
    libgl1-mesa-glx libglib2.0-0 \
    fonts-noto-cjk curl wget git \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖安装
RUN pip install --no-cache-dir \
    psutil>=5.9.0 \
    Pillow>=10.0.0 pyheif>=0.1.0 \
    mutagen>=1.47.0 \
    opencc-python-reimplemented>=0.1.7 zhconv>=1.2 \
    python-dateutil>=2.8.0 chinese-calendar>=1.9 zhdate>=2.1.0 \
    pyyaml>=6.0 requests>=2.28.0 numpy>=1.24.0 \
    opencv-python>=4.5.0

COPY A009-Init.sh /ql/docker/A009-Init.sh
RUN chmod +x /ql/docker/A009-Init.sh

ENV PROJECT_NAME=A009-QingLong LOG_LEVEL=INFO MAX_WORKERS=4 TZ=Asia/Shanghai

EXPOSE 5700
