[app]

# 应用基本信息
title = 川叶视频模块
package.name = chuanye_video
package.domain = com.chuanye
source.dir = .
source.include_exts = py,png,jpg,kv,atlas

# 主入口
main.py = main.py

# 版本
version = 2.0
version.code = 2

# 依赖（注意：beautifulsoup4 在 p4a 里要写 bs4！）
requirements = python3,kivy,requests,bs4,yt-dlp

# Android 权限
android.permissions = INTERNET,WRITE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE

# 架构（先只打 arm64-v8a，快很多）
android.arch = arm64-v8a

# 最低 API
android.minapi = 21
android.api = 33
android.ndk = 25b

# SDK 许可
android.accept_sdk_license = True

# Gradle 兼容 Java 11/17
android.gradle_dependencies = com.android.tools.build:gradle:7.3.3
p4a.branch = develop

# 图标和方向
orientation = portrait
fullscreen = 0

# 日志
log_level = 2

# 其他
android.allow_backup = True
android.presplash_color = #1A1A2E

# Buildozer 自身设置
[buildozer]
log_level = 2
warn_on_root = 1