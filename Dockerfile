# =============================================================================
# 青龙面板定制镜像 - A009 项目专用
# 优化版本 v2.0 - 2026-05-01
# 优化内容：
#   1. BuildKit 缓存挂载加速依赖安装
#   2. 层合并减少镜像体积
#   3. 安装全部依赖（含 numpy, opencv, requests）
#   4. 挂载点优化
# =============================================================================

# 基础镜像
FROM whyour/qinglong:debian

# 防止交互式安装提示
ENV DEBIAN_FRONTEND=noninteractive
# 设置时区
ENV TZ=Asia/Shanghai

# =============================================================================
# 优化一：使用 BuildKit 缓存挂载加速依赖安装
# 关键：--mount=type=cache 缓存 apt/pip 下载，下次构建直接使用
# =============================================================================
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    \
    echo "=== 步骤1/4：安装系统依赖 ===" && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        # 视频处理
        ffmpeg \
        libavcodec-extra \
        # 图像处理依赖
        libsm6 \
        libxext6 \
        libxrender-dev \
        libgomp1 \
        # OpenCV 依赖
        libgl1-mesa-glx \
        libglib2.0-0 \
        # 中文字体支持
        fonts-noto-cjk \
        # 工具
        curl \
        wget \
        git \
        && \
    echo "=== 步骤2/4：安装 Python 核心依赖 ===" && \
    pip install --no-cache-dir \
        # 系统资源监控
        psutil>=5.9.0 \
        # 图像处理
        Pillow>=10.0.0 \
        pyheif>=0.1.0 \
        # 音频/视频元数据
        mutagen>=1.47.0 \
        # 中文简繁转换
        opencc-python-reimplemented>=0.1.7 \
        zhconv>=1.2 \
        # 日期/日历
        python-dateutil>=2.8.0 \
        chinese-calendar>=1.9 \
        zhdate>=2.1.0 \
        # YAML 配置
        pyyaml>=6.0 \
        # HTTP 请求
        requests>=2.28.0 && \
    echo "=== 步骤3/4：安装 Python 可选依赖 ===" && \
    pip install --no-cache-dir \
        # numpy（图像处理加速）
        numpy>=1.24.0 \
        # OpenCV（人脸检测、视频封面）
        opencv-python>=4.5.0 && \
    echo "=== 步骤4/4：清理缓存并配置权限 ===" && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* && \
    echo "=== 构建完成 ==="

# =============================================================================
# 项目初始化脚本（青龙面板专用）
# =============================================================================
COPY A009-Init.sh /ql/docker/A009-Init.sh
RUN chmod +x /ql/docker/A009-Init.sh

# =============================================================================
# 环境变量配置
# =============================================================================
ENV PROJECT_NAME=A009-QingLong
ENV LOG_LEVEL=INFO
ENV MAX_WORKERS=4
ENV TZ=Asia/Shanghai

# =============================================================================
# 暴露端口
# =============================================================================
EXPOSE 5700

# =============================================================================
# 挂载优化：定义青龙面板核心数据卷挂载点
# 建议在 docker-compose.yml 中按需挂载
# =============================================================================
# /ql/config   - 配置文件
# /ql/data     - 应用数据
# /ql/db       - SQLite 数据库
# /ql/jbot     - 机器人相关
# /ql/log      - 日志文件
# /ql/raw      - 原始脚本
# /ql/repo     - 仓库克隆
# /ql/scripts  - 脚本目录
# /source      - 外部源文件（建议只读挂载）
# =============================================================================

# 入口点由青龙面板镜像提供
