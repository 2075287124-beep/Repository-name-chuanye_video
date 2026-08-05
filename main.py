"""
川叶视频模块 — Android APK 版 (Kivy) v2.8.2
小川叶原作 | Operit 姐姐移植
"""

import os
import re
import json
import glob
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
_DOWNLOAD_DIRS = [
    '/storage/emulated/0/Download',
    '/sdcard/Download',
    '/data/data/com.chuanye.chuanye_video/files',  # 应用内部，一定可写
]
_DOWNLOAD_DIR = None
for _dd in _DOWNLOAD_DIRS:
    if os.path.isdir(_dd) and os.access(_dd, os.W_OK):
        _DOWNLOAD_DIR = _dd
        break
if _DOWNLOAD_DIR is None:
    _DOWNLOAD_DIR = '/sdcard/Download'


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
                text="⚠ 因不可抗力(学校开学) 软件无法定期维护 见谅~",
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

    # ---- 关于弹窗 ----
    def _show_about(self):
        msg = ("川叶视频模块 v2.8.2\n\n"
               "作者：小川叶\n"
               "移植：笨蛋姐姐 (Operit)\n"
               "QQ：2075287124\n\n"
               "支持：B站 抖音 快手 小红书\n"
               "       + 通用网页视频抓取")
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
        """策略1: yt-dlp提取 + 策略2: 通用网页抓取 + 抖音专用解析"""
        # 抖音走专用解析通道（yt-dlp在Android上对抖音完全不兼容）
        platform = detect_platform(url)
        if platform == "douyin":
            try:
                self._try_download_douyin(url)
                return
            except Exception as e:
                Clock.schedule_once(lambda dt, e=e:
                    self.log(f"[X] 抖音专用解析失败：{e}"))
                return

        try:
            self._try_download_ytdlp(url)
            return
        except Exception as e:
            Clock.schedule_once(lambda dt, e=e:
                self.log(f"[!] yt-dlp失败({e})，尝试通用抓取..."))

        try:
            self._try_download_generic(url)
        except Exception as e:
            Clock.schedule_once(lambda dt, e=e:
                self.log(f"[X] 也失败：{e}"))

    # ========== 策略1：yt-dlp 提取 + requests 下载 ==========
    def _try_download_ytdlp(self, url: str):
        Clock.schedule_once(lambda dt: self._reset_progress())
        self._set_progress_thread(0, "解析中...")

        platform = detect_platform(url)
        pname = PLATFORM_MAP.get(platform, {}).get("name", "未知平台")

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "simulate": True,       # 模拟模式，避免某些提取器尝试写文件
            "skip_download": True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            err = str(e)
            if 'write' in err.lower():
                raise Exception(f"{pname}提取器在Android上不兼容({err[:80]})")
            raise

        title = info.get('title', '视频')
        vid = info.get('id', 'video')
        formats = info.get('formats', [])
        if not formats:
            raise Exception("未找到可用格式")

        # 选最佳格式
        best = None
        # 1) mp4有音+有画
        for f in formats:
            if (f.get('ext') == 'mp4' and f.get('url') and
                f.get('acodec', 'none') != 'none' and
                f.get('vcodec', 'none') != 'none'):
                if best is None or (f.get('filesize') or f.get('filesize_approx') or 0) > (best.get('filesize') or best.get('filesize_approx') or 0):
                    best = f
        # 2) mp4有画面（无音也可）
        if best is None:
            for f in formats:
                if (f.get('ext') == 'mp4' and f.get('url') and
                    f.get('vcodec', 'none') != 'none'):
                    if best is None or (f.get('height') or 0) > (best.get('height') or 0):
                        best = f
        # 3) 任意有画面的格式
        if best is None:
            for f in formats:
                if f.get('vcodec', 'none') != 'none' and f.get('url'):
                    if best is None or (f.get('height') or 0) > (best.get('height') or 0):
                        best = f
        # 4) 兜底
        if best is None:
            for f in formats:
                if f.get('url'):
                    best = f
                    break
        if best is None:
            raise Exception("无法获取下载地址")

        dl_url = best['url']
        ext = best.get('ext', 'mp4')
        total = best.get('filesize') or best.get('filesize_approx') or 0
        h = best.get('height', '?')
        outpath = os.path.join(_DOWNLOAD_DIR, f"{vid}.{ext}")

        Clock.schedule_once(lambda dt, t=title, hh=h, s=total:
            self.log(f"[...] {t} ({hh}p, {s/1024/1024:.1f}MB)"))

        # 继承 yt-dlp 提取的 http_headers（含 Referer/Cookie，B站必需！）
        extra_headers = best.get('http_headers', {})
        self._stream_download(dl_url, outpath, total, title, extra_headers)

    # ========== 策略1.5：抖音专用解析（纯requests，不靠yt-dlp） ==========
    def _try_download_douyin(self, url: str):
        """模拟手机浏览器访问抖音页面，从RENDER_DATA JSON中提取无水印视频地址"""
        Clock.schedule_once(lambda dt: self._reset_progress())
        self._set_progress_thread(0, "解析抖音...")

        MOBILE_UA = (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
        )

        session = requests.Session()
        session.headers.update({"User-Agent": MOBILE_UA})

        # 1. 访问短链接 → 跟踪重定向获取真实URL + cookie
        self._set_progress_thread(5, "获取视频页面...")
        resp = session.get(url, allow_redirects=True, timeout=20)
        final_url = resp.url
        Clock.schedule_once(lambda dt, u=final_url[:80]:
            self.log(f"[...] 重定向: {u}..."))

        # 2. 从URL提取video_id
        m = re.search(r'/video/(\d+)', final_url)
        if not m:
            m = re.search(r'modal_id=(\d+)', final_url)
        if not m:
            # 可能短链接已经失效或格式变化，尝试从响应body找
            m = re.search(r'video/(\d+)', resp.text)
        if not m:
            raise Exception("无法解析抖音视频ID，请检查链接是否有效")
        video_id = m.group(1)
        Clock.schedule_once(lambda dt, vid=video_id:
            self.log(f"[...] 视频ID: {vid}"))

        # 3. 获取包含RENDER_DATA的页面（如果需要重新请求）
        self._set_progress_thread(10, "提取页面数据...")
        if '/video/' not in final_url:
            final_url = f"https://www.douyin.com/video/{video_id}"
        page_resp = session.get(final_url, timeout=20, headers={
            "Referer": "https://www.douyin.com/",
            "User-Agent": MOBILE_UA,
        })

        # 4. 从HTML中提取RENDER_DATA
        m = re.search(
            r'<script[^>]*id="RENDER_DATA"[^>]*>([^<]+)</script>',
            page_resp.text
        )
        if not m:
            # 备用：尝试匹配 window._ROUTER_DATA
            m2 = re.search(
                r'window\._ROUTER_DATA\s*=\s*({.+?});?\s*</script>',
                page_resp.text, re.DOTALL
            )
            if m2:
                try:
                    data = json.loads(m2.group(1))
                except Exception:
                    raise Exception("解析_ROUTER_DATA JSON失败")
            else:
                raise Exception("未找到RENDER_DATA（页面结构可能已变化）")
        else:
            # RENDER_DATA是URL-encoded JSON
            render_encoded = m.group(1)
            try:
                data = json.loads(unquote(render_encoded))
            except Exception as e:
                raise Exception(f"解析RENDER_DATA失败: {e}")

        # 5. 递归搜索视频URL
        self._set_progress_thread(15, "搜索视频地址...")
        video_info = self._search_douyin_video(data)
        if not video_info:
            raise Exception("未在页面数据中找到视频地址")

        title = video_info.get('title', f"抖音_{video_id}")
        # 优先无水印地址
        dl_url = (
            video_info.get('download_addr')
            or video_info.get('play_addr')
            or video_info.get('bit_rate')
        )
        if not dl_url:
            raise Exception("未找到可下载的视频URL")

        # 如果是列表（url_list），取第一个
        if isinstance(dl_url, list):
            dl_url = dl_url[0]
        # 去水印：替换watermark=1为watermark=0，或移除wm后缀
        dl_url = dl_url.replace('watermark=1', 'watermark=0')
        dl_url = dl_url.replace('playwm', 'play')

        outpath = os.path.join(_DOWNLOAD_DIR, f"dy_{video_id}.mp4")

        Clock.schedule_once(lambda dt, t=title, v=video_id:
            self.log(f"[...] 抖音: {t}"))
        self._stream_download(dl_url, outpath, 0, title, {
            "Referer": "https://www.douyin.com/",
            "User-Agent": MOBILE_UA,
        })

    def _search_douyin_video(self, obj, depth=0):
        """递归搜索抖音RENDER_DATA中的视频信息"""
        if depth > 15 or obj is None:
            return None

        if isinstance(obj, dict):
            # 查找包含play_addr的字典
            if 'video' in obj and isinstance(obj['video'], dict):
                v = obj['video']
                result = {}
                # play_addr
                pa = v.get('play_addr', {})
                if isinstance(pa, dict) and 'url_list' in pa:
                    result['play_addr'] = pa['url_list'][0] if pa['url_list'] else None
                # download_addr（可能无水印）
                da = v.get('download_addr', {})
                if isinstance(da, dict) and 'url_list' in da:
                    result['download_addr'] = da['url_list'][0] if da['url_list'] else None
                # bit_rate（高清）
                br = v.get('bit_rate', [])
                if br and isinstance(br, list) and len(br) > 0:
                    b0 = br[0]
                    if isinstance(b0, dict) and 'play_addr' in b0:
                        bpa = b0['play_addr']
                        if isinstance(bpa, dict) and 'url_list' in bpa:
                            result['bit_rate'] = bpa['url_list'][0] if bpa['url_list'] else None
                # title
                if 'desc' in obj:
                    result['title'] = obj['desc']
                elif 'title' in obj:
                    result['title'] = obj['title']

                if result.get('play_addr') or result.get('download_addr'):
                    return result

            # 递归搜索所有值
            for key, value in obj.items():
                found = self._search_douyin_video(value, depth + 1)
                if found:
                    # 补充title
                    if 'desc' in obj and not found.get('title'):
                        found['title'] = obj['desc']
                    return found

        elif isinstance(obj, list):
            for item in obj:
                found = self._search_douyin_video(item, depth + 1)
                if found:
                    return found

        return None

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
            self._show_popup("下载完成", f"{t}\n已保存到：\n{p}"))

    def _set_progress_thread(self, value: int, text: str):
        Clock.schedule_once(lambda dt, v=value, t=text:
            self._set_progress(v, t))

    def _show_popup(self, title: str, msg: str, show_open: bool = True):
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