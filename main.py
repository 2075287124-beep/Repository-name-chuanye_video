"""
川叶视频模块 — Android APK 版 (Kivy)
小川叶原作 | Operit 姐姐移植
"""

import re
import threading
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import yt_dlp

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.popup import Popup
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard

# ========== 平台配置 ==========
PLATFORM_MAP = {
    "bilibili":    {"name": "B站",       "domains": ["bilibili.com", "b23.tv"]},
    "youtube":     {"name": "YouTube",   "domains": ["youtube.com", "youtu.be"]},
    "douyin":      {"name": "抖音",       "domains": ["douyin.com", "v.douyin.com"]},
    "kuaishou":    {"name": "快手",       "domains": ["kuaishou.com"]},
    "xiaohongshu": {"name": "小红书",     "domains": ["xiaohongshu.com"]},
}

YTDLP_PLATFORMS = {"bilibili", "youtube", "douyin", "kuaishou", "xiaohongshu"}


def detect_platform(url: str) -> str | None:
    for key, cfg in PLATFORM_MAP.items():
        for domain in cfg["domains"]:
            if domain in url:
                return key
    return None


def get_ytdlp_info(url: str) -> str:
    ydl_opts = {"quiet": True}
    lines = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        platform_name = PLATFORM_MAP.get(detect_platform(url), {}).get("name", "视频")
        lines.append(f"====== {platform_name}情报 ======")
        lines.append(f"标题：{info.get('title', '未知')}")
        lines.append(f"时长：{info.get('duration', '?')} 秒")
        lines.append(f"上传者：{info.get('uploader', '未知')}")
        lines.append(f"格式数：{len(info.get('formats', []))}")
    return "\n".join(lines)


def get_generic_info(url: str) -> str:
    lines = []
    response = requests.get(url, timeout=10)
    response.encoding = response.apparent_encoding
    lines.append(f"✅ 状态码：{response.status_code} | 大小：{len(response.content)} 字符")
    soup = BeautifulSoup(response.text, "html.parser")
    if soup.title:
        lines.append(f"📄 标题：{soup.title.string}")
    desc = soup.find("meta", attrs={"name": "description"})
    if desc:
        lines.append(f"📝 描述：{desc.get('content', '')[:150]}...")
    images = soup.find_all("img")
    lines.append(f"🖼 图片：{len(images)} 张")
    videos = soup.find_all("video")
    lines.append(f"🎬 video标签：{len(videos)} 个")
    for v in videos:
        src = v.get("src") or (v.find("source") or {}).get("src")
        if src:
            lines.append(f"📹 视频地址：{urljoin(url, src)}")
            break
    t = response.text
    lines.append(f"🔍 .mp4={t.count('.mp4')} | .m3u8={t.count('.m3u8')}")
    lines.append("====== 分析完成 ======")
    return "\n".join(lines)


# ========== Kivy UI ==========
class VideoAppUI(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", padding=15, spacing=10, **kwargs)

        # 标题
        self.add_widget(Label(
            text="🎬 川叶视频模块",
            font_size="22sp",
            size_hint=(1, 0.1),
            bold=True,
            color=(0.2, 0.7, 1, 1),
        ))

        # URL 输入
        self.url_input = TextInput(
            hint_text="粘贴视频链接...",
            size_hint=(1, 0.08),
            multiline=False,
            font_size="14sp",
        )
        self.add_widget(self.url_input)

        # 按钮行
        btn_box = BoxLayout(size_hint=(1, 0.1), spacing=10)
        info_btn = Button(text="🔍 获取信息", background_color=(0.2, 0.6, 1, 1))
        info_btn.bind(on_press=self.on_get_info)
        btn_box.add_widget(info_btn)

        download_btn = Button(text="⬇ 下载视频", background_color=(0.2, 0.8, 0.4, 1))
        download_btn.bind(on_press=self.on_download)
        btn_box.add_widget(download_btn)

        paste_btn = Button(text="📋 粘贴", background_color=(0.5, 0.5, 0.5, 1))
        paste_btn.bind(on_press=self.on_paste)
        btn_box.add_widget(paste_btn)

        self.add_widget(btn_box)

        # 输出区域
        self.output_label = Label(
            text="等待输入...\n",
            size_hint=(1, None),
            font_size="13sp",
            halign="left",
            valign="top",
            text_size=(None, None),
            color=(0.9, 0.9, 0.9, 1),
        )
        self.output_label.bind(texture_size=lambda instance, size: setattr(instance, 'size', size))

        scroll = ScrollView(size_hint=(1, 0.72))
        scroll.add_widget(self.output_label)
        self.add_widget(scroll)

    def log(self, msg: str):
        current = self.output_label.text or ""
        self.output_label.text = current + msg + "\n"
        # 自动滚动到底部
        self.output_label.text_size = (self.output_label.width, None)

    def on_paste(self, instance):
        try:
            clipboard_text = Clipboard.paste()
            if clipboard_text:
                self.url_input.text = clipboard_text
                self.log("📋 已粘贴剪贴板内容")
        except Exception:
            self.log("⚠️ 粘贴失败，请手动输入")

    def on_get_info(self, instance):
        url = self.url_input.text.strip()
        if not url:
            self.log("⚠️ 请输入视频地址！")
            return
        if not url.startswith("http"):
            url = "https://" + url
        self.log(f"📥 分析中：{url}")
        threading.Thread(target=self._fetch_info, args=(url,), daemon=True).start()

    def _fetch_info(self, url: str):
        platform = detect_platform(url)
        if platform in YTDLP_PLATFORMS:
            try:
                result = get_ytdlp_info(url)
                Clock.schedule_once(lambda dt: self.log(result))
            except Exception as e:
                Clock.schedule_once(lambda dt: self.log(f"⚠️ yt-dlp失败：{e}"))
                self._fetch_generic(url)
        else:
            self._fetch_generic(url)

    def _fetch_generic(self, url: str):
        try:
            result = get_generic_info(url)
            Clock.schedule_once(lambda dt: self.log(result))
        except Exception as e:
            Clock.schedule_once(lambda dt: self.log(f"❌ 解析失败：{e}"))

    def on_download(self, instance):
        url = self.url_input.text.strip()
        if not url:
            self.log("⚠️ 请输入视频地址！")
            return
        self.log("⬇ 开始下载（后台运行）...")
        threading.Thread(target=self._do_download, args=(url,), daemon=True).start()

    def _do_download(self, url: str):
        ydl_opts = {
            "outtmpl": "/storage/emulated/0/Download/%(title)s.%(ext)s",
            "merge_output_format": "mp4",
            "quiet": True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
            Clock.schedule_once(lambda dt: self.log(f"✅ 下载完成：{filename}"))
            Clock.schedule_once(lambda dt: self._show_popup("下载完成", f"已保存到：\n{filename}"))
        except Exception as e:
            Clock.schedule_once(lambda dt: self.log(f"❌ 下载失败：{e}"))

    def _show_popup(self, title: str, msg: str):
        content = BoxLayout(orientation="vertical", padding=10)
        content.add_widget(Label(text=msg))
        btn = Button(text="好的", size_hint=(1, 0.3))
        popup = Popup(title=title, content=content, size_hint=(0.8, 0.4))
        btn.bind(on_press=popup.dismiss)
        content.add_widget(btn)
        popup.open()


class VideoApp(App):
    def build(self):
        self.title = "川叶视频模块"
        return VideoAppUI()


if __name__ == "__main__":
    VideoApp().run()