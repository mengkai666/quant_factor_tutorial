"""桌面端壳 (Desktop Shell) — 用 pywebview 把每日报告站点装进原生窗口。

设计取向: 内容即产品。窗口内容 = 站点本体, 桌面壳只做增量:
  1. 右上角悬浮胶囊 (首页/刷新/日志/归档), 需要时才出现
  2. 跑批完毕自动返回首页并重载
  3. 首次运行 (site/ 未生成) 显示引导页, 一键完成初始化

零业务侵入: 不改任何计算/报告模块。
运行: python src/desktop_app.py
"""
import os
import subprocess
import sys
import threading
from datetime import datetime

import webview

from paths import BASE_DIR, SITE_DIR


SITE_INDEX = os.path.join(SITE_DIR, 'index.html')
MAIN_SCRIPT = os.path.join(BASE_DIR, 'src', '主线强度追踪.py')


def _file_url(path):
    """本地文件 -> file:/// URL (Windows 反斜杠转正斜杠)。"""
    return 'file:///' + os.path.abspath(path).replace('\\', '/')


# 注入到站点页面的悬浮控制条 + 日志抽屉。每次页面导航后重新注入。
# __HOME_URL__ 由 Python 在注入前替换为绝对 file:// URL。
INJECT_JS = r"""
(function () {
  if (window.__desktop_shell_injected) return;
  window.__desktop_shell_injected = true;

  const HOME_URL = '__HOME_URL__';
  const style = document.createElement('style');
  style.textContent = `
    #ds-panel { position: fixed; top: 14px; right: 14px; z-index: 99999;
      display: flex; flex-direction: column; align-items: flex-end; gap: 8px;
      font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; }
    #ds-bar { display: flex; gap: 4px; background: rgba(22,27,34,.85);
      backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
      border: 1px solid rgba(255,255,255,.08); border-radius: 999px;
      padding: 5px; box-shadow: 0 8px 24px rgba(0,0,0,.4); }
    .ds-btn { border: 0; background: transparent; color: #c9d1d9;
      padding: 7px 14px; border-radius: 999px; font-size: 12px; font-weight: 600;
      cursor: pointer; transition: background .15s, color .15s; white-space: nowrap;
      display: inline-flex; align-items: center; gap: 6px; user-select: none; }
    .ds-btn:hover { background: rgba(255,255,255,.06); color: #fff; }
    .ds-btn.primary { background: #238636; color: #fff; }
    .ds-btn.primary:hover { background: #2ea043; }
    .ds-btn:disabled { opacity: .55; cursor: progress; }
    .ds-dot { width: 6px; height: 6px; border-radius: 999px; background: #3fb950;
      flex-shrink: 0; box-shadow: 0 0 8px rgba(63,185,80,.6); }
    .ds-dot.running { background: #d29922; box-shadow: 0 0 8px rgba(210,153,34,.7);
      animation: ds-pulse 1s ease-in-out infinite; }
    @keyframes ds-pulse { 50% { opacity: .35; } }
    #ds-drawer { width: 480px; max-width: calc(100vw - 32px);
      background: rgba(13,17,23,.96);
      backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
      border: 1px solid rgba(255,255,255,.08); border-radius: 12px;
      padding: 14px 16px; color: #8b949e;
      font-family: Consolas, "Cascadia Code", ui-monospace, monospace;
      font-size: 11px; line-height: 1.6; white-space: pre-wrap;
      max-height: 340px; overflow: auto; display: none;
      box-shadow: 0 12px 40px rgba(0,0,0,.55); }
    #ds-drawer.open { display: block; }
    #ds-drawer::-webkit-scrollbar { width: 6px; }
    #ds-drawer::-webkit-scrollbar-thumb { background: #30363d; border-radius: 3px; }
  `;
  document.head.appendChild(style);

  const panel = document.createElement('div');
  panel.id = 'ds-panel';
  panel.innerHTML = `
    <div id="ds-bar">
      <button class="ds-btn" id="ds-home" title="回到首页">⌂</button>
      <button class="ds-btn primary" id="ds-refresh"><span class="ds-dot" id="ds-dot"></span>刷新今日</button>
      <button class="ds-btn" id="ds-log">日志</button>
      <button class="ds-btn" id="ds-dir">归档</button>
    </div>
    <div id="ds-drawer">就绪。</div>
  `;
  document.body.appendChild(panel);

  const drawer = document.getElementById('ds-drawer');
  const dot = document.getElementById('ds-dot');
  const btnRefresh = document.getElementById('ds-refresh');

  document.getElementById('ds-home').onclick = () => {
    window.location.href = HOME_URL + '?t=' + Date.now();
  };
  document.getElementById('ds-log').onclick = () => drawer.classList.toggle('open');
  document.getElementById('ds-dir').onclick = () => window.pywebview.api.open_site_dir();

  let poller = null;
  function startPolling() {
    if (poller) return;
    btnRefresh.disabled = true;
    dot.classList.add('running');
    drawer.classList.add('open');
    poller = setInterval(pollStatus, 800);
  }
  async function pollStatus() {
    const s = await window.pywebview.api.get_status();
    drawer.textContent = s.log_tail || '就绪。';
    drawer.scrollTop = drawer.scrollHeight;
    if (!s.running) {
      clearInterval(poller); poller = null;
      btnRefresh.disabled = false;
      dot.classList.remove('running');
      window.pywebview.api.reload_after_run();
    }
  }
  btnRefresh.onclick = async () => {
    const r = await window.pywebview.api.refresh_report();
    drawer.textContent = r.msg;
    if (!r.ok) return;
    startPolling();
  };

  // 页面导航后重新注入时, 若有在跑的任务, 继续挂上轮询
  (async () => {
    const s = await window.pywebview.api.get_status();
    if (s.running) { drawer.textContent = s.log_tail || '跑批中…'; startPolling(); }
  })();
})();
"""


EMPTY_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>主线强度追踪 · 首次运行</title>
<style>
  :root { color-scheme: dark; }
  html, body { margin: 0; height: 100%; background: #0d1117; color: #e6edf3;
    font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; }
  .wrap { display: flex; height: 100%; align-items: center; justify-content: center;
    flex-direction: column; gap: 24px; text-align: center; padding: 48px; }
  .brand { font-size: 12px; color: #8b949e; letter-spacing: 3px; text-transform: uppercase; }
  h1 { font-size: 32px; font-weight: 800; letter-spacing: -.5px; margin: 0; }
  p { color: #8b949e; font-size: 14px; max-width: 520px; line-height: 1.8; margin: 0; }
  button { background: #238636; color: #fff; border: 0; border-radius: 12px;
    padding: 16px 40px; font-size: 15px; font-weight: 700; cursor: pointer;
    transition: background .15s, transform .1s; box-shadow: 0 4px 20px rgba(35,134,54,.35); }
  button:hover { background: #2ea043; }
  button:active { transform: translateY(1px); }
  button:disabled { background: #30363d; cursor: progress; box-shadow: none; }
  .log { width: 640px; max-width: 100%; margin-top: 8px; padding: 16px 18px;
    background: rgba(13,17,23,.6); border: 1px solid #21262d; border-radius: 10px;
    color: #8b949e; font-family: Consolas, ui-monospace, monospace; font-size: 11px;
    line-height: 1.65; white-space: pre-wrap; text-align: left; max-height: 320px;
    overflow: auto; display: none; }
  .log.on { display: block; }
</style></head>
<body><div class="wrap">
  <div class="brand">A股短线主线终端</div>
  <h1>主线强度追踪</h1>
  <p>还没有生成过日报。点击下方按钮跑第一次批, 大约 3–5 分钟, 期间可最小化。完成后自动进入报告首页。</p>
  <button id="btn" onclick="run()">开始首次跑批</button>
  <div class="log" id="log"></div>
</div>
<script>
  let poller = null;
  async function run() {
    const b = document.getElementById('btn'); const l = document.getElementById('log');
    b.disabled = true; b.textContent = '跑批中…'; l.classList.add('on');
    const r = await window.pywebview.api.refresh_report();
    l.textContent = r.msg;
    if (!r.ok) { b.disabled = false; b.textContent = '开始首次跑批'; return; }
    poller = setInterval(async () => {
      const s = await window.pywebview.api.get_status();
      l.textContent = s.log_tail || '';
      l.scrollTop = l.scrollHeight;
      if (!s.running) { clearInterval(poller); window.pywebview.api.reload_after_run(); }
    }, 800);
  }
</script></body></html>
"""


class Api:
    def __init__(self):
        self._window = None
        self._running = False
        self._log_lines = []

    def bind_window(self, window):
        self._window = window

    def refresh_report(self):
        if self._running:
            return {'ok': False, 'msg': '已有一次跑批在进行, 请稍候…'}
        if not os.path.exists(MAIN_SCRIPT):
            return {'ok': False, 'msg': f'主脚本不存在: {MAIN_SCRIPT}'}
        self._running = True
        self._log_lines = []
        threading.Thread(target=self._run_worker, daemon=True).start()
        return {'ok': True, 'msg': '已开始跑批, 完成后自动刷新'}

    def get_status(self):
        return {
            'running': self._running,
            'log_tail': ''.join(self._log_lines[-80:]),
        }

    def open_site_dir(self):
        target = SITE_DIR if os.path.isdir(SITE_DIR) else BASE_DIR
        try:
            if sys.platform.startswith('win'):
                os.startfile(target)  # noqa: S606 - 打开系统文件夹, 路径为项目内固定
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', target])
            else:
                subprocess.Popen(['xdg-open', target])
            return {'ok': True}
        except Exception as e:
            return {'ok': False, 'msg': str(e)}

    def reload_after_run(self):
        """跑批完成后由前端回调, 载入最新 index.html (带时间戳绕过缓存)。"""
        if self._window is None or not os.path.exists(SITE_INDEX):
            return {'ok': False}
        try:
            self._window.load_url(_file_url(SITE_INDEX) + f'?t={int(datetime.now().timestamp())}')
        except Exception:
            pass
        return {'ok': True}

    def _run_worker(self):
        try:
            proc = subprocess.Popen(
                [sys.executable, MAIN_SCRIPT],
                cwd=os.path.dirname(MAIN_SCRIPT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
            )
            assert proc.stdout is not None  # PIPE 已声明, 让类型收窄
            for line in proc.stdout:
                self._log_lines.append(line)
            proc.wait()
            self._log_lines.append(
                f'\n[完成] 退出码 {proc.returncode} @ {datetime.now():%H:%M:%S}\n'
            )
        except Exception as e:
            self._log_lines.append(f'\n[异常] {e}\n')
        finally:
            self._running = False


def _inject_toolbar(window):
    """页面加载完成回调: 在站点页面注入悬浮控制条; 引导页跳过。"""
    try:
        current = window.get_current_url() or ''
        # 只在从磁盘加载的站点页注入; data:/about:blank 是引导页, 不注入
        if not current.startswith('file://'):
            return
        js = INJECT_JS.replace('__HOME_URL__', _file_url(SITE_INDEX))
        window.evaluate_js(js)
    except Exception:
        pass


def main():
    api = Api()
    if os.path.exists(SITE_INDEX):
        window = webview.create_window(
            title='主线强度追踪',
            url=_file_url(SITE_INDEX),
            js_api=api,
            width=1280,
            height=840,
            min_size=(960, 640),
            background_color='#0d1117',
        )
    else:
        window = webview.create_window(
            title='主线强度追踪',
            html=EMPTY_HTML,
            js_api=api,
            width=960,
            height=640,
            min_size=(720, 480),
            background_color='#0d1117',
        )
    assert window is not None  # create_window 类型标注为 Optional, 实际不会失败
    api.bind_window(window)
    window.events.loaded += lambda: _inject_toolbar(window)
    webview.start()


if __name__ == '__main__':
    main()
