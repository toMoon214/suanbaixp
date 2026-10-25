"""
300英雄 占卜自动化 - GUI 界面
"""

import sys
import time
import logging
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext

import numpy as np

from config import (
    ARROW_TO_LINE, DELAY_AFTER_START, DELAY_AFTER_FLIP, DELAY_AFTER_CHOOSE,
    DELAY_AFTER_CONFIRM, DELAY_BETWEEN_ACTIONS, DELAY_SCAN_RETRY,
)
from capture import (
    screenshot, get_window_offset, is_failsafe_triggered,
    send_click, ensure_game_foreground, detect_uipi_block,
)
from recognition import (
    TemplateManager, find_button, find_arrows, scan_all_cells,
    compute_grid_from_arrows
)
from strategy import choose_best_arrow, choose_next_flip

logger = logging.getLogger('divination')


# ==================== 日志处理器：写入 GUI ====================

class TextBoxHandler(logging.Handler):
    """将日志输出到 tkinter ScrolledText 控件。"""
    
    def __init__(self, text_widget: scrolledtext.ScrolledText):
        super().__init__()
        self.text_widget = text_widget
    
    def emit(self, record):
        msg = self.format(record) + '\n'
        try:
            self.text_widget.after(0, self._append, msg)
        except Exception:
            pass
    
    def _append(self, msg: str):
        self.text_widget.configure(state='normal')
        self.text_widget.insert(tk.END, msg)
        self.text_widget.see(tk.END)
        self.text_widget.configure(state='disabled')
        # 限制行数，防止内存溢出
        lines = int(self.text_widget.index('end-1c').split('.')[0])
        if lines > 2000:
            self.text_widget.configure(state='normal')
            self.text_widget.delete('1.0', f'{lines - 1500}.0')
            self.text_widget.configure(state='disabled')


# ==================== 点击工具 ====================

import ctypes

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

stop_event = threading.Event()
_failsafe_reported = False


def check_stop() -> bool:
    """
    统一停止判定：用户点停止，或鼠标移到屏幕左上角（紧急停止）。
    返回 True 表示应当中止当前动作。
    """
    global _failsafe_reported
    if stop_event.is_set():
        return True
    if is_failsafe_triggered():
        if not _failsafe_reported:
            logger.warning('检测到鼠标位于屏幕左上角 → 紧急停止')
            _failsafe_reported = True
        stop_event.set()
        return True
    return False


def safe_click(x: int, y: int, delay: float = DELAY_BETWEEN_ACTIONS):
    """移动光标并点击游戏窗口（SendInput）。"""
    if check_stop():
        return
    ox, oy = get_window_offset()
    if not send_click(int(x + ox), int(y + oy)):
        logger.warning('点击事件发送失败')
    time.sleep(delay)


def wait_for(condition_fn, timeout: float = 10.0, interval: float = 0.5,
             desc: str = '') -> bool:
    """等待条件满足。"""
    start = time.time()
    while time.time() - start < timeout:
        if check_stop():
            return False
        if condition_fn():
            return True
        time.sleep(interval)
    logger.warning(f'等待超时: {desc}')
    return False


# ==================== 占卜核心逻辑 ====================

def run_single_divination(templates: TemplateManager) -> bool:
    """执行一次完整的占卜流程。"""
    if check_stop():
        return False
    
    # 确保游戏在前台，否则点击会落到别的窗口上
    ensure_game_foreground()
    
    # 步骤 1: 点击"开始占卜"
    logger.info('=' * 50)
    logger.info('步骤 1: 寻找并点击"开始占卜"按钮')
    
    screen = screenshot()
    start_pos = find_button(screen, templates, 'start')
    if not start_pos:
        logger.error('未找到"开始占卜"按钮')
        return False
    
    logger.info(f'找到"开始占卜"按钮: {start_pos}')
    safe_click(*start_pos, delay=DELAY_AFTER_START)
    
    # 步骤 2: 定位箭头，计算网格
    logger.info('步骤 2: 定位箭头，计算网格坐标...')
    
    screen = screenshot()
    arrows = find_arrows(screen, templates, selected=False)
    
    if len(arrows) < 6:
        logger.warning(f'只找到 {len(arrows)} 个箭头，等待重试...')
        time.sleep(1.0)
        screen = screenshot()
        arrows = find_arrows(screen, templates, selected=False)
    
    grid_positions = compute_grid_from_arrows(arrows)
    if len(grid_positions) < 9:
        logger.error(f'只能确定 {len(grid_positions)}/9 个格子坐标')
        return False
    
    for pos in range(9):
        r, c = pos // 3, pos % 3
        x, y = grid_positions[pos]
        logger.debug(f'  pos={pos} (行{r},列{c}): ({x}, {y})')
    
    # 步骤 3: 等待系统翻牌
    logger.info('步骤 3: 等待系统自动翻牌...')
    
    revealed = {}
    
    def detect_auto_reveal():
        nonlocal revealed
        screen = screenshot()
        revealed = scan_all_cells(screen, templates, grid_positions)
        return len(revealed) >= 1
    
    if not wait_for(detect_auto_reveal, timeout=8.0, interval=DELAY_SCAN_RETRY,
                    desc='系统翻牌'):
        logger.error('未检测到系统自动翻牌')
        return False
    
    logger.info(f'系统翻牌: {revealed}')
    
    # 步骤 4: 自适应翻牌
    logger.info('步骤 4: 自适应翻牌...')
    
    for flip_num in range(1, 4):
        if check_stop():
            return False
        
        next_pos = choose_next_flip(revealed)
        click_x, click_y = grid_positions[next_pos]
        logger.info(f'翻开第 {flip_num}/3 张: pos={next_pos} at ({click_x}, {click_y})')
        
        expected_count = len(revealed) + 1
        
        for attempt in range(3):
            safe_click(click_x, click_y, delay=DELAY_AFTER_FLIP)
            time.sleep(0.3)
            
            screen = screenshot()
            revealed = scan_all_cells(screen, templates, grid_positions)
            
            if len(revealed) >= expected_count:
                logger.info(f'  翻牌成功 ({len(revealed)} 张)')
                break
            else:
                logger.warning(f'  翻牌未生效 (第{attempt+1}次), 重试...')
                time.sleep(0.5)
        else:
            logger.warning('  翻牌重试3次仍未成功，继续...')
    
    # 步骤 5: 最终扫描
    logger.info('步骤 5: 最终扫描确认...')
    
    def scan_all_cards():
        nonlocal revealed
        screen = screenshot()
        revealed = scan_all_cells(screen, templates, grid_positions)
        return len(revealed) >= 4
    
    if not wait_for(scan_all_cards, timeout=5.0, interval=DELAY_SCAN_RETRY,
                    desc='扫描4张牌'):
        logger.warning(f'只识别到 {len(revealed)} 张牌')
    
    logger.info(f'已翻开的牌: {revealed}')
    
    # 步骤 6: 选择最佳线
    logger.info('步骤 6: 计算期望值...')
    best_arrow, best_ev = choose_best_arrow(revealed)
    logger.info(f'选择箭头 l{best_arrow}, 期望值={best_ev:.1f}')
    
    # 步骤 7: 点击箭头（带重试）
    logger.info('步骤 7: 点击箭头...')
    
    def wait_for_choose_prompt():
        screen = screenshot()
        return find_button(screen, templates, 'choose') is not None
    
    wait_for(wait_for_choose_prompt, timeout=5.0, interval=0.5, desc='选择界面')
    
    for attempt in range(3):
        screen = screenshot()
        arrows = find_arrows(screen, templates, selected=False)
        
        target = best_arrow if best_arrow in arrows else (list(arrows.keys())[0] if arrows else None)
        if target is None:
            logger.error('未找到任何箭头')
            return False
        
        arrow_pos = arrows[target]
        logger.info(f'点击箭头 l{target} at {arrow_pos} (第{attempt+1}次)')
        safe_click(*arrow_pos, delay=DELAY_AFTER_CHOOSE)
        
        time.sleep(0.3)
        screen = screenshot()
        if find_button(screen, templates, 'confirm') is not None:
            break
        else:
            logger.warning('点击箭头后未出现确认按钮，重试...')
            time.sleep(0.5)
    
    # 步骤 8: 点击确认（带重试）
    logger.info('步骤 8: 点击确认...')
    
    for attempt in range(3):
        screen = screenshot()
        confirm_pos = find_button(screen, templates, 'confirm')
        
        if confirm_pos:
            logger.info(f'点击确认: {confirm_pos}')
            safe_click(*confirm_pos, delay=DELAY_AFTER_CONFIRM)
            
            time.sleep(0.5)
            screen = screenshot()
            if find_button(screen, templates, 'confirm') is None:
                break
            logger.warning('确认按钮仍在，重试...')
        else:
            logger.warning(f'未找到确认按钮 (第{attempt+1}次)')
            time.sleep(1.0)
    
    logger.info('本轮占卜完成!')
    return True


def run_automation(templates: TemplateManager, max_rounds: int,
                   on_round_done=None, on_finished=None):
    """主循环。on_round_done(remaining) 每轮完成后回调。"""
    global _failsafe_reported
    stop_event.clear()
    _failsafe_reported = False
    
    logger.info('=' * 60)
    logger.info(f'自动占卜开始! (最大 {max_rounds} 轮)')
    logger.info('鼠标移到屏幕左上角可紧急停止')
    logger.info('=' * 60)
    
    round_count = 0
    remaining = max_rounds
    
    while remaining > 0:
        if check_stop():
            break
        round_count += 1
        logger.info(f'\n===== 第 {round_count} 轮 (剩余{remaining}次) =====')
        
        try:
            success = run_single_divination(templates)
            if success:
                remaining -= 1
                if on_round_done:
                    on_round_done(remaining)
                if remaining <= 0:
                    logger.info('剩余次数已用完，自动停止')
                    break
            else:
                logger.info('本轮未成功，等待后重试...')
                time.sleep(2.0)
                
                screen = screenshot()
                if not find_button(screen, templates, 'start'):
                    logger.info('未找到"开始占卜"按钮，占卜可能已完成')
                    break
        except Exception as e:
            logger.error(f'执行出错: {e}', exc_info=True)
            time.sleep(2.0)
    
    logger.info(f'\n自动占卜结束! 共执行 {round_count} 轮')
    
    if on_finished:
        on_finished()


# ==================== GUI ====================

class DivinationGUI:
    """占卜自动化 GUI。"""
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title('感觉不如zmd')
        self.root.geometry('680x520')
        self.root.resizable(True, True)
        self.root.configure(bg='#1a1a2e')
        
        self.templates = None
        self.running = False
        self.worker_thread = None
        
        self._setup_styles()
        self._build_ui()
        self._setup_logging()
        
        # 关闭时清理
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
    
    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')
        
        # 按钮样式
        style.configure('Start.TButton',
                        font=('Microsoft YaHei', 12, 'bold'),
                        padding=(20, 10))
        style.configure('Stop.TButton',
                        font=('Microsoft YaHei', 12, 'bold'),
                        padding=(20, 10))
        style.configure('Title.TLabel',
                        font=('Microsoft YaHei', 16, 'bold'),
                        foreground='#e0e0e0',
                        background='#1a1a2e')
        style.configure('Status.TLabel',
                        font=('Microsoft YaHei', 10),
                        foreground='#aaaaaa',
                        background='#1a1a2e')
        style.configure('Info.TLabel',
                        font=('Microsoft YaHei', 9),
                        foreground='#888888',
                        background='#1a1a2e')
    
    def _build_ui(self):
        # 标题区域
        title_frame = tk.Frame(self.root, bg='#1a1a2e')
        title_frame.pack(fill='x', padx=15, pady=(15, 5))
        
        ttk.Label(title_frame, text='自动好运星盘',
                  style='Title.TLabel').pack(side='left')
        
        self.status_label = ttk.Label(title_frame, text='就绪',
                                      style='Status.TLabel')
        self.status_label.pack(side='right')
        
        # 控制区域
        ctrl_frame = tk.Frame(self.root, bg='#1a1a2e')
        ctrl_frame.pack(fill='x', padx=15, pady=8)
        
        # 左侧：次数设置 + 剩余显示
        left_frame = tk.Frame(ctrl_frame, bg='#1a1a2e')
        left_frame.pack(side='left')
        
        ttk.Label(left_frame, text='剩余次数:',
                  style='Info.TLabel').pack(side='left', padx=(0, 5))
        
        self.rounds_var = tk.StringVar(value='0')
        rounds_entry = tk.Entry(left_frame, textvariable=self.rounds_var,
                                width=5, font=('Consolas', 11),
                                bg='#16213e', fg='#e0e0e0',
                                insertbackground='#e0e0e0',
                                relief='flat', bd=2)
        rounds_entry.pack(side='left')
        
        # 大字剩余次数显示
        self.remaining_label = tk.Label(
            left_frame, text='',
            font=('Microsoft YaHei', 14, 'bold'),
            fg='#ffd700', bg='#1a1a2e'
        )
        self.remaining_label.pack(side='left', padx=(15, 0))
        
        # 右侧：按钮
        btn_frame = tk.Frame(ctrl_frame, bg='#1a1a2e')
        btn_frame.pack(side='right')
        
        self.start_btn = ttk.Button(btn_frame, text='▶ 开始',
                                     style='Start.TButton',
                                     command=self._on_start)
        self.start_btn.pack(side='left', padx=5)
        
        self.stop_btn = ttk.Button(btn_frame, text='■ 停止',
                                    style='Stop.TButton',
                                    command=self._on_stop,
                                    state='disabled')
        self.stop_btn.pack(side='left', padx=5)
        
        # 日志区域
        log_frame = tk.Frame(self.root, bg='#1a1a2e')
        log_frame.pack(fill='both', expand=True, padx=15, pady=(5, 10))
        
        ttk.Label(log_frame, text='运行日志',
                  style='Info.TLabel').pack(anchor='w', pady=(0, 3))
        
        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            font=('Consolas', 9),
            bg='#0f0f23',
            fg='#cccccc',
            insertbackground='#cccccc',
            relief='flat',
            bd=0,
            state='disabled',
            wrap='word'
        )
        self.log_text.pack(fill='both', expand=True)
        
        # 底部信息
        info_frame = tk.Frame(self.root, bg='#1a1a2e')
        info_frame.pack(fill='x', padx=15, pady=(0, 10))
        
        ttk.Label(info_frame,
                  text='提示: 输入剩余次数后点「开始」　|　紧急停止: 鼠标移到屏幕左上角',
                  style='Info.TLabel').pack(side='left')
    
    def _setup_logging(self):
        """配置日志输出到 GUI。"""
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
        
        gui_handler = TextBoxHandler(self.log_text)
        gui_handler.setLevel(logging.INFO)
        gui_handler.setFormatter(
            logging.Formatter('%(asctime)s [%(levelname)s] %(message)s',
                              datefmt='%H:%M:%S'))
        root_logger.addHandler(gui_handler)
    
    def _set_status(self, text: str):
        self.status_label.configure(text=text)
    
    def _update_remaining(self, remaining: int):
        """更新 GUI 上的剩余次数（线程安全）。"""
        self.root.after(0, self._set_remaining_display, remaining)
    
    def _set_remaining_display(self, remaining: int):
        self.remaining_label.configure(text=f'→ 剩余 {remaining} 次')
        self.rounds_var.set(str(remaining))
        if remaining <= 5:
            self.remaining_label.configure(fg='#ff4444')
        elif remaining <= 15:
            self.remaining_label.configure(fg='#ffaa00')
        else:
            self.remaining_label.configure(fg='#ffd700')
    
    def _on_start(self):
        if self.running:
            return
        
        try:
            max_rounds = int(self.rounds_var.get())
        except ValueError:
            max_rounds = 999
        
        self.running = True
        self.start_btn.configure(state='disabled')
        self.stop_btn.configure(state='normal')
        self._set_status('加载模板...')
        self._set_remaining_display(max_rounds)
        
        def worker():
            try:
                logger.info('加载模板中...')
                self.templates = TemplateManager()
                logger.info('模板加载完成')
                
                # 权限不对等时点击会被 Windows 静默丢弃，直接拦下，避免白跑
                warn = detect_uipi_block()
                if warn:
                    logger.error(warn)
                    self.root.after(0, lambda: self._set_status('权限不足'))
                    self._on_finished()
                    return
                
                self._set_status('运行中')
                logger.info('3秒后开始自动占卜（鼠标移到左上角 / 点停止可取消）...')
                deadline = time.time() + 3.0
                while time.time() < deadline:
                    if check_stop():
                        logger.info('已取消启动')
                        self._on_finished()
                        return
                    time.sleep(0.1)
                
                run_automation(self.templates, max_rounds,
                               on_round_done=self._update_remaining,
                               on_finished=self._on_finished)
            except Exception as e:
                logger.error(f'启动失败: {e}', exc_info=True)
                self.root.after(0, self._on_finished)
        
        stop_event.clear()
        self._reset_failsafe()
        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()
    
    def _reset_failsafe(self):
        global _failsafe_reported
        _failsafe_reported = False
    
    def _on_stop(self):
        if not self.running:
            return
        logger.info('收到停止指令...')
        stop_event.set()
        self._set_status('正在停止...')
    
    def _on_finished(self):
        """自动化结束后更新 UI。"""
        self.running = False
        self.root.after(0, self._reset_buttons)
    
    def _reset_buttons(self):
        self.start_btn.configure(state='normal')
        self.stop_btn.configure(state='disabled')
        self._set_status('已停止')
    
    def _on_close(self):
        stop_event.set()
        self.root.destroy()
    
    def run(self):
        self.root.mainloop()


if __name__ == '__main__':
    # 直接运行 gui.py 时不请求管理员权限
    app = DivinationGUI()
    app.run()
