"""Windows desktop entry point: local FastAPI + WebView2 window."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent

try:
    from edition import EDITION
except Exception:
    EDITION = "personal"

# 每个版本独立数据目录：个人版沿用旧目录，其余各用各的
_FOLDERS = {
    "personal": "Yijiang",
    "free": "YijiangFree",       # 与旧测试目录隔离，避免把本机历史/Key 带进新安装
    "lite": "YijiangLite",
    "buyout": "YijiangBuyout",
    "complete": "YijiangComplete",
    "enterprise": "YijiangEnterprise",
    "dev": "YijiangDev",
    "offline": "YijiangOffline",
}
_app_folder = _FOLDERS.get(EDITION, "Yijiang" + str(EDITION).capitalize())
user_root = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / _app_folder

# 便携模式：exe 旁边如果有 data\ （并且里面有授权状态），就用它。
# 否则用户直接双击便携包的 exe 时，会跑到 %LOCALAPPDATA% 的空白授权上，
# 明明预置了激活却显示「免费版 / 3 次严格」。
_exe_dir = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
_portable_dir = _exe_dir / "data"
_env_data = os.getenv("YIJIANG_DATA", "").strip()
if _env_data:
    data_dir = Path(_env_data)
elif _portable_dir.is_dir() and (_portable_dir / "entitlement_state.enc").exists():
    data_dir = _portable_dir
else:
    data_dir = user_root / "data"
try:
    data_dir.mkdir(parents=True, exist_ok=True)
except Exception:
    # Locked-down profile: keep the app usable with a temp data directory.
    data_dir = Path(tempfile.gettempdir()) / "Yijiang" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
os.environ["YIJIANG_DATA"] = str(data_dir)
os.environ["YIJIANG_CONFIG"] = str(data_dir / "config.json")
log_path = data_dir.parent / "startup.log"
# PyInstaller windowed apps have sys.stdout/sys.stderr == None. Uvicorn's
# default formatter calls sys.stderr.isatty(), so bind them before importing
# uvicorn and disable uvicorn's own dictConfig as well.
try:
    _log_stream = open(log_path, "a", encoding="utf-8", buffering=1)
except Exception:
    # Last-resort stream; never let logging prevent the app from starting.
    _log_stream = open(os.devnull, "w", encoding="utf-8")
if sys.stdout is None:
    sys.stdout = _log_stream
if sys.stderr is None:
    sys.stderr = _log_stream


def _log(message: str) -> None:
    try:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(message.rstrip() + "\n")
    except Exception:
        pass


def _show_error(message: str) -> None:
    _log(message)
    if os.name == "nt":
        try:
            ctypes.windll.user32.MessageBoxW(None, message, "译匠启动失败", 0x10)
        except Exception:
            pass


try:
    _log(f"starting {ROOT} data={data_dir}")
except Exception:
    pass

_BROWSER_PROFILE: Path | None = None


def _has_zone_marker(path: Path) -> bool:
    if os.name != "nt":
        return False
    try:
        with open(str(path) + ":Zone.Identifier", "rb"):
            return True
    except OSError:
        return False


def _launch_native_window(url: str) -> subprocess.Popen | None:
    source = ROOT / "resources" / "window"
    host = source / "YijiangWindow.exe"
    if os.name != "nt" or not host.exists():
        return None
    try:
        # A clean installed copy works in place. Files extracted from a
        # downloaded ZIP carry Zone.Identifier streams and WebView2 silently
        # refuses them, so only then stage an unblocked copy elsewhere.
        marked = any(_has_zone_marker(item) for item in source.iterdir() if item.is_file())
        if marked:
            target = host
            candidates = (
                ROOT / "windowhost",
                data_dir / "windowhost",
                Path(tempfile.gettempdir()) / "YijiangWindowHost",
            )
            for local in candidates:
                try:
                    local.mkdir(parents=True, exist_ok=True)
                    for item in source.iterdir():
                        if not item.is_file():
                            continue
                        dst = local / item.name
                        if not dst.exists() or dst.stat().st_size != item.stat().st_size:
                            shutil.copyfile(item, dst)
                        try:
                            os.remove(str(dst) + ":Zone.Identifier")
                        except OSError:
                            pass
                    candidate = local / "YijiangWindow.exe"
                    if candidate.exists():
                        target = candidate
                        break
                except OSError:
                    continue
            if target == host:
                target = host
        else:
            target = host
        proc = subprocess.Popen(
            [str(target), url],
            cwd=str(target.parent),
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _log(f"native window started: {target}")
        return proc
    except Exception:
        _log("native window failed:\n" + traceback.format_exc())
        return None


def _process_running(name: str) -> bool:
    try:
        result = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"], capture_output=True, text=True, timeout=5)
        return name.lower() in result.stdout.lower()
    except Exception:
        return False


def _open_app_window(url: str, prefer_chrome: bool = True) -> subprocess.Popen | None:
    """Start a real app window with a unique profile.

    A fixed profile lets a running Edge instance swallow --app and open a tab
    in the user's existing browser. A per-launch profile forces a new browser
    process/window.
    """
    global _BROWSER_PROFILE
    chrome = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
    ]
    edge = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    # Chrome first: it keeps a separate app process even when Edge already has
    # the user's normal browser window open. Callers can force Edge fallback.
    candidates = chrome + edge if prefer_chrome else edge + chrome
    profile = data_dir.parent / "runtime" / f"browser-{os.getpid()}-{int(time.time())}"
    try:
        profile.mkdir(parents=True, exist_ok=True)
    except Exception:
        try:
            profile = Path(tempfile.gettempdir()) / "Yijiang" / f"browser-{os.getpid()}-{int(time.time())}"
            profile.mkdir(parents=True, exist_ok=True)
        except Exception:
            _log("browser profile mkdir failed:\n" + traceback.format_exc())
            return None
    for exe in candidates:
        if not exe.exists():
            continue
        try:
            proc = subprocess.Popen(
                [
                    str(exe),
                    f"--app={url}",
                    "--window-size=1440,920",
                    "--window-position=120,80",
                    f"--user-data-dir={profile}",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _BROWSER_PROFILE = profile
            _log(f"browser app started: {exe} profile={profile}")
            return proc
        except Exception:
            _log("browser app failed:\n" + traceback.format_exc())
    return None


# Single-instance guard must run before importing app/store. Otherwise a second
# direct launch can fail on the first instance's SQLite file before it can
# discover that an instance is already running.
#
# 互斥体按“数据目录”区分：四个档位（以及便携副本）各自的数据目录不同，
# 共用一个全局互斥体会导致「启动 A 档时被 B 档的进程挡住」——
# 然后拿 B 档的 server.json 开窗口，令牌对不上就出现 403「本地接口需要授权」。
_dir_key = hashlib.sha1(str(data_dir).lower().encode("utf-8")).hexdigest()[:10]
MUTEX_NAME = f"Local\\YijiangStudio_{_dir_key}" + (
    "_dev" if os.getenv("YIJIANG_DEV") == "1" else ""
)


def _kill_orphan_windows() -> None:
    """关掉「父进程已经没了」的窗口宿主。

    Yijiang.exe 被强杀/崩溃时，YijiangWindow.exe 会留下来并显示
    「127.0.0.1 拒绝连接」。启动时清掉这些孤儿窗口，避免用户以为程序坏了。
    """
    if os.name != "nt":
        return
    ps = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$all=Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name;"
        "$alive=@{}; foreach($p in $all){ $alive[[int]$p.ProcessId]=$true };"
        "foreach($p in $all){"
        " if($p.Name -eq 'YijiangWindow.exe' -and -not $alive.ContainsKey([int]$p.ParentProcessId)){"
        "  Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue; Write-Output $p.ProcessId"
        " }"
        "}"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=20, creationflags=0x08000000,
        )
        killed = [x for x in (out.stdout or "").split() if x.strip().isdigit()]
        if killed:
            _log(f"closed orphan window(s): {killed}")
    except Exception:
        _log("orphan window cleanup failed:\n" + traceback.format_exc())


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        return str(pid) in out
    except Exception:
        return False


def _server_reachable(url: str, timeout: float = 1.2) -> bool:
    """只做 TCP 连通性探测。

    server.json 在正常退出时会删除，但崩溃/被任务管理器杀掉时会留下死地址；
    旧逻辑直接拿它开窗口，于是窗口连到一个没有服务的端口，表现为
    「本地服务连接中断 / 译匠的后台进程没有响应」。
    """
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or "127.0.0.1"
        port = parts.port or 80
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


_mutex = None
try:
    if os.name == "nt":
        _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            info_path = data_dir.parent / "server.json"
            url = ""
            pid = 0
            try:
                payload = json.loads(info_path.read_text(encoding="utf-8"))
                url = str(payload.get("url") or "")
                pid = int(payload.get("pid") or 0)
            except Exception:
                pass
            alive = bool(url) and _pid_alive(pid)
            reachable = alive and _server_reachable(url)
            if alive and not reachable:
                # 进程还在但端口没起来：给它几秒（刚启动 / 正在重启）
                for _ in range(6):
                    time.sleep(0.5)
                    if _server_reachable(url):
                        reachable = True
                        break
            if reachable:
                proc = _launch_native_window(url) or _open_app_window(url, prefer_chrome=_process_running("msedge.exe"))
                if proc is not None:
                    proc.wait()
                    if _BROWSER_PROFILE:
                        shutil.rmtree(_BROWSER_PROFILE, ignore_errors=True)
                else:
                    _show_error("找不到 Edge/Chrome，无法打开 app 窗口。请安装 Edge 或 Chrome 后重试。")
                sys.exit(0)
            # 旧实例已经不在了：清掉死地址，继续以正常流程启动，自己起服务。
            _log(f"stale server.json ignored (url={url!r} pid={pid} alive={alive} reachable={reachable})")
            try:
                info_path.unlink(missing_ok=True)
            except Exception:
                pass
except SystemExit:
    raise
except Exception:
    _log("mutex setup failed:\n" + traceback.format_exc())

# 本地接口 token：必须在 import app 之前写入环境，app.py 的中间件才会启用。
API_TOKEN = os.environ.get("YIJIANG_API_TOKEN") or secrets.token_urlsafe(24)
os.environ["YIJIANG_API_TOKEN"] = API_TOKEN

# seed runtime config that is not part of the encrypted KB
for name in ("languages.json", "sample_en.txt", "sample_zh.txt"):
    target = data_dir / name
    if target.exists():
        continue
    for candidate in (ROOT / "resources" / name, ROOT / "data" / name):
        if candidate.exists():
            shutil.copy2(candidate, target)
            break

try:
    import uvicorn  # noqa: E402

    from app import _free_port, app, cfg  # noqa: E402
except Exception:
    _show_error("应用启动失败，详情已写入：\n" + str(log_path) + "\n\n" + traceback.format_exc())
    sys.exit(1)


def _wait_port(host: str, port: int, timeout: float = 12.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            if sock.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.1)
    return False


def _launch_browser_window(url: str, prefer_chrome: bool = True) -> subprocess.Popen | None:
    return _open_app_window(url, prefer_chrome=prefer_chrome)


def main() -> None:
    _log("main")
    port = _free_port(cfg.host, cfg.port)
    url = f"http://{cfg.host}:{port}"
    ui_url = f"{url}/?token={API_TOKEN}"
    _log(f"url={url}")
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=cfg.host,
            port=port,
            log_level="warning",
            log_config=None,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_port(cfg.host, port)
    _log("server ready")
    server_info = data_dir.parent / "server.json"
    # 原子写：另一个实例可能同时读这个文件，半截 JSON 会被当成「无实例」。
    tmp_info = server_info.with_name("server.json.tmp")
    try:
        tmp_info.write_text(json.dumps({"url": ui_url, "pid": os.getpid()}), encoding="utf-8")
        os.replace(tmp_info, server_info)
    except Exception:
        try:
            tmp_info.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            server_info.write_text(json.dumps({"url": ui_url, "pid": os.getpid()}), encoding="utf-8")
        except Exception:
            pass

    heartbeat_path = data_dir / "heartbeat"
    try:
        heartbeat_path.unlink(missing_ok=True)
    except Exception:
        pass
    browser: subprocess.Popen | None = None
    try:
        _kill_orphan_windows()
        browser = _launch_native_window(ui_url)
        if browser is None:
            browser = _launch_browser_window(ui_url, prefer_chrome=_process_running("msedge.exe"))
        if browser is not None:
            # If the first browser produced no app window, try the other one.
            probe_until = time.time() + 15
            while time.time() < probe_until and not heartbeat_path.exists():
                time.sleep(0.5)
            if not heartbeat_path.exists():
                _log("first browser produced no UI heartbeat; trying fallback")
                try:
                    browser.terminate()
                except Exception:
                    pass
                browser = _launch_browser_window(ui_url, prefer_chrome=False)
        if browser is not None:
            # Chromium may return immediately after handing the window to a
            # browser process. Keep the local server alive while the UI sends
            # heartbeats; closing the app window stops them and exits here.
            started = time.time()
            saw_heartbeat = False
            while thread.is_alive():
                try:
                    age = time.time() - heartbeat_path.stat().st_mtime
                    saw_heartbeat = True
                except Exception:
                    age = None
                if saw_heartbeat:
                    if age is not None and age > 35:
                        break
                elif time.time() - started > 75:
                    _log("no UI heartbeat after launch")
                    _show_error("浏览器窗口没有成功打开。请确认 Edge/Chrome 可正常启动，或查看 startup.log。")
                    break
                time.sleep(2)
            _log("browser window closed")
        else:
            _show_error("找不到 Edge/Chrome，无法打开 app 窗口。请安装 Edge 或 Chrome 后重试。")
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        # 先把窗口收掉，避免留下指向已停服务的孤儿窗口
        try:
            if browser is not None:
                browser.terminate()
        except Exception:
            pass
        try:
            server_info.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            heartbeat_path.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            if _mutex:
                ctypes.windll.kernel32.CloseHandle(_mutex)
        except Exception:
            pass
        try:
            if _BROWSER_PROFILE:
                shutil.rmtree(_BROWSER_PROFILE, ignore_errors=True)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _show_error("应用运行失败，详情已写入：\n" + str(log_path) + "\n\n" + traceback.format_exc())
        sys.exit(1)
