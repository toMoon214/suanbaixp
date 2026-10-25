"""
调试脚本：截图并测试模板匹配（使用箭头定位 + 逐格扫描）。
"""

import logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')

import cv2
from capture import screenshot, save_image
from recognition import (
    TemplateManager, find_button, find_arrows,
    compute_grid_from_arrows, scan_all_cells
)

def main():
    print('正在截图（自动查找游戏窗口）...')
    screen = screenshot()
    
    ok = save_image('debug_screenshot.png', screen)
    print(f'截图已保存: debug_screenshot.png ({screen.shape[1]}x{screen.shape[0]})' if ok
          else '截图保存失败')
    
    print('\n加载模板...')
    templates = TemplateManager()
    
    # === 按钮 ===
    print('\n--- 按钮 ---')
    for name in ['start', 'confirm', 'left', 'choose']:
        pos = find_button(screen, templates, name)
        status = f'({pos[0]}, {pos[1]})' if pos else '未找到'
        print(f'  {name:10s}: {status}')
    
    # === 箭头 ===
    print('\n--- 箭头 ---')
    arrows = find_arrows(screen, templates, selected=False)
    
    arrow_names = {
        0: '↗ 副对角线', 1: '→ 第3行', 2: '→ 第2行', 3: '→ 第1行',
        4: '↘ 主对角线', 5: '↓ 第1列', 6: '↓ 第2列', 7: '↓ 第3列',
    }
    for idx in range(8):
        if idx in arrows:
            print(f'  l{idx} {arrow_names[idx]:12s}: ({arrows[idx][0]}, {arrows[idx][1]}) ✓')
        else:
            print(f'  l{idx} {arrow_names[idx]:12s}: 未找到 ✗')
    
    # === 验证箭头顺序 ===
    if all(i in arrows for i in [1, 2, 3]):
        ok = arrows[3][1] < arrows[2][1] < arrows[1][1]
        print(f'  行 y 顺序: l3={arrows[3][1]} < l2={arrows[2][1]} < l1={arrows[1][1]} → {"✓" if ok else "✗"}')
    if all(i in arrows for i in [5, 6, 7]):
        ok = arrows[5][0] < arrows[6][0] < arrows[7][0]
        print(f'  列 x 顺序: l5={arrows[5][0]} < l6={arrows[6][0]} < l7={arrows[7][0]} → {"✓" if ok else "✗"}')
    
    # === 网格坐标（从箭头计算）===
    print('\n--- 网格坐标 (从箭头计算) ---')
    grid = compute_grid_from_arrows(arrows)
    for pos in range(9):
        r, c = pos // 3, pos % 3
        if pos in grid:
            x, y = grid[pos]
            print(f'  格子 {pos} (行{r},列{c}): ({x}, {y})')
        else:
            print(f'  格子 {pos} (行{r},列{c}): 未确定 ✗')
    
    # === 逐格数字识别 ===
    print('\n--- 逐格数字识别 ---')
    revealed = scan_all_cells(screen, templates, grid)
    if revealed:
        for pos in sorted(revealed.keys()):
            r, c = pos // 3, pos % 3
            print(f'  格子 {pos} (行{r},列{c}) = 数字 {revealed[pos]} ✓')
        unrevealed = [p for p in range(9) if p not in revealed]
        if unrevealed:
            print(f'  未翻开: {unrevealed}')
    else:
        print('  未识别到任何数字')
    
    print(f'\n共识别到 {len(revealed)}/9 张牌')
    print('完成!')


if __name__ == '__main__':
    main()
