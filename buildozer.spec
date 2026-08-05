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
android.api = 34
android.ndk = 25b

# SDK 许可
android.accept_sdk_license = True

# Gradle 兼容
# p4a 会自动选择合适的 Gradle 版本，无需手动指定

# 图标和方向
orientation = portrait
fullscreen = 0

# FileProvider 配置（Android 7+ 分享文件必需）
# ↑ p4a 的 android.providers 字段有兼容性问题，改用 extra_manifest_entries 直接注入 AndroidManifest
android.add_resources = res
android.gradle_dependencies = androidx.core:core:1.12.0
android.extra_manifest_entries = <provider android:name="androidx.core.content.FileProvider" android:authorities="chuanye_video.fileprovider" android:exported="false" android:grantUriPermissions="true"><meta-data android:name="android.support.FILE_PROVIDER_PATHS" android:resource="@xml/file_paths"/></provider>

# 日志
log_level = 2

# 其他
android.allow_backup = True
android.presplash_color = #1A1A2E

# Buildozer 自身设置
[buildozer]
log_level = 2
warn_on_root = 1