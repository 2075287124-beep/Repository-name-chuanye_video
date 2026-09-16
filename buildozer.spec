[app]

# 应用基本信息
title = 川叶视频模块
package.name = chuanye_video
package.domain = com.chuanye
source.dir = .
# 注意：这是「白名单」——不在列表里的后缀根本不会被打进 APK。
# 小鲸核实过：本 App 的界面和图形全部由代码绘制，main.py 没有引用任何
# assets/*.png / *.gif / *.mp3 之类的外部素材，所以现有列表够用。
# ⚠️ 如果以后加了素材（音效/图片/动图），必须把对应后缀加进来，
#    否则会「本机跑得好好的，打包后素材全丢」。
source.include_exts = py,png,jpg,kv,atlas

# 主入口
main.py = main.py

# 版本（2026-08-31 姐姐升级：抖音解析重写API版）
# 2026-09-16 小鲸修复：B站无声音 + 抖音改浏览器兜底 → 同步升到 2.8.4
# ⚠️ version / version.code 必须和 main.py 里的版本号一起改，
#    否则手机会认为是同一个版本，覆盖安装不生效！
version = 2.8.4
version.code = 284

# 依赖
#   bs4 : beautifulsoup4 在 p4a 里的名字确实是 bs4（不要写 beautifulsoup4）
#   urllib3/certifi/charset-normalizer/idna : requests 的依赖，建议显式写出
#       ⚠️ 尤其 certifi：Android 上跑 HTTPS 靠它提供 CA 证书，
#          缺了会 SSL 证书验证失败，表现为「什么链接都下不了」
requirements = python3,kivy,requests,bs4,yt-dlp,urllib3,certifi,charset-normalizer,idna

# Android 权限
# ⚠️ 说明：WRITE_EXTERNAL_STORAGE / READ_EXTERNAL_STORAGE 从 Android 13 (API 33) 起
#    已失效（声明了也不生效）。App 会自动降级到「应用私有目录」保存，
#    此时用户在相册里是找不到文件的，需要用 App 里的「打开文件」按钮。
#    保留这两行是为了兼容 Android 12 及以下的老手机。
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

# FileProvider 配置（Android 7+ 分享/打开文件必需）
# ↑ p4a 的 android.providers 字段有兼容性问题，改用 extra_manifest_entries 直接注入 AndroidManifest
#
# 🔴 2026-09-16 小鲸修复了一个真 bug：
#    原来这里写的是  android:authorities="chuanye_video.fileprovider"
#    但 main.py 运行时是这么拼的：  package_name + ".fileprovider"
#    实际包名 = package.domain + "." + package.name = com.chuanye.chuanye_video
#    → 运行时拼出 com.chuanye.chuanye_video.fileprovider，和清单里的对不上，
#      FileProvider.getUriForFile() 必然抛异常 → 这就是「打开文件功能暂不可用」的病根！
#    改法：authorities 必须写成「完整包名 + .fileprovider」。
#    📌 以后改 package.name / package.domain，这一行也必须跟着改！
#
# 📌 另外：manifest 里引用了 @xml/file_paths，所以 res/xml/file_paths.xml 必须存在，
#    内容见随附的 res/xml/file_paths.xml（我已一并提供）。
android.add_resources = res
android.gradle_dependencies = androidx.core:core:1.12.0
android.extra_manifest_entries = <provider android:name="androidx.core.content.FileProvider" android:authorities="com.chuanye.chuanye_video.fileprovider" android:exported="false" android:grantUriPermissions="true"><meta-data android:name="android.support.FILE_PROVIDER_PATHS" android:resource="@xml/file_paths"/></provider>

# 日志
log_level = 2

# 其他
android.allow_backup = True
android.presplash_color = #1A1A2E

# Buildozer 自身设置
[buildozer]
log_level = 2
warn_on_root = 1