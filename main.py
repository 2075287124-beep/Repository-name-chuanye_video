"""
川叶视频模块 — Android APK 版 (Kivy) v2.8.5-bundled-ffmpeg
 小川叶原作 | Operit 姐姐移植 | 小鲸修 bug

 v2.8.5 修复（重要）：
   B站"卡在开始下载、没反应" —— 根因是【手机上没有 ffmpeg】。
   实测确认 B站视频没有任何「音视频已合流」的格式（全是 DASH 分离轨），
   所以没有 ffmpeg 就【无法】下到有声的B站视频，这不是能绕过去的。
   本次改动：
     1. 支持从 APK 内置释放 ffmpeg（assets/ffmpeg → 应用私有目录）
     2. ffmpeg 查找更全面：环境变量 → APK内置 → PATH → 常见目录
     3. 缺失时写明确日志（原来只弹窗，日志区一片空白，看起来像卡死）
     4. 修复"释放的文件名与查找的文件名不一致"的健壮性问题

 v2.8.4 修复：
   1. B站视频没声音 —— 改用 yt-dlp 内置下载器选轨 + ffmpeg 合并
   2. 抖音下载不了 —— 抖音已上 Argus 风控，改为浏览器打开原链接下载
"""

import os
import re
import json
import glob
import shutil
import platform
import subprocess
import threading
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, unquote
import yt_dlp

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.text import LabelBase

# ========== 中文字体加载（带保护） ==========
FONT_NAME = 'Roboto'
try:
    _CJK_PATHS = [
        '/system/fonts/NotoSansCJK-Regular.ttc',
        '/system/fonts/DroidSansFallback.ttf',
        '/system/fonts/NotoSansSC-Regular.otf',
        '/system/fonts/NotoSerifCJK-Regular.ttc',
    ]
    for _fp in _CJK_PATHS:
        if os.path.exists(_fp):
            try:
                LabelBase.register(name='CJKFont', fn_regular=_fp)
                FONT_NAME = 'CJKFont'
                break
            except Exception:
                continue
except Exception:
    pass

# ========== 存储权限（Android） ==========
try:
    from android.permissions import request_permissions, Permission
    request_permissions([Permission.WRITE_EXTERNAL_STORAGE, Permission.READ_EXTERNAL_STORAGE])
except Exception:
    pass

# ========== 下载目录 ==========
# 注意：原版只列了 Android 路径，在桌面端会全部落空。这里按平台给候选。
if platform.system() == "Windows":
    _DOWNLOAD_DIRS = [
        os.path.join(os.path.expanduser("~"), "Downloads"),
        os.getcwd(),
    ]
elif platform.system() == "Darwin":
    _DOWNLOAD_DIRS = [os.path.join(os.path.expanduser("~"), "Downloads")]
else:
    _DOWNLOAD_DIRS = [
        '/storage/emulated/0/Download',
        '/sdcard/Download',
        '/data/data/com.chuanye.chuanye_video/files',  # 应用内部，一定可写
        os.path.join(os.path.expanduser("~"), "Downloads"),
    ]

_DOWNLOAD_DIR = None
for _dd in _DOWNLOAD_DIRS:
    try:
        if os.path.isdir(_dd) and os.access(_dd, os.W_OK):
            _DOWNLOAD_DIR = _dd
            break
    except Exception:
        continue
if _DOWNLOAD_DIR is None:
    _DOWNLOAD_DIR = os.getcwd()


# ========== 应用私有目录（用于放置内置 ffmpeg） ==========
# Android 上 /data/data/<包名>/ 是 App 自己的地盘，一定能读写、也能给可执行权限。
_APP_DIR = None
if os.environ.get("ANDROID_ARGUMENT") or os.environ.get("ANDROID_PRIVATE"):
    _APP_DIR = os.path.dirname(os.environ.get("ANDROID_PRIVATE", "") or
                              os.environ.get("ANDROID_ARGUMENT", ""))

# 记录 ffmpeg 查找过程中试过的路径，找不到时用来给用户明确诊断
_FFMPEG_SEARCHED = []


def _extract_ffmpeg_tarball(tar_path, dst_dir):
    """把内置的 ffmpeg tar.gz 解压到 dst_dir（Android 上没有 tar 命令，用纯 Python）。"""
    import tarfile
    with tarfile.open(tar_path, "r:gz") as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            # 只取文件名，防目录穿越
            name = os.path.basename(m.name)
            if not name or name.startswith("."):
                continue
            dst = os.path.join(dst_dir, name)
            src = tf.extractfile(m)
            if src is None:
                continue
            with src, open(dst, "wb") as fo:
                shutil.copyfileobj(src, fo, 1024 * 1024)


def _prepare_bundled_ffmpeg():
    """把 APK 里内置的 ffmpeg 释放到应用私有目录，返回其所在目录。

    重点解决：B站是 DASH 流，音视频分为两条轨道，【必须有 ffmpeg 合并】，
    否则下载的视频没有声音。而手机系统通常不带 ffmpeg。

    内置形式（二选一）：
      A) assets/ffmpeg-bundle.tar.gz  —— Termux 版 ffmpeg + 全部依赖库（推荐）
      B) assets/ffmpeg               —— 单个静态编译的 ffmpeg 二进制

    会同时设置 LD_LIBRARY_PATH，让动态链接版的 ffmpeg 能找到同目录的 .so。
    """
    if not _APP_DIR:
        return None                       # 非 Android 环境，无需释放
    exe_name = "ffmpeg"
    dst_dir = os.path.join(_APP_DIR, "bin")
    dst = os.path.join(dst_dir, exe_name)
    try:
        here = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else ""
        # p4a 不同版本把 assets 放的位置不一样，多试几个
        roots = [_APP_DIR, os.path.join(_APP_DIR, "app"), os.getcwd()]
        if here:
            roots += [here, os.path.join(here, "..")]
        def find_in_assets(filename):
            for r in roots:
                for sub in ("assets", ""):
                    p = os.path.join(r, sub, filename) if sub else os.path.join(r, filename)
                    try:
                        if os.path.isfile(p) and os.path.getsize(p) > 4096:
                            return p
                    except Exception:
                        continue
            return None

        os.makedirs(dst_dir, exist_ok=True)

        # 已经释放过就不重复做（避免每次都拷贝 23MB）
        already = os.path.isfile(dst) and os.path.getsize(dst) > 1024
        if not already:
            # --- A) 优先用打包好的 tar.gz（含依赖库） ---
            tarball = find_in_assets("ffmpeg-bundle.tar.gz")
            if tarball:
                _extract_ffmpeg_tarball(tarball, dst_dir)
            else:
                # --- B) 退回到单个可执行文件 ---
                single = find_in_assets(exe_name)
                if not single:
                    return None
                with open(single, "rb") as fi, open(dst, "wb") as fo:
                    shutil.copyfileobj(fi, fo, 1024 * 1024)

        # 确保可执行
        try:
            os.chmod(dst, 0o755)
        except Exception:
            pass
        if not os.path.isfile(dst) or os.path.getsize(dst) < 1024:
            return None

        # 动态链接版需要能找到同目录的 .so：
        #   1) 设 LD_LIBRARY_PATH（子进程会继承，yt-dlp 调用 ffmpeg 时生效）
        #   2) 再补一份带 SONAME 的「无版本后缀」软链（部分加载器按短名找）
        try:
            cur = os.environ.get("LD_LIBRARY_PATH", "")
            if dst_dir not in cur.split(os.pathsep):
                os.environ["LD_LIBRARY_PATH"] = (
                    dst_dir + (os.pathsep + cur if cur else ""))
        except Exception:
            pass
        # 注意：Android/Windows 都可能禁止建符号链接，
        # 所以依次尝试 软链 -> 硬链 -> 复制，保证一定能成。
        #
        # ⚠️ 必须建【两个】别名：
        #     libavcodec.so.62.28.102 是实体文件，
        #     但 ffmpeg 的 ELF 里记的需求名是 SONAME = libavcodec.so.62
        #     （少了这个就加载失败！）。libavcodec.so 是给 -l 链接用的。
        try:
            for fn in os.listdir(dst_dir):
                if ".so." not in fn or ".so." not in fn:
                    continue
                head, _, tail = fn.partition(".so.")
                if not tail:
                    continue
                major = tail.split(".")[0]
                for short in (f"{head}.so", f"{head}.so.{major}"):
                    sp = os.path.join(dst_dir, short)
                    if os.path.exists(sp):
                        continue
                    src = os.path.join(dst_dir, fn)
                    try:
                        os.symlink(fn, sp)                        # 首选：软链（最省空间）
                    except Exception:
                        try:
                            os.link(src, sp)                      # 次选：硬链
                        except Exception:
                            try:
                                shutil.copy2(src, sp)             # 兜底：复制
                            except Exception:
                                pass
        except Exception:
            pass

        return dst_dir
    except Exception:
        return None


# ========== ffmpeg 检测（B站音视频合并必需） ==========
# B站是 DASH 流：画面(m4s/mp4) 与 声音(m4a) 分成两条轨道，
# 必须用 ffmpeg 合并，否则下出来的文件「没有声音」。
# 实测结论（2026-09）：B站视频【不存在】音视频已合流的格式，
# 所以没有 ffmpeg 就真的下不到有声视频 —— 不是代码能绕过去的。
def _find_ffmpeg_dir():
    """返回含 ffmpeg 可执行文件的目录；找不到返回 None。

    查找顺序（重要）：
      1. 环境变量 FFMPEG_DIR / FFMPEG_BINARY
      2. 从 APK 内置释放的 ffmpeg（_prepare_bundled_ffmpeg）—— 最可靠
      3. 系统 PATH
      4. 已知的 Android / Linux 目录（Termux、系统目录等）

    为什么这么麻烦：实测确认 B站视频【没有音视频已合流的格式】，
    必须用 ffmpeg 把视频轨和音频轨合并，否则下出来的是「无声视频」。
    """
    exe_name = "ffmpeg.exe" if platform.system() == "Windows" else "ffmpeg"
    tried = []

    def looks_executable(p):
        """检查 p 是否像一个可用的 ffmpeg 可执行文件（跨平台）。"""
        try:
            if not p or not os.path.isfile(p):
                return False
            if os.path.getsize(p) < 4096:
                return False
            if os.name != "nt":
                if not os.access(p, os.X_OK):
                    return False
        except Exception:
            return False
        return True

    def has_ffmpeg(d):
        """目录 d 里有没有 ffmpeg（两种文件名都认，避免平台判断出错）。"""
        if not d:
            return None
        for name in (exe_name, "ffmpeg", "ffmpeg.exe"):
            p = os.path.join(d, name)
            if looks_executable(p):
                return d
        return None

    def remember(d):
        try:
            if d and d not in tried:
                tried.append(d)
        except Exception:
            pass

    _FFMPEG_SEARCHED[:] = []
    for _p in (os.environ.get("FFMPEG_DIR"), os.environ.get("FFMPEG_BINARY")):
        remember(_p)
    _FFMPEG_SEARCHED[:] = list(tried)

    # 1) 环境变量显式指定
    for env_name in ("FFMPEG_DIR", "FFMPEG_BINARY"):
        val = os.environ.get(env_name)
        if not val:
            continue
        if os.path.isdir(val):
            if has_ffmpeg(val):
                return val
        elif looks_executable(val):
            return os.path.dirname(val)

    # 2) 从 APK 内置释放出来的 ffmpeg（离线可用，最推荐）
    bundled = _prepare_bundled_ffmpeg()
    if bundled:
        remember(bundled)
        if has_ffmpeg(bundled):
            return bundled

    # 3) 系统 PATH
    w = shutil.which("ffmpeg")
    remember(os.path.dirname(w) if w else None)
    if w and looks_executable(w):
        return os.path.dirname(w)

    # 4) 已知 Android / Linux 目录（含应用私有目录）
    candidates = []
    if _APP_DIR:
        candidates.append(os.path.join(_APP_DIR, "bin"))
    candidates += [
        "/data/data/org.termux/files/usr/bin",   # Termux（需用户自行安装）
        "/data/data/com.termux/files/usr/bin",
        "/data/local/tmp",
        "/system/bin",
        "/system/xbin",
        "/usr/bin",
        "/usr/local/bin",
        os.path.join(os.getcwd(), "bin"),
    ]
    for d in candidates:
        remember(d)
        try:
            if has_ffmpeg(d):
                return d
        except Exception:
            continue

    _FFMPEG_SEARCHED[:] = list(tried)
    return None


_FFMPEG_DIR = _find_ffmpeg_dir()


# ========== 工具函数 ==========

def clean_url(raw: str) -> str:
    """从粘贴文本中自动提取 https:// 开头的URL"""
    raw = raw.strip()
    # 匹配 https?:// 开头的URL
    m = re.search(r'https?://\S+', raw)
    if m:
        return m.group(0).rstrip(' .,;:!?)]}，。、；：！？）】')
    return raw


def _safe_name(text: str, maxlen: int = 80) -> str:
    """安全文件名：去掉非法字符，限制长度"""
    if not text:
        return "video"
    # 去掉路径非法字符
    safe = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', text)
    safe = safe.strip('. _')
    if len(safe) > maxlen:
        safe = safe[:maxlen]
    return safe or "video"


def detect_platform(url: str) -> str | None:
    for key, cfg in PLATFORM_MAP.items():
        for domain in cfg["domains"]:
            if domain in url:
                return key
    return None


# ========== 平台配置 ==========
PLATFORM_MAP = {
    "bilibili":    {"name": "B站",       "domains": ["bilibili.com", "b23.tv"]},
    "youtube":     {"name": "YouTube",   "domains": ["youtube.com", "youtu.be"]},
    "douyin":      {"name": "抖音",       "domains": ["douyin.com", "v.douyin.com"]},
    "kuaishou":    {"name": "快手",       "domains": ["kuaishou.com"]},
    "xiaohongshu": {"name": "小红书",     "domains": ["xiaohongshu.com"]},
}

YTDLP_PLATFORMS = {"bilibili", "youtube", "douyin", "kuaishou", "xiaohongshu"}


def get_ytdlp_info(url: str) -> str:
    ydl_opts = {"quiet": True, "no_warnings": True, "noprogress": True}
    lines = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        platform_name = PLATFORM_MAP.get(detect_platform(url), {}).get("name", "视频")
        lines.append(f"====== {platform_name}情报 ======")
        lines.append(f"标题：{info.get('title', '未知')}")
        dur = info.get('duration')
        lines.append(f"时长：{dur} 秒" if dur else "时长：未知")
        lines.append(f"上传者：{info.get('uploader', '未知')}")
        lines.append(f"格式数：{len(info.get('formats', []))}")
    return "\n".join(lines)


def get_generic_info(url: str) -> str:
    lines = []
    response = requests.get(url, timeout=15, headers={
        "User-Agent": "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36"
    })
    response.encoding = response.apparent_encoding
    lines.append(f"状态码：{response.status_code} | 大小：{len(response.content)}字符")
    soup = BeautifulSoup(response.text, "html.parser")
    if soup.title:
        lines.append(f"标题：{soup.title.string}")
    desc = soup.find("meta", attrs={"name": "description"})
    if desc:
        lines.append(f"描述：{desc.get('content', '')[:150]}...")
    images = soup.find_all("img")
    lines.append(f"图片：{len(images)} 张")
    videos = soup.find_all("video")
    lines.append(f"video标签：{len(videos)} 个")
    for v in videos:
        src = v.get("src") or (v.find("source") or {}).get("src")
        if src:
            lines.append(f"视频地址：{urljoin(url, src)}")
            break
    t = response.text
    lines.append(f".mp4={t.count('.mp4')} | .m3u8={t.count('.m3u8')}")
    lines.append("====== 分析完成 ======")
    return "\n".join(lines)


class _MissingFFmpeg(Exception):
    """B站等 DASH 站点需要 ffmpeg 合并音视频，但本机没有。"""
    pass


# ========== Kivy UI ==========
class VideoAppUI(BoxLayout):
    def __init__(self, **kwargs):
        try:
            super().__init__(orientation="vertical", padding=15, spacing=10, **kwargs)

            # 标题
            self.add_widget(Label(
                text="[b]川叶视频模块[/b]",
                markup=True,
                font_size="22sp",
                font_name=FONT_NAME,
                size_hint=(1, 0.1),
                color=(0.2, 0.7, 1, 1),
            ))

            # 公告横幅
            self.announce_label = Label(
                text="⚠「打开文件」功能暂不可用 请移步相册/文件管理器查找 | ZNO",
                font_name=FONT_NAME,
                font_size="9sp",
                size_hint=(1, 0.03),
                halign="center",
                color=(1, 0.7, 0.3, 1),
            )
            self.add_widget(self.announce_label)

            # URL 输入
            self.url_input = TextInput(
                hint_text="粘贴视频链接...",
                font_name=FONT_NAME,
                size_hint=(1, 0.08),
                multiline=False,
                font_size="14sp",
            )
            self.add_widget(self.url_input)

            # 按钮行
            btn_box = BoxLayout(size_hint=(1, 0.1), spacing=10)

            info_btn = Button(
                text="获取信息",
                font_name=FONT_NAME,
                font_size="14sp",
                background_color=(0.2, 0.6, 1, 1),
            )
            info_btn.bind(on_press=self._safe_on_get_info)
            btn_box.add_widget(info_btn)

            download_btn = Button(
                text="下载视频",
                font_name=FONT_NAME,
                font_size="14sp",
                background_color=(0.2, 0.8, 0.4, 1),
            )
            download_btn.bind(on_press=self._safe_on_download)
            btn_box.add_widget(download_btn)

            paste_btn = Button(
                text="粘贴",
                font_name=FONT_NAME,
                font_size="13sp",
                background_color=(0.5, 0.5, 0.5, 1),
            )
            paste_btn.bind(on_press=self._safe_on_paste)
            btn_box.add_widget(paste_btn)

            about_btn = Button(
                text="关于",
                font_name=FONT_NAME,
                font_size="13sp",
                background_color=(0.6, 0.4, 0.8, 1),
            )
            about_btn.bind(on_press=self._safe_on_about)
            btn_box.add_widget(about_btn)

            self.add_widget(btn_box)

            # 进度条区域
            self.progress_bar = ProgressBar(max=100, value=0, size_hint=(1, 0.04))
            self.add_widget(self.progress_bar)
            self.progress_label = Label(
                text="",
                font_name=FONT_NAME,
                font_size="11sp",
                size_hint=(1, 0.04),
                halign="center",
                color=(0.6, 0.9, 0.6, 1),
            )
            self.add_widget(self.progress_label)

            # 输出区域
            self.output_label = Label(
                text="等待输入...\n",
                font_name=FONT_NAME,
                size_hint=(1, None),
                font_size="13sp",
                halign="left",
                valign="top",
                text_size=(None, None),
                color=(0.9, 0.9, 0.9, 1),
            )
            self.output_label.bind(texture_size=lambda instance, size: setattr(instance, 'size', size))

            scroll = ScrollView(size_hint=(1, 0.61))
            scroll.add_widget(self.output_label)
            self.add_widget(scroll)

        except Exception as e:
            # 最外层保护：万一初始化崩溃，至少留个兜底界面
            print(f"[川叶] UI初始化异常: {e}")

    def log(self, msg: str):
        try:
            current = self.output_label.text or ""
            self.output_label.text = current + msg + "\n"
            self.output_label.text_size = (self.output_label.width, None)
        except Exception:
            pass  # 日志失败不影响使用

    # ---- 安全包装器（每个按钮回调都带 try-except） ----
    def _safe_on_paste(self, instance):
        try:
            self.on_paste(instance)
        except Exception as e:
            self.log(f"[X] 粘贴异常: {e}")

    def _safe_on_get_info(self, instance):
        try:
            self.on_get_info(instance)
        except Exception as e:
            self.log(f"[X] 获取信息异常: {e}")

    def _safe_on_download(self, instance):
        try:
            self.on_download(instance)
        except Exception as e:
            self.log(f"[X] 下载异常: {e}")

    def _safe_on_about(self, instance):
        try:
            self._show_about()
        except Exception as e:
            self.log(f"[X] 关于异常: {e}")

    # ---- 复制/打开文件夹 ----
    def _copy_path(self, *args):
        """尝试用 content:// URI 打开文件夹，失败则复制路径"""
        path = _DOWNLOAD_DIR
        opened = False

        # 策略：pyjnius + content:// URI（绕过 file:// 的 Android 7+ 限制）
        try:
            from jnius import autoclass
            Intent = autoclass('android.content.Intent')
            Uri = autoclass('android.net.Uri')
            PythonActivity = autoclass('org.kivy.android.PythonActivity')

            # 用 ExternalStorage Provider 的 content URI
            content_uri = Uri.parse(
                'content://com.android.externalstorage.documents/tree/primary%3ADownload'
            )

            intent = Intent(Intent.ACTION_VIEW)
            intent.setDataAndType(content_uri, 'resource/folder')
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            PythonActivity.mActivity.startActivity(intent)
            opened = True
        except Exception:
            pass

        # 无论是否打开成功，都复制路径到剪贴板
        try:
            Clipboard.copy(path)
        except Exception:
            pass

        # 如果打开失败，弹窗提示
        if not opened:
            self._show_popup(
                "路径已复制 ✅",
                f"文件保存在：\n{path}\n\n路径已复制，请手动打开文件管理器",
                show_open=False,
            )

    # ---- 打开已下载的文件（四重保险） ----
    def _open_file(self, filepath, *args):
        """调起系统'打开方式'菜单——四层fallback"""

        # ===== 保险1：FileProvider content:// URI（Android 7+ 标准方式） =====
        try:
            from jnius import autoclass
            from android import mActivity

            Intent = autoclass('android.content.Intent')
            File = autoclass('java.io.File')
            FileProvider = autoclass('androidx.core.content.FileProvider')

            context = mActivity.getApplicationContext()
            package_name = context.getPackageName()
            file_obj = File(filepath)
            # ✅ authority直接传Python字符串，pyjnius自动转java.lang.String
            authority = package_name + ".fileprovider"
            file_uri = FileProvider.getUriForFile(context, authority, file_obj)

            intent = Intent(Intent.ACTION_VIEW)
            intent.setDataAndType(file_uri, 'video/*')
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)

            chooser = Intent.createChooser(intent, '选择应用')
            chooser.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            mActivity.startActivity(chooser)
            return  # ✅ 成功！
        except Exception as e1:
            self.log(f"[!] PlanA(FileProvider)失败: {str(e1)[:60]}")

        # ===== 保险2：ACTION_VIEW + Uri.fromFile（不用EXTRA_STREAM绕过类型检查） =====
        try:
            from jnius import autoclass
            from android import mActivity

            Intent = autoclass('android.content.Intent')
            File = autoclass('java.io.File')
            Uri = autoclass('android.net.Uri')

            file_obj = File(filepath)
            file_uri = Uri.fromFile(file_obj)

            intent = Intent(Intent.ACTION_VIEW)
            # ✅ 用setDataAndType而不是putExtra(EXTRA_STREAM)，避免Uri类型检查
            intent.setDataAndType(file_uri, 'video/*')
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)

            chooser = Intent.createChooser(intent, '选择应用')
            mActivity.startActivity(chooser)
            return  # ✅ 成功！
        except Exception as e2:
            self.log(f"[!] PlanB(ACTION_VIEW)失败: {str(e2)[:60]}")

        # ===== 保险3：shell am start 命令（系统级调用，权限更宽） =====
        try:
            # 用 am start 直接调起，某些ROM的shell环境不受file://限制
            cmd = (
                f'am start -a android.intent.action.VIEW '
                f'-d "file://{filepath}" '
                f'-t video/* '
                f'--activity-new-task 2>/dev/null'
            )
            os.system(cmd)
            # shell命令不抛异常就算尝试了
            Clipboard.copy(filepath)
            return
        except Exception as e3:
            self.log(f"[!] PlanC(shell)失败: {str(e3)[:60]}")

        # ===== 保险4：复制路径兜底（100%可靠） =====
        try:
            Clipboard.copy(filepath)
        except Exception:
            pass
        self._show_popup(
            "路径已复制 ✅",
            f"无法自动打开，路径已复制到剪贴板：\n{filepath}",
            show_open=False,
        )

    # ---- 关于弹窗 ----
    def _show_about(self):
        msg = ("川叶视频模块 v2.8.5-bundled-ffmpeg\n\n"
               "作者：小川叶\n"
               "移植：笨蛋姐姐 (Operit)\n"
               "修 bug：小鲸\n"
               "QQ：2075287124\n\n"
               "支持：B站 抖音 快手 小红书\n"
               "       + 通用网页视频抓取\n\n"
               "v2.8.4~2.8.5 修复：\n"
               "  ✔ B站下载没声音（DASH音视频分离，\n"
               "    现用 yt-dlp 选轨 + ffmpeg 合并）\n"
               "  ✔ 支持 APK 内置 ffmpeg（打包时放入\n"
               "    assets/ffmpeg，首次运行自动释放）\n"
               "  ⚠ 抖音已上 Argus 风控，自动解析失效，\n"
               "    改为一键用浏览器打开下载\n"
               "  ℹ B站必须有 ffmpeg 才能合成有声视频，\n"
               "    抖音/快手/小红书不受影响")
        self._show_popup("关于", msg, show_open=False)

    # ---- 进度条更新 ----
    def _on_progress(self, d: dict):
        """yt-dlp 进度回调（在下载线程调用，通过 Clock 转到主线程）"""
        try:
            if d.get('status') == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                downloaded = d.get('downloaded_bytes', 0)
                if total > 0:
                    pct = int(downloaded / total * 100)
                else:
                    pct = 0
                speed = d.get('_speed_str', '').strip() or ''
                eta = d.get('_eta_str', '').strip() or ''
                info = f"{pct}%"
                if speed:
                    info += f"  {speed}"
                if eta:
                    info += f"  ETA {eta}"
                # 安全调度到主线程
                Clock.schedule_once(lambda dt, v=pct, t=info:
                    self._set_progress(v, t))
            elif d.get('status') == 'finished':
                Clock.schedule_once(lambda dt:
                    self._set_progress(100, "处理中..."))
        except Exception:
            pass

    def _set_progress(self, value: int, text: str):
        try:
            self.progress_bar.value = value
            self.progress_label.text = text
        except Exception:
            pass

    def _reset_progress(self):
        try:
            self.progress_bar.value = 0
            self.progress_label.text = ""
        except Exception:
            pass

    # ---- 实际逻辑 ----
    def on_paste(self, instance):
        try:
            clipboard_text = Clipboard.paste()
            if clipboard_text:
                # 自动清洗：提取https://开头的URL
                url = clean_url(clipboard_text)
                self.url_input.text = url
                if url != clipboard_text.strip():
                    self.log("[OK] 已粘贴并自动提取URL")
                else:
                    self.log("[OK] 已粘贴剪贴板内容")
        except Exception:
            self.log("[!] 粘贴失败，请手动输入")

    def on_get_info(self, instance):
        raw = self.url_input.text.strip()
        if not raw:
            self.log("[!] 请输入视频地址！")
            return
        # 自动清洗URL
        url = clean_url(raw)
        if url != raw:
            self.url_input.text = url
            self.log("[...] 已自动提取URL")
        if not url.startswith("http"):
            url = "https://" + url
            self.url_input.text = url
        self.log(f"[...] 分析中：{url}")
        threading.Thread(target=self._fetch_info, args=(url,), daemon=True).start()

    def _fetch_info(self, url: str):
        try:
            platform = detect_platform(url)
            if platform in YTDLP_PLATFORMS:
                try:
                    result = get_ytdlp_info(url)
                    Clock.schedule_once(lambda dt: self.log(result))
                except Exception as e:
                    Clock.schedule_once(lambda dt: self.log(f"[!] yt-dlp失败：{e}"))
                    self._fetch_generic(url)
            else:
                self._fetch_generic(url)
        except Exception as e:
            Clock.schedule_once(lambda dt: self.log(f"[X] 信息获取异常: {e}"))

    def _fetch_generic(self, url: str):
        try:
            result = get_generic_info(url)
            Clock.schedule_once(lambda dt: self.log(result))
        except Exception as e:
            Clock.schedule_once(lambda dt: self.log(f"[X] 解析失败：{e}"))

    def on_download(self, instance):
        raw = self.url_input.text.strip()
        if not raw:
            self.log("[!] 请输入视频地址！")
            return
        url = clean_url(raw)
        if url != raw:
            self.url_input.text = url
        if not url.startswith("http"):
            url = "https://" + url
            self.url_input.text = url
        self.log(f"[...] 开始下载：{url}")
        threading.Thread(target=self._do_download, args=(url,), daemon=True).start()

    def _do_download(self, url: str):
        """下载主流程。

        v2.8.4 变更：改为「让 yt-dlp 自己选轨并下载」。
        原因：B站是 DASH 流（画面/声音分离），手工挑单一 URL 必然丢音轨；
        yt-dlp 的 bestvideo+bestaudio 会分别取两条轨道再用 ffmpeg 合并。
        """
        platform = detect_platform(url)

        if platform == "douyin":
            self._download_douyin(url)
            return

        try:
            self._try_download_ytdlp(url)
            return
        except _MissingFFmpeg:
            Clock.schedule_once(lambda dt: self._set_progress(0, ""))
            self._show_missing_ffmpeg()
            return
        except Exception as e:
            Clock.schedule_once(lambda dt, e=e:
                self.log(f"[!] yt-dlp 失败：{str(e)[:120]}"))
            Clock.schedule_once(lambda dt:
                self.log("[...] 尝试通用网页抓取..."))

        try:
            self._try_download_generic(url)
        except Exception as e:
            Clock.schedule_once(lambda dt, e=e:
                self.log(f"[X] 通用抓取也失败：{str(e)[:120]}"))

    # ========== B站/YouTube 等：交给 yt-dlp 自己选轨下载 ==========
    @staticmethod
    def _format_selector(platform):
        """B站等 DASH 站点：必须 bestvideo+bestaudio 再合并，否则没声音。"""
        if platform in ("bilibili", "youtube"):
            return "bestvideo+bestaudio/best"
        return "best/bestvideo+bestaudio"

    @staticmethod
    def _need_ffmpeg(platform):
        """这些平台音视频分离，必须有 ffmpeg 才能合并。"""
        return platform in ("bilibili", "youtube")

    def _ensure_ffmpeg(self, platform):
        """返回 ffmpeg 所在目录；本平台缺 ffmpeg 时抛 _MissingFFmpeg。

        ⚠️ 实测结论：B站视频【没有音视频已合流的格式】（全是 DASH 分离轨），
        所以缺 ffmpeg 时无法产出有声视频 —— 只能明确提示，不能硬下个无声的。
        """
        if _FFMPEG_DIR:
            return _FFMPEG_DIR
        if self._need_ffmpeg(platform):
            # 写入日志，便于前因后果一目了然（以前只弹窗，日志区一片空白）
            Clock.schedule_once(lambda dt: self.log(
                "[X] 本机没有 ffmpeg，B站/YouTube 无法合并音视频"))
            try:
                if _APP_DIR and not _prepare_bundled_ffmpeg():
                    Clock.schedule_once(lambda dt, d=_APP_DIR: self.log(
                        f"[i] APK 内置 ffmpeg 未找到（已查 {d}/assets/ffmpeg）"))
                dirs = _FFMPEG_SEARCHED[:6]
                if dirs:
                    Clock.schedule_once(lambda dt, d=dirs:
                        self.log("[i] 已查找: " + " | ".join(str(x) for x in d)))
            except Exception:
                pass
            raise _MissingFFmpeg()
        return None

    def _try_download_ytdlp(self, url: str):
        """用 yt-dlp 内置下载器下载（自动选轨 + ffmpeg 合并）。

        这是 v2.8.4 的核心修复：原实现用手工挑一个 format 的 url 再 requests 流式下载，
        对 B站 DASH 流只会拿到「纯视频轨」，所以下载出来的视频没有声音。
        """
        Clock.schedule_once(lambda dt: self._reset_progress())
        self._set_progress_thread(0, "解析中...")

        platform = detect_platform(url)
        pname = PLATFORM_MAP.get(platform, {}).get("name", "未知平台")
        ffmpeg_dir = self._ensure_ffmpeg(platform)

        if ffmpeg_dir:
            Clock.schedule_once(lambda dt, d=ffmpeg_dir:
                self.log(f"[i] ffmpeg: {d}"))
        else:
            Clock.schedule_once(lambda dt:
                self.log("[i] 未检测到 ffmpeg（本平台音视频未分离，可直接下载)"))

        # ---- 进度回调：yt-dlp 下载在子线程，必须用 Clock 切回主线程 ----
        def _hook(d):
            try:
                st = d.get('status')
                if st == 'downloading':
                    total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                    done = d.get('downloaded_bytes', 0) or 0
                    pct = int(done / total * 100) if total else 0
                    if pct < 0:
                        pct = 0
                    if pct > 99:
                        pct = 99
                    text = d.get('_percent_str', '').strip() or f"{pct}%"
                    sp = (d.get('_speed_str') or '').strip()
                    eta = (d.get('_eta_str') or '').strip()
                    if sp:
                        text += f"  {sp}"
                    if eta:
                        text += f"  ETA {eta}"
                    Clock.schedule_once(
                        lambda dt, v=pct, t=text: self._set_progress(v, t))
                elif st == 'finished':
                    Clock.schedule_once(
                        lambda dt: self._set_progress(100, "合并音视频中..."))
            except Exception:
                pass

        opts = {
            "format": self._format_selector(platform),
            "outtmpl": os.path.join(_DOWNLOAD_DIR, "%(title).80B-%(id)s.%(ext)s"),
            "merge_output_format": "mp4",
            "progress_hooks": [_hook],
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
        }
        if ffmpeg_dir:
            opts["ffmpeg_location"] = ffmpeg_dir

        def _run():
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])

        try:
            _run()
        except Exception as e:
            msg = str(e).lower()
            # yt-dlp 版本过旧时，常见报错提到 extractor / 需要更新
            if any(k in msg for k in ("unable to extract", "unsupported url",
                                      "no video formats", "failed to extract")):
                Clock.schedule_once(lambda dt:
                    self.log("[i] 若反复失败，可能是 yt-dlp 版本过旧，建议更新"))
            raise

        Clock.schedule_once(lambda dt: self._set_progress(100, ""))
        Clock.schedule_once(lambda dt, p=_DOWNLOAD_DIR:
            self.log(f"[OK] 下载完成 ✅ 已保存到：{p}"))
        Clock.schedule_once(lambda dt, t=pname, p=_DOWNLOAD_DIR:
            self._show_popup("下载完成", f"{t} 下载完成\n已保存到：\n{p}\n\n请移步相册或文件管理器查找"))

    def _show_missing_ffmpeg(self):
        """B站音视频合并需要 ffmpeg，缺少时给出可操作的指引。"""
        try:
            content = BoxLayout(orientation="vertical", padding=10, spacing=8)
            content.add_widget(Label(
                text=("B站的画面和声音是分开的两条轨道（DASH），\n"
                      "必须用 ffmpeg 合并，否则视频没有声音。\n\n"
                      "⚠️ 实测确认：B站没有「已合流」的格式，\n"
                      "   所以缺 ffmpeg 时无法下到有声的B站视频。\n\n"
                      "本机没有检测到 ffmpeg。\n\n"
                      "【推荐】打包时把 ffmpeg 内置进 APK：\n"
                      "  把 Android arm64 版 ffmpeg 二进制放到工程的\n"
                      "  assets/ffmpeg，App 首次运行会自动释放并使用。\n\n"
                      "【次选】安装 Termux 后执行：\n"
                      "  pkg install ffmpeg\n\n"
                      "抖音、快手、小红书不受影响，无需 ffmpeg。"),
                font_name=FONT_NAME,
            ))
            close_btn = Button(text="知道了", font_name=FONT_NAME,
                               size_hint=(1, 0.3))
            popup = Popup(title="B站需要 ffmpeg", content=content,
                          size_hint=(0.92, 0.72))
            close_btn.bind(on_press=popup.dismiss)
            content.add_widget(close_btn)
            popup.open()
        except Exception:
            pass

    # ========== 抖音：yt-dlp 尝试 + 浏览器兜底 ==========
    def _download_douyin(self, url: str):
        """抖音专用流程。

        背景（实测结论）：抖音已启用 Argus 风控，网页接口返回
            403 Blocked by ArgusSecurityPlugin Uifid Not Found
        且分享页已变成纯 JS 空壳，服务端 HTML 里不再包含 play_addr。
        因此「纯 requests 解析」这条路已经走不通，必须依赖：
          (a) yt-dlp + 真实浏览器 cookie（有就能下），或
          (b) 直接用浏览器打开原链接（用户自己在浏览器里下载）
        """
        self._set_progress_thread(0, "抖音：尝试 yt-dlp...")
        try:
            self._try_download_ytdlp(url)
            return
        except _MissingFFmpeg:
            self._show_missing_ffmpeg()
            return
        except Exception as e:
            Clock.schedule_once(lambda dt, e=e:
                self.log(f"[!] 抖音自动下载失败：{str(e)[:110]}"))
            Clock.schedule_once(lambda dt:
                self.log("[i] 抖音有 Argus 风控，自动解析已不可用"))
        self._show_browser_fallback(url)

    def _show_browser_fallback(self, url: str):
        """自动下载失败时：用浏览器打开原链接，让用户在浏览器里下载。"""
        def _do_open(*_args):
            self._open_url_in_browser(url)

        def _do_copy(*_args):
            try:
                Clipboard.copy(url)
                self.log("[OK] 链接已复制，可粘贴到浏览器或下载工具")
            except Exception:
                pass

        try:
            content = BoxLayout(orientation="vertical", padding=10, spacing=8)
            content.add_widget(Label(
                text=("抖音已启用风控，本机无法直接解析下载。\n\n"
                      "建议用浏览器打开原链接，在浏览器里下载。\n"
                      "（也可以把链接复制到第三方解析工具）"),
                font_name=FONT_NAME,
            ))
            btn_box = BoxLayout(size_hint=(1, 0.35), spacing=8)

            open_btn = Button(text="用浏览器打开", font_name=FONT_NAME,
                              background_color=(0.2, 0.6, 1, 1))
            open_btn.bind(on_press=_do_open)
            btn_box.add_widget(open_btn)

            copy_btn = Button(text="复制链接", font_name=FONT_NAME,
                              background_color=(0.5, 0.5, 0.5, 1))
            copy_btn.bind(on_press=_do_copy)
            btn_box.add_widget(copy_btn)

            popup = Popup(title="抖音下载", content=content, size_hint=(0.85, 0.42))
            close_btn = Button(text="关闭", font_name=FONT_NAME)
            close_btn.bind(on_press=popup.dismiss)
            btn_box.add_widget(close_btn)

            content.add_widget(btn_box)
            popup.open()
        except Exception:
            # 弹窗都失败就直接复制链接兜底
            _do_copy()
            self.log(f"[i] 请手动用浏览器打开：{url}")

    def _open_url_in_browser(self, url: str):
        """打开浏览器访问 url，并把链接复制到剪贴板。"""
        try:
            Clipboard.copy(url)
        except Exception:
            pass
        # Android：显式 Intent 打开浏览器
        try:
            from jnius import autoclass
            Intent = autoclass('android.content.Intent')
            Uri = autoclass('android.net.Uri')
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            PythonActivity.mActivity.startActivity(intent)
            return
        except Exception:
            pass
        # 桌面端：webbrowser
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass

    # ========== 策略2：通用网页抓取 mp4/video ==========
    def _try_download_generic(self, url: str):
        Clock.schedule_once(lambda dt: self._reset_progress())
        self._set_progress_thread(0, "抓取网页...")

        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36"
        })
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        dl_url = None
        title = "video"

        # 1) 找 <video> 标签
        for v in soup.find_all("video"):
            src = v.get("src") or (v.find("source") or {}).get("src")
            if src:
                dl_url = urljoin(url, src)
                break

        # 2) 找页面里的 .mp4 链接
        if not dl_url:
            t = resp.text
            m = re.search(r'https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*', t)
            if m:
                dl_url = m.group(0)

        # 3) 找 .m3u8 链接
        if not dl_url:
            m = re.search(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', resp.text)
            if m:
                dl_url = m.group(0)

        if not dl_url:
            raise Exception("未找到视频地址（无video标签、无.mp4、无.m3u8）")

        # 生成文件名
        if soup.title:
            title = soup.title.string.strip() or "video"
        ext = "mp4" if ".mp4" in dl_url else ("m3u8" if ".m3u8" in dl_url else "mp4")
        safe_title = _safe_name(title, 50)
        outpath = os.path.join(_DOWNLOAD_DIR, f"{safe_title}.{ext}")

        Clock.schedule_once(lambda dt, u=dl_url[:80]:
            self.log(f"[...] 找到视频：{u}..."))
        self._stream_download(dl_url, outpath, 0, title)

    # ========== 公共：流式下载（requests.iter_content） ==========
    def _stream_download(self, dl_url: str, outpath: str, total_size: int, title: str, extra_headers: dict = None):
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36",
        }
        if extra_headers:
            headers.update(extra_headers)
        # Referer兜底
        if 'Referer' not in headers:
            headers['Referer'] = dl_url

        resp = requests.get(dl_url, stream=True, timeout=60, headers=headers)
        resp.raise_for_status()

        if total_size == 0:
            total_size = int(resp.headers.get('content-length', 0)) or 0

        downloaded = 0
        last_pct = -1
        with open(outpath, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        pct = min(int(downloaded / total_size * 100), 99)
                        if pct != last_pct:
                            last_pct = pct
                            info_text = f"{pct}%  {downloaded/1024/1024:.1f}/{total_size/1024/1024:.1f}MB"
                            Clock.schedule_once(lambda dt, v=pct, t=info_text:
                                self._set_progress(v, t))
                    elif downloaded % (256 * 1024) == 0:
                        # 没有总大小，只显示已下载
                        info_text = f"{downloaded/1024/1024:.1f}MB"
                        Clock.schedule_once(lambda dt, t=info_text:
                            self._set_progress(50, t))

        Clock.schedule_once(lambda dt: self._set_progress(100, ""))
        size_mb = os.path.getsize(outpath) / (1024 * 1024)
        Clock.schedule_once(lambda dt, p=outpath, s=size_mb:
            self.log(f"[OK] 下载完成 ({s:.1f}MB)：{p}"))
        Clock.schedule_once(lambda dt, t=title, p=outpath:
            self._show_popup("下载完成", f"{t}\n已保存到：\n{p}\n\n请移步相册或文件管理器查找"))

    def _set_progress_thread(self, value: int, text: str):
        Clock.schedule_once(lambda dt, v=value, t=text:
            self._set_progress(v, t))

    def _show_popup(self, title: str, msg: str, show_open: bool = True, filepath: str = None):
        try:
            content = BoxLayout(orientation="vertical", padding=10)
            content.add_widget(Label(text=msg, font_name=FONT_NAME))
            btn_box = BoxLayout(size_hint=(1, 0.35), spacing=8)
            ok_btn = Button(text="好的", font_name=FONT_NAME)
            popup = Popup(title=title, content=content, size_hint=(0.8, 0.45))
            ok_btn.bind(on_press=popup.dismiss)
            btn_box.add_widget(ok_btn)
            if show_open:
                copy_btn = Button(text="复制路径", font_name=FONT_NAME,
                                  background_color=(0.2, 0.6, 1, 1))
                copy_btn.bind(on_press=lambda x: self._copy_path())
                btn_box.add_widget(copy_btn)
            if filepath:
                open_btn = Button(text="打开文件", font_name=FONT_NAME,
                                  background_color=(0.2, 0.8, 0.4, 1))
                open_btn.bind(on_press=lambda x, fp=filepath: self._open_file(fp))
                btn_box.add_widget(open_btn)
            content.add_widget(btn_box)
            popup.open()
        except Exception:
            pass


class VideoApp(App):
    def build(self):
        try:
            self.title = "川叶视频模块"
            return VideoAppUI()
        except Exception as e:
            # 极端情况：连UI都建不了，返回一个错误界面
            box = BoxLayout(orientation="vertical", padding=20)
            box.add_widget(Label(
                text=f"[b]启动失败[/b]\n{e}",
                markup=True,
                font_name=FONT_NAME,
            ))
            return box


if __name__ == "__main__":
    try:
        VideoApp().run()
    except Exception as e:
        print(f"[川叶] 致命错误: {e}")
