"""
300英雄 占卜小游戏自动化脚本
按 F5 开始，F6 停止
"""

import sys
import time
import logging
import threading

import pyautogui
import keyboard
import numpy as np

from config import (
    ARROW_TO_LINE, DELAY_AFTER_START, DELAY_AFTER_FLIP, DELAY_AFTER_CHOOSE,
    DELAY_AFTER_CONFIRM, DELAY_BETWEEN_ACTIONS, DELAY_SCAN_RETRY,
    HOTKEY_START, HOTKEY_STOP
)
from capture import (
    screenshot, get_window_offset, is_failsafe_triggered,
    send_click, ensure_game_foreground, detect_uipi_block,
)
from recognition import (
    TemplateManager, find_button, find_arrows, scan_all_cells,
    compute_grid_from_arrows
)
from strategy import (
    choose_best_arrow, choose_next_flip
)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('auto_divination.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# pyautogui 设置：点击实际走 Win32 API，FAILSAFE 不会生效，
# 紧急停止由下面的 check_stop() 轮询光标位置实现
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.1

# 全局控制标志
running = False
stop_event = threading.Event()

import ctypes

# Win32 mouse event constants
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


def check_stop() -> bool:
    """
    统一停止判定：用户按 F6，或鼠标移到屏幕左上角（紧急停止）。
    返回 True 表示应当中止当前动作。
    """
    global running
    if stop_event.is_set():
        return True
    if is_failsafe_triggered():
        logger.warning('检测到鼠标位于屏幕左上角 → 紧急停止')
        stop_event.set()
        running = False
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
    """等待条件满足，超时返回 False。"""
    start = time.time()
    while time.time() - start < timeout:
        if check_stop():
            return False
        if condition_fn():
            return True
        time.sleep(interval)
    logger.warning(f'等待超时: {desc}')
    return False


def take_screenshot_arr() -> np.ndarray:
    """截取游戏窗口（BGR numpy 数组）。"""
    return screenshot()


def run_single_divination(templates: TemplateManager) -> bool:
    """
    执行一次完整的占卜流程。
    返回 True 表示成功，False 表示需要停止。
    """
    if check_stop():
        return False
    
    # 确保游戏在前台，否则点击会落到别的窗口上
    ensure_game_foreground()
    
    # ====== 步骤 1: 点击"开始占卜" ======
    logger.info('=' * 50)
    logger.info('步骤 1: 寻找并点击"开始占卜"按钮')
    
    screen = take_screenshot_arr()
    start_pos = find_button(screen, templates, 'start')
    if not start_pos:
        logger.error('未找到"开始占卜"按钮')
        return False
    
    logger.info(f'找到"开始占卜"按钮: {start_pos}')
    safe_click(*start_pos, delay=DELAY_AFTER_START)
    
    # ====== 步骤 2: 定位箭头，计算网格坐标 ======
    logger.info('步骤 2: 定位箭头，计算网格坐标...')
    
    screen = take_screenshot_arr()
    arrows = find_arrows(screen, templates, selected=False)
    
    if len(arrows) < 6:
        logger.warning(f'只找到 {len(arrows)} 个箭头，等待重试...')
        time.sleep(1.0)
        screen = take_screenshot_arr()
        arrows = find_arrows(screen, templates, selected=False)
    
    grid_positions = compute_grid_from_arrows(arrows)
    if len(grid_positions) < 9:
        logger.error(f'只能确定 {len(grid_positions)}/9 个格子坐标')
        return False
    
    logger.info(f'网格坐标:')
    for pos in range(9):
        r, c = pos // 3, pos % 3
        x, y = grid_positions[pos]
        logger.info(f'  pos={pos} (行{r},列{c}): ({x}, {y})')
    
    # ====== 步骤 3: 等待系统自动翻牌 ======
    logger.info('步骤 3: 等待系统自动翻牌...')
    
    revealed = {}
    
    def detect_auto_reveal():
        nonlocal revealed
        screen = take_screenshot_arr()
        revealed = scan_all_cells(screen, templates, grid_positions)
        return len(revealed) >= 1
    
    if not wait_for(detect_auto_reveal, timeout=8.0, interval=DELAY_SCAN_RETRY,
                    desc='系统翻牌'):
        logger.error('未检测到系统自动翻牌')
        return False
    
    logger.info(f'系统翻牌: {revealed}')
    
    # ====== 步骤 4: 自适应翻牌（翻1张→验证→计算下一张，重复3次）======
    logger.info('步骤 4: 自适应翻牌...')
    
    for flip_num in range(1, 4):
        if check_stop():
            return False
        
        next_pos = choose_next_flip(revealed)
        click_x, click_y = grid_positions[next_pos]
        logger.info(f'翻开第 {flip_num}/3 张: pos={next_pos} at ({click_x}, {click_y})')
        
        expected_count = len(revealed) + 1
        
        # 重试翻牌直到确认成功
        for attempt in range(3):
            safe_click(click_x, click_y, delay=DELAY_AFTER_FLIP)
            time.sleep(0.3)
            
            screen = take_screenshot_arr()
            revealed = scan_all_cells(screen, templates, grid_positions)
            
            if len(revealed) >= expected_count:
                logger.info(f'  翻牌成功: {revealed} ({len(revealed)} 张)')
                break
            else:
                logger.warning(f'  翻牌未生效 (第{attempt+1}次), 重试...')
                time.sleep(0.5)
        else:
            logger.warning(f'  翻牌重试3次仍未成功，继续...')
    
    # 步骤 5: 最终扫描确认
    logger.info('步骤 5: 最终扫描确认...')
    
    def scan_all_cards():
        nonlocal revealed
        screen = take_screenshot_arr()
        revealed = scan_all_cells(screen, templates, grid_positions)
        return len(revealed) >= 4
    
    if not wait_for(scan_all_cards, timeout=5.0, interval=DELAY_SCAN_RETRY,
                    desc='扫描4张牌'):
        logger.warning(f'只识别到 {len(revealed)} 张牌，使用已有信息继续')
    
    logger.info(f'已翻开的牌: {revealed}')
    
    # ====== 步骤 6: 选择最佳线 ======
    logger.info('步骤 6: 计算期望值并选择最佳线...')
    
    best_arrow, best_ev = choose_best_arrow(revealed)
    logger.info(f'选择箭头 l{best_arrow}, 期望值={best_ev:.1f}')
    
    # ====== 步骤 7: 点击箭头（带重试）======
    logger.info('步骤 7: 点击箭头...')
    
    # 先等待"请选择结果"出现
    def wait_for_choose_prompt():
        screen = take_screenshot_arr()
        return find_button(screen, templates, 'choose') is not None
    
    wait_for(wait_for_choose_prompt, timeout=5.0, interval=0.5,
             desc='选择界面出现')
    
    # 点击箭头，重试直到"确认选择"按钮出现
    for attempt in range(3):
        screen = take_screenshot_arr()
        arrows = find_arrows(screen, templates, selected=False)
        
        target_arrow = best_arrow if best_arrow in arrows else (list(arrows.keys())[0] if arrows else None)
        if target_arrow is None:
            logger.error('未找到任何箭头')
            return False
        
        arrow_pos = arrows[target_arrow]
        logger.info(f'点击箭头 l{target_arrow} at {arrow_pos} (第{attempt+1}次)')
        safe_click(*arrow_pos, delay=DELAY_AFTER_CHOOSE)
        
        # 验证：确认按钮是否出现
        time.sleep(0.3)
        screen = take_screenshot_arr()
        if find_button(screen, templates, 'confirm') is not None:
            break
        else:
            logger.warning(f'点击箭头后未出现确认按钮，重试...')
            time.sleep(0.5)
    
    # ====== 步骤 8: 点击"确认选择"（带重试）======
    logger.info('步骤 8: 点击"确认选择"按钮...')
    
    for attempt in range(3):
        screen = take_screenshot_arr()
        confirm_pos = find_button(screen, templates, 'confirm')
        
        if confirm_pos:
            logger.info(f'点击"确认选择": {confirm_pos} (第{attempt+1}次)')
            safe_click(*confirm_pos, delay=DELAY_AFTER_CONFIRM)
            
            # 验证：确认按钮应该消失
            time.sleep(0.5)
            screen = take_screenshot_arr()
            if find_button(screen, templates, 'confirm') is None:
                break
            else:
                logger.warning('确认按钮仍在，重试...')
        else:
            logger.warning(f'未找到确认按钮 (第{attempt+1}次)')
            time.sleep(1.0)
    
    logger.info('本轮占卜完成!')
    return True


def run_automation(templates: TemplateManager, max_rounds: int = 999):
    """主循环：重复执行占卜直到次数用完或用户停止。"""
    global running
    running = True
    stop_event.clear()
    
    logger.info('=' * 60)
    logger.info('自动占卜开始!')
    logger.info(f'按 {HOTKEY_STOP} 停止')
    logger.info('=' * 60)
    
    round_count = 0
    
    while round_count < max_rounds:
        if check_stop():
            break
        round_count += 1
        logger.info(f'\n===== 第 {round_count} 轮 =====')
        
        try:
            success = run_single_divination(templates)
            if not success:
                logger.info('本轮未成功，等待后重试...')
                time.sleep(2.0)
                
                # 检查是否还有"开始占卜"按钮
                screen = take_screenshot_arr()
                if not find_button(screen, templates, 'start'):
                    logger.info('未找到"开始占卜"按钮，占卜可能已完成')
                    break
        except Exception as e:
            logger.error(f'执行出错: {e}', exc_info=True)
            time.sleep(2.0)
    
    running = False
    logger.info(f'\n自动占卜结束! 共执行 {round_count} 轮')


def on_start():
    """F5 按下时启动。"""
    global running
    if running:
        logger.info('已经在运行中')
        return
    
    logger.info('加载模板中...')
    try:
        templates = TemplateManager()
    except Exception as e:
        logger.error(f'模板加载失败: {e}')
        return
    
    warn = detect_uipi_block()
    if warn:
        logger.error(warn)
        return
    
    logger.info('模板加载完成，3秒后开始...')
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if check_stop():
            logger.info('已取消启动')
            return
        time.sleep(0.1)
    
    thread = threading.Thread(target=run_automation, args=(templates,), daemon=True)
    thread.start()


def on_stop():
    """F6 按下时停止。"""
    global running
    if not running:
        return
    logger.info('收到停止指令，正在停止...')
    stop_event.set()
    running = False


def main():
    print('=' * 60)
    print('  300英雄 占卜自动化脚本')
    print('=' * 60)
    print(f'  按 {HOTKEY_START} 开始自动占卜')
    print(f'  按 {HOTKEY_STOP} 停止')
    print(f'  鼠标移到屏幕左上角可紧急停止')
    print('=' * 60)
    print('等待指令...')
    
    keyboard.add_hotkey(HOTKEY_START, on_start)
    keyboard.add_hotkey(HOTKEY_STOP, on_stop)
    
    try:
        keyboard.wait('esc')  # 按 ESC 退出程序
    except KeyboardInterrupt:
        pass
    
    stop_event.set()
    print('程序退出')


if __name__ == '__main__':
    main()
