# A009 青龙面板定制镜像 - 构建与部署指南

## GitHub Actions 自动构建

推送到 main 分支将自动触发构建。

镜像地址：`ghcr.io/s2662939/a009-qinglong`

## 依赖列表

### Python
- psutil, Pillow, pyheif, mutagen
- opencc-python-reimplemented, chinese-calendar, zhdate
- pyyaml, requests, numpy, opencv-python

### 系统
- ffmpeg, libavcodec-extra
- fonts-noto-cjk

## 环境变量

| 变量 | 说明 |
|------|------|
| WXPUSHER_APP_TOKEN | WxPusher 令牌 |
| SYNOLOGY_USERNAME | 群晖用户名 |
| SMTP_SERVICE | 邮件服务 |
