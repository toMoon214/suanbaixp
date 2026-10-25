"""
屏幕截图和模板匹配功能（基于 OpenCV）。
"""

import os
import sys
import time
import logging
import ctypes
import ctypes.wintypes

import cv2
import numpy as np
import mss

from config import TEMPLATE_DIR

logger = logging.getLogger(__name__)

# Windows API
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
advapi32 = ctypes.windll.advapi32

SW_RESTORE = 9
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_ELEVATION = 20


def get_template_path(filename: str) -> str:
    """获取模板文件的路径，兼容 PyInstaller 打包。"""
    if hasattr(sys, '_MEIPASS'):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, TEMPLATE_DIR, filename)


def find_game_window_hwnd() -> int | None:
    """
    查找 300英雄 游戏窗口句柄。
    跳过最小化/过小的窗口；有多个候选时取面积最大的。
    """
    game_titles = ['300英雄', '300Heroes']
    candidates = []

    def enum_callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if not any(gt in title for gt in game_titles):
            return True
        # 检查窗口是否最小化或隐藏 (位置 -32000 = 最小化)
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if rect.left <= -30000 or rect.top <= -30000 or w < 400 or h < 300:
            logger.debug(f'跳过无效窗口: "{title}" at ({rect.left},{rect.top}) size {w}x{h}')
            return True
        candidates.append((w * h, int(hwnd), title))
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(enum_callback), 0)

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    if len(candidates) > 1:
        logger.debug(f'发现 {len(candidates)} 个候选窗口，选用面积最大的: {candidates[0][2]}')
    return candidates[0][1]


def find_game_window() -> dict | None:
    """
    查找 300英雄 游戏窗口，返回 mss 格式的区域字典。
    """
    hwnd = find_game_window_hwnd()
    if hwnd is None:
        return None

    rect = ctypes.wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))

    region = {
        'left': rect.left,
        'top': rect.top,
        'width': rect.right - rect.left,
        'height': rect.bottom - rect.top,
    }
    logger.info(f'找到游戏窗口: {region}')
    return region


def ensure_game_foreground() -> bool:
    """确保游戏窗口位于前台（点击才会作用于游戏）。"""
    hwnd = find_game_window_hwnd()
    if hwnd is None:
        return False
    if user32.GetForegroundWindow() == hwnd:
        return True

    user32.ShowWindow(hwnd, SW_RESTORE)
    # 前台窗口锁：临时附加到当前前台线程才能可靠地 SetForegroundWindow
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    me_tid = kernel32.GetCurrentThreadId()
    attached = bool(user32.AttachThreadInput(me_tid, fg_tid, True))
    try:
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(me_tid, fg_tid, False)

    time.sleep(0.2)
    ok = user32.GetForegroundWindow() == hwnd
    if not ok:
        logger.warning('无法将游戏窗口切换到前台，点击可能无效')
    return ok


# ==================== 权限（UIPI）检查 ====================

class _TOKEN_ELEVATION(ctypes.Structure):
    _fields_ = [('TokenIsElevated', ctypes.wintypes.DWORD)]


def is_process_elevated(pid: int) -> bool | None:
    """查询指定进程是否以管理员权限运行。None 表示无权查询（通常也意味着它是高权限进程）。"""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    token = ctypes.wintypes.HANDLE()
    if not advapi32.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(token)):
        kernel32.CloseHandle(h)
        return None
    elev = _TOKEN_ELEVATION()
    ret = ctypes.wintypes.DWORD()
    ok = advapi32.GetTokenInformation(token, TOKEN_ELEVATION,
                                      ctypes.byref(elev), ctypes.sizeof(elev),
                                      ctypes.byref(ret))
    kernel32.CloseHandle(token)
    kernel32.CloseHandle(h)
    return bool(elev.TokenIsElevated) if ok else None


def self_elevated() -> bool:
    """本进程是否以管理员权限运行。"""
    return bool(is_process_elevated(kernel32.GetCurrentProcessId()))


def detect_uipi_block() -> str | None:
    """
    检测「游戏高权限、本程序低权限」的组合。
    这种情况 Windows 会静默丢弃我们发出的鼠标消息，表现为点击完全无反应。
    返回警告文本，正常则返回 None。
    """
    hwnd = find_game_window_hwnd()
    if hwnd is None:
        return None
    pid = ctypes.wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    game_elev = is_process_elevated(pid.value)
    if game_elev and not self_elevated():
        return ('游戏以管理员权限运行，而本程序是普通权限。'
                'Windows 的 UIPI 会拦截点击，游戏不会有任何反应。'
                '解决办法：关闭本程序，右键 → 以管理员身份运行。')
    return None


# ==================== 模拟点击（SendInput） ====================

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ('dx', ctypes.wintypes.LONG),
        ('dy', ctypes.wintypes.LONG),
        ('mouseData', ctypes.wintypes.DWORD),
        ('dwFlags', ctypes.wintypes.DWORD),
        ('time', ctypes.wintypes.DWORD),
        ('dwExtraInfo', ctypes.POINTER(ctypes.wintypes.ULONG)),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [('mi', _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ('u',)
    _fields_ = [('type', ctypes.wintypes.DWORD), ('u', _INPUTUNION)]


def _send_mouse_event(flag: int) -> int:
    inp = _INPUT()
    inp.type = 0  # INPUT_MOUSE
    inp.mi.dwFlags = flag
    return user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def send_click(x: int, y: int, hold: float = 0.05) -> bool:
    """
    移动光标并发送左键点击。使用 SendInput（比已废弃的 mouse_event 兼容性更好）。
    返回是否成功提交事件（注意：被 UIPI 拦截时依然返回成功）。
    """
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.02)
    n_down = _send_mouse_event(MOUSEEVENTF_LEFTDOWN)
    time.sleep(hold)
    n_up = _send_mouse_event(MOUSEEVENTF_LEFTUP)
    return n_down == 1 and n_up == 1


# 缓存游戏窗口区域
_cached_game_region: dict | None = None


def get_window_offset() -> tuple[int, int]:
    """
    返回游戏窗口左上角的屏幕绝对坐标 (x, y)。
    截图中的坐标 + offset = 屏幕绝对坐标（用于 pyautogui.click）。
    """
    if _cached_game_region:
        return (_cached_game_region['left'], _cached_game_region['top'])
    region = find_game_window()
    if region:
        return (region['left'], region['top'])
    return (0, 0)


def screenshot() -> np.ndarray:
    """
    截取游戏窗口区域。
    优先查找游戏窗口，找不到则截取整个虚拟屏幕（所有显示器）。
    """
    global _cached_game_region
    
    with mss.mss() as sct:
        # 尝试用缓存的窗口区域
        if _cached_game_region:
            try:
                img = sct.grab(_cached_game_region)
                frame = np.array(img)
                return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            except Exception:
                _cached_game_region = None
        
        # 查找游戏窗口
        region = find_game_window()
        if region and region['width'] > 100 and region['height'] > 100:
            _cached_game_region = region
            img = sct.grab(region)
            frame = np.array(img)
            return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        
        # 回退：截取整个虚拟屏幕（monitors[0] = 所有显示器合并）
        logger.warning('未找到游戏窗口，截取全部屏幕')
        monitor = sct.monitors[0]
        img = sct.grab(monitor)
        frame = np.array(img)
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)


def screenshot_region(x: int, y: int, w: int, h: int) -> np.ndarray:
    """截取指定区域，返回 BGR numpy 数组。"""
    with mss.mss() as sct:
        region = {'left': x, 'top': y, 'width': w, 'height': h}
        img = sct.grab(region)
        frame = np.array(img)
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)


# ==================== 紧急停止 ====================

FAILSAFE_MARGIN = 10  # 左上角触发区域边长（像素）


def is_failsafe_triggered(margin: int = FAILSAFE_MARGIN) -> bool:
    """
    紧急停止开关：鼠标位于屏幕左上角区域时返回 True。

    本项目点击走 Win32 API 而非 pyautogui，所以 pyautogui.FAILSAFE
    不会生效，必须自己轮询光标位置。
    """
    pt = ctypes.wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return False
    return pt.x < margin and pt.y < margin


def load_template(filename: str) -> np.ndarray:
    """加载模板图片为 BGR numpy 数组（兼容中文路径）。"""
    path = get_template_path(filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f'模板文件不存在: {path}')
    # cv2.imread 在 Windows 上不认非 ASCII 路径，改用 np.fromfile + imdecode
    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f'模板文件无法解码（可能已损坏）: {path}')
    return img


def save_image(path: str, img: np.ndarray) -> bool:
    """保存图片（兼容中文路径）。"""
    ext = os.path.splitext(path)[1] or '.png'
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        return False
    buf.tofile(path)
    return True


def find_template(screen: np.ndarray, template: np.ndarray,
                  threshold: float = 0.8) -> tuple[int, int] | None:
    """
    在截图中搜索模板，返回匹配中心坐标或 None。
    使用 OpenCV TM_CCOEFF_NORMED（归一化互相关）。
    """
    result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    
    if max_val >= threshold:
        th, tw = template.shape[:2]
        cx = max_loc[0] + tw // 2
        cy = max_loc[1] + th // 2
        return (cx, cy)
    return None


def find_template_score(screen: np.ndarray, template: np.ndarray
                        ) -> tuple[float, tuple[int, int]]:
    """
    在截图中搜索模板，返回 (最大分数, 匹配中心坐标)。
    """
    result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    th, tw = template.shape[:2]
    cx = max_loc[0] + tw // 2
    cy = max_loc[1] + th // 2
    return (max_val, (cx, cy))


def find_all_matches(screen: np.ndarray, template: np.ndarray,
                     threshold: float = 0.8, min_distance: int = 30
                     ) -> list[tuple[int, int]]:
    """
    在截图中搜索所有匹配位置。
    返回所有匹配的中心坐标列表。
    """
    result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
    locations = np.where(result >= threshold)
    
    th, tw = template.shape[:2]
    points = []
    
    for pt in zip(*locations[::-1]):  # (x, y)
        cx = pt[0] + tw // 2
        cy = pt[1] + th // 2
        
        # 去重：检查是否与已有点太近
        too_close = False
        for px, py in points:
            if abs(cx - px) < min_distance and abs(cy - py) < min_distance:
                too_close = True
                break
        if not too_close:
            points.append((cx, cy))
    
    return points
