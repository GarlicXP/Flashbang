import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import winsound
import ctypes
import os
import json
import atexit
import sys
import pythoncom
from pynput import keyboard, mouse
from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
import pystray
from PIL import Image, ImageDraw
import winreg

# ================= 资源路径处理 =================
def resource_path(relative_path):
    """获取资源绝对路径，兼容打包后"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

# ================= 默认配置（移除了音效文件字段） =================
DEFAULT_CONFIG = {
    "hotkey": "<ctrl>+<alt>+b",
    "hold_duration": 3.0,
    "recovery_duration": 3.0,
    "enabled": True,
    "autostart": False,
    "volume_fade_duration": 3.0,
}
CONFIG_FILE = os.path.join(os.path.expanduser("~"), "flashlight_config.json")
EMBEDDED_SOUND = "flashlight.wav"  # 固定音效文件名，需与脚本同目录（打包时嵌入）

# ================= 配置管理 =================
class ConfigManager:
    def __init__(self):
        self.config = DEFAULT_CONFIG.copy()
        self.load()

    def load(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                self.config.update(saved)
            except:
                pass

    def save(self):
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=4)

    def get(self, key):
        return self.config.get(key)

    def set(self, key, value):
        self.config[key] = value
        self.save()

# ================= 音频控制 =================
class AudioController:
    def __init__(self):
        self.sessions = []
        self.lock = threading.Lock()
        atexit.register(self.final_restore)

    def _run_with_com(self, func):
        pythoncom.CoInitialize()
        try:
            func()
        finally:
            pythoncom.CoUninitialize()

    def mute_other_apps(self):
        def _mute():
            self.sessions.clear()
            sessions = AudioUtilities.GetAllSessions()
            current_pid = os.getpid()
            for session in sessions:
                if session.ProcessId and session.ProcessId != current_pid:
                    try:
                        volume = session._ctl.QueryInterface(ISimpleAudioVolume)
                        original_vol = volume.GetMasterVolume()
                        volume.SetMute(1, None)
                        volume.SetMasterVolume(0.0, None)
                        self.sessions.append((volume, original_vol))
                    except:
                        pass
        threading.Thread(target=lambda: self._run_with_com(_mute), daemon=True).start()

    def restore_volume(self, ratio):
        def _restore():
            for volume, original_vol in self.sessions:
                try:
                    volume.SetMute(0, None)
                    volume.SetMasterVolume(original_vol * ratio, None)
                except:
                    pass
        threading.Thread(target=lambda: self._run_with_com(_restore), daemon=True).start()

    def final_restore(self):
        def _final():
            for volume, original_vol in self.sessions:
                try:
                    volume.SetMasterVolume(original_vol, None)
                    volume.SetMute(0, None)
                except:
                    pass
            self.sessions.clear()
            sessions = AudioUtilities.GetAllSessions()
            current_pid = os.getpid()
            for session in sessions:
                if session.ProcessId and session.ProcessId != current_pid:
                    try:
                        volume = session._ctl.QueryInterface(ISimpleAudioVolume)
                        volume.SetMute(0, None)
                        volume.SetMasterVolume(1.0, None)
                    except:
                        pass
        threading.Thread(target=lambda: self._run_with_com(_final), daemon=True).start()

# ================= 白屏效果 =================
class FlashEffect:
    def __init__(self, config_manager):
        ctypes.windll.user32.SetProcessDPIAware()
        self.cm = config_manager
        self.audio = AudioController()
        self.root = tk.Tk()
        self.root.withdraw()

        # 全屏窗口
        self.white_win = tk.Toplevel()
        self.white_win.overrideredirect(True)
        self.white_win.configure(bg='white')
        self.white_win.attributes('-topmost', True)
        self.white_win.attributes('-alpha', 0.0)

        self.screen_w = ctypes.windll.user32.GetSystemMetrics(0)
        self.screen_h = ctypes.windll.user32.GetSystemMetrics(1)
        self.white_win.geometry(f"{self.screen_w}x{self.screen_h}+0+0")

        self.white_win.update_idletasks()
        self.hwnd_white = ctypes.windll.user32.GetParent(self.white_win.winfo_id())
        self.taskbar_hwnd = ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None)

        self._make_click_through()
        self._keep_on_top()

        self.alpha = 0.0
        self.is_running = False
        self.hold_until = 0.0
        self.recovery_done = True
        self.lock = threading.Lock()
        self.generation = 0
        self.recover_thread = None
        self.cursor_hidden = False

        self.top_thread_running = True
        self.top_thread = threading.Thread(target=self._top_loop, daemon=True)
        self.top_thread.start()

        atexit.register(self._cleanup_on_exit)

        def excepthook(exc_type, exc_value, exc_traceback):
            self._cleanup_on_exit()
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
        sys.excepthook = excepthook

        self.update_animation()

    def _make_click_through(self):
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x80000
        WS_EX_TRANSPARENT = 0x20
        WS_EX_TOPMOST = 0x8
        style = ctypes.windll.user32.GetWindowLongW(self.hwnd_white, GWL_EXSTYLE)
        style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST
        ctypes.windll.user32.SetWindowLongW(self.hwnd_white, GWL_EXSTYLE, style)

    def _keep_on_top(self):
        if self.hwnd_white:
            GWL_EXSTYLE = -20
            WS_EX_TOPMOST = 0x8
            WS_EX_TRANSPARENT = 0x20
            style = ctypes.windll.user32.GetWindowLongW(self.hwnd_white, GWL_EXSTYLE)
            if not (style & WS_EX_TOPMOST):
                style |= WS_EX_TOPMOST
            if not (style & WS_EX_TRANSPARENT):
                style |= WS_EX_TRANSPARENT
            ctypes.windll.user32.SetWindowLongW(self.hwnd_white, GWL_EXSTYLE, style)

            HWND_TOPMOST = -1
            SWP_NOACTIVATE = 0x0010
            SWP_SHOWWINDOW = 0x0040
            SWP_NOREDRAW = 0x0008
            ctypes.windll.user32.SetWindowPos(
                self.hwnd_white, HWND_TOPMOST, 0, 0, self.screen_w, self.screen_h,
                SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_NOREDRAW
            )
            ctypes.windll.user32.BringWindowToTop(self.hwnd_white)

    def _hide_cursor(self):
        if not self.cursor_hidden:
            for _ in range(10):
                ctypes.windll.user32.ShowCursor(False)
            ctypes.windll.user32.SetCursor(None)
            self.cursor_hidden = True

    def _show_cursor(self):
        if self.cursor_hidden:
            arrow = ctypes.windll.user32.LoadCursorW(None, 32512)
            ctypes.windll.user32.SetCursor(arrow)
            for _ in range(10):
                ctypes.windll.user32.ShowCursor(True)
            self.cursor_hidden = False

    def _top_loop(self):
        THREAD_PRIORITY_HIGHEST = 31
        ctypes.windll.kernel32.SetThreadPriority(ctypes.windll.kernel32.GetCurrentThread(), THREAD_PRIORITY_HIGHEST)
        while self.top_thread_running:
            if self.is_running or self.alpha > 0:
                self._keep_on_top()
                self._make_click_through()
                if self.is_running and not self.cursor_hidden:
                    self._hide_cursor()
            time.sleep(0.001)

    def play_sound(self):
        """播放固定的音效文件，若文件不存在则使用系统提示音"""
        sound_path = resource_path(EMBEDDED_SOUND)
        if os.path.exists(sound_path):
            winsound.PlaySound(sound_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            winsound.PlaySound('SystemExclamation', winsound.SND_ALIAS | winsound.SND_ASYNC)

    def trigger(self):
        if not self.cm.get('enabled'):
            return

        # 重新获取屏幕尺寸（防止分辨率变化）
        self.screen_w = ctypes.windll.user32.GetSystemMetrics(0)
        self.screen_h = ctypes.windll.user32.GetSystemMetrics(1)
        self.white_win.geometry(f"{self.screen_w}x{self.screen_h}+0+0")

        threading.Thread(target=self.play_sound, daemon=True).start()

        def delayed_mute():
            time.sleep(0.3)
            self.audio.mute_other_apps()
        threading.Thread(target=delayed_mute, daemon=True).start()

        with self.lock:
            self.generation += 1
            current_gen = self.generation
            self.alpha = 1.0
            self.hold_until = time.monotonic() + self.cm.get('hold_duration')
            self.recovery_done = False
            self.is_running = True

        self._hide_cursor()
        self.root.after(0, self._update_alpha, 1.0)

        self.recover_thread = threading.Thread(target=self._recover, args=(current_gen,), daemon=True)
        self.recover_thread.start()

    def _recover(self, gen):
        time.sleep(self.cm.get('hold_duration'))
        if gen != self.generation:
            return

        steps = int(self.cm.get('recovery_duration') * 25)
        for i in range(steps):
            if gen != self.generation:
                return
            alpha = 1.0 - (i / steps)
            volume_ratio = 1.0 - alpha
            self.root.after(0, self._update_alpha, alpha)
            self.audio.restore_volume(volume_ratio)
            time.sleep(1 / 25)

        if gen == self.generation:
            self.root.after(0, self._update_alpha, 0.0)
            self.audio.final_restore()
            self._show_cursor()
            with self.lock:
                self.is_running = False
                self.recovery_done = True
                self.alpha = 0.0

    def _update_alpha(self, alpha):
        self.white_win.attributes('-alpha', alpha)

    def update_animation(self):
        with self.lock:
            if self.is_running or self.alpha > 0:
                self._keep_on_top()
        self.root.after(10, self.update_animation)

    def set_enabled(self, enabled):
        self.cm.set('enabled', enabled)

    def on_exit(self):
        self.top_thread_running = False
        self.audio.final_restore()
        self._show_cursor()
        if self.taskbar_hwnd:
            ctypes.windll.user32.ShowWindow(self.taskbar_hwnd, 1)
        self.root.quit()

    def _cleanup_on_exit(self):
        try:
            self._show_cursor()
            if self.taskbar_hwnd:
                ctypes.windll.user32.ShowWindow(self.taskbar_hwnd, 1)
        except:
            pass

# ================= 热键管理 =================
class HotkeyManager:
    def __init__(self, config_manager, callback):
        self.cm = config_manager
        self.callback = callback
        self.keyboard_listener = None
        self.mouse_listener = None
        self.current_hotkey = None
        self.start()

    def start(self):
        self.stop()
        hotkey = self.cm.get('hotkey')
        if hotkey.startswith('mouse_'):
            mouse_button = hotkey.split('_')[1]
            self.mouse_listener = mouse.Listener(on_click=self.on_mouse_click)
            self.mouse_listener.start()
            self.current_hotkey = hotkey
        else:
            try:
                self.keyboard_listener = keyboard.GlobalHotKeys({hotkey: self.callback})
                self.keyboard_listener.start()
                self.current_hotkey = hotkey
            except Exception as e:
                print(f"热键注册失败: {e}")
                self.current_hotkey = None

    def on_mouse_click(self, x, y, button, pressed):
        if not pressed:
            return
        mouse_map = {
            mouse.Button.left: 'mouse_left',
            mouse.Button.right: 'mouse_right',
            mouse.Button.middle: 'mouse_middle'
        }
        current = mouse_map.get(button, '')
        if current == self.current_hotkey:
            self.callback()

    def stop(self):
        if self.keyboard_listener:
            self.keyboard_listener.stop()
            self.keyboard_listener = None
        if self.mouse_listener:
            self.mouse_listener.stop()
            self.mouse_listener = None

    def update_hotkey(self, new_hotkey):
        self.cm.set('hotkey', new_hotkey)
        self.start()

# ================= 快捷键捕获窗口 =================
class HotkeyCaptureDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title('按下快捷键或点击鼠标')
        self.geometry('300x150')
        self.resizable(False, False)
        self.attributes('-topmost', True)
        self.protocol('WM_DELETE_WINDOW', self.cancel)

        self.label = tk.Label(self, text='请按下键盘组合键\n或点击鼠标左/中/右键\n\n按 ESC 取消', font=('Arial', 12))
        self.label.pack(expand=True, fill='both', padx=20, pady=20)

        self.result = None
        self.running = True
        self._modifier_keys = set()
        self.keyboard_listener = None
        self.mouse_listener = None

        self._start_listeners()
        self.after(100, self._check_result)

    def _start_listeners(self):
        def on_key_press(key):
            if not self.running:
                return False
            if key == keyboard.Key.esc:
                self.result = None
                self.running = False
                return False
            if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r,
                       keyboard.Key.alt_l, keyboard.Key.alt_r,
                       keyboard.Key.shift_l, keyboard.Key.shift_r):
                self._modifier_keys.add(key)
                return True
            parts = []
            if any(k in self._modifier_keys for k in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r)):
                parts.append('<ctrl>')
            if any(k in self._modifier_keys for k in (keyboard.Key.alt_l, keyboard.Key.alt_r)):
                parts.append('<alt>')
            if any(k in self._modifier_keys for k in (keyboard.Key.shift_l, keyboard.Key.shift_r)):
                parts.append('<shift>')
            key_str = self._key_to_string(key)
            if key_str:
                parts.append(key_str)
                self.result = '+'.join(parts) if len(parts) > 1 else parts[0]
                self.running = False
                return False
            return True

        def on_key_release(key):
            if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r,
                       keyboard.Key.alt_l, keyboard.Key.alt_r,
                       keyboard.Key.shift_l, keyboard.Key.shift_r):
                self._modifier_keys.discard(key)
            return True

        def on_click(x, y, button, pressed):
            if not self.running:
                return False
            if pressed:
                if button == mouse.Button.left:
                    self.result = 'mouse_left'
                elif button == mouse.Button.right:
                    self.result = 'mouse_right'
                elif button == mouse.Button.middle:
                    self.result = 'mouse_middle'
                if self.result:
                    self.running = False
                    return False
            return True

        self.keyboard_listener = keyboard.Listener(on_press=on_key_press, on_release=on_key_release)
        self.mouse_listener = mouse.Listener(on_click=on_click)
        self.keyboard_listener.start()
        self.mouse_listener.start()

    def _key_to_string(self, key):
        try:
            if hasattr(key, 'char') and key.char:
                return key.char
            elif key == keyboard.Key.space:
                return '<space>'
            elif key == keyboard.Key.enter:
                return '<enter>'
            elif key == keyboard.Key.tab:
                return '<tab>'
            elif key == keyboard.Key.backspace:
                return '<backspace>'
            elif key == keyboard.Key.esc:
                return '<esc>'
            elif key in (keyboard.Key.f1, keyboard.Key.f2, keyboard.Key.f3, keyboard.Key.f4,
                         keyboard.Key.f5, keyboard.Key.f6, keyboard.Key.f7, keyboard.Key.f8,
                         keyboard.Key.f9, keyboard.Key.f10, keyboard.Key.f11, keyboard.Key.f12):
                return '<' + key.name + '>'
            elif key == keyboard.Key.up:
                return '<up>'
            elif key == keyboard.Key.down:
                return '<down>'
            elif key == keyboard.Key.left:
                return '<left>'
            elif key == keyboard.Key.right:
                return '<right>'
            else:
                if key.name:
                    return '<' + key.name + '>'
                return None
        except:
            return None

    def _check_result(self):
        if not self.running:
            if self.keyboard_listener:
                self.keyboard_listener.stop()
            if self.mouse_listener:
                self.mouse_listener.stop()
            self.destroy()
            return
        self.after(100, self._check_result)

    def cancel(self):
        self.result = None
        self.running = False

# ================= 系统托盘（悬停提示为“闪光弹模拟”） =================
class TrayIcon:
    def __init__(self, app):
        self.app = app
        self.icon_image = self.create_image()
        self.tray = None
        self.start_tray()

    def create_image(self):
        img = Image.new('RGB', (64, 64), color='white')
        d = ImageDraw.Draw(img)
        d.rectangle([16, 16, 48, 48], fill='black')
        return img

    def start_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem('打开设置', self.open_settings),
            pystray.MenuItem('退出', self.quit_app)
        )
        self.tray = pystray.Icon('FlashLight', self.icon_image, '闪光弹模拟', menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def open_settings(self, icon=None, item=None):
        self.app.show_settings()

    def quit_app(self, icon=None, item=None):
        self.app.quit()

# ================= 主应用 =================
class FlashLightApp:
    def __init__(self):
        self.cm = ConfigManager()
        self.effect = FlashEffect(self.cm)
        self.hotkey_manager = HotkeyManager(self.cm, self.effect.trigger)
        self.tray = None
        self.settings_window = None

        if self.cm.get('autostart'):
            self.enable_autostart(True)
        else:
            self.enable_autostart(False)

        self.create_settings_window()
        self.settings_window.withdraw()

        self.tray = TrayIcon(self)

        self.effect.root.mainloop()

    def create_settings_window(self):
        win = tk.Toplevel(self.effect.root)
        win.title('闪光弹模拟 - 设置')
        win.geometry('420x300')  # 移除音效相关行，高度减小
        win.protocol('WM_DELETE_WINDOW', self.hide_settings)

        self.enabled_var = tk.BooleanVar(value=self.cm.get('enabled'))
        ttk.Checkbutton(win, text='启用效果', variable=self.enabled_var, command=self.toggle_enabled).pack(anchor='w', padx=10, pady=5)

        ttk.Label(win, text='当前快捷键:').pack(anchor='w', padx=10)
        self.hotkey_label = ttk.Label(win, text=self.cm.get('hotkey'), font=('Arial', 10, 'bold'))
        self.hotkey_label.pack(anchor='w', padx=10)
        ttk.Button(win, text='设置快捷键', command=self.capture_hotkey).pack(anchor='w', padx=10, pady=5)

        ttk.Label(win, text='白屏保持时间(秒):').pack(anchor='w', padx=10)
        self.hold_var = tk.DoubleVar(value=self.cm.get('hold_duration'))
        ttk.Spinbox(win, from_=0.5, to=10, increment=0.5, textvariable=self.hold_var, width=10).pack(anchor='w', padx=10)

        ttk.Label(win, text='恢复过渡时间(秒):').pack(anchor='w', padx=10)
        self.recovery_var = tk.DoubleVar(value=self.cm.get('recovery_duration'))
        ttk.Spinbox(win, from_=0.5, to=10, increment=0.5, textvariable=self.recovery_var, width=10).pack(anchor='w', padx=10)

        self.autostart_var = tk.BooleanVar(value=self.cm.get('autostart'))
        ttk.Checkbutton(win, text='开机自启', variable=self.autostart_var, command=self.toggle_autostart).pack(anchor='w', padx=10, pady=5)

        ttk.Button(win, text='保存设置', command=self.save_settings).pack(pady=10)

        self.settings_window = win

    def show_settings(self):
        self.settings_window.deiconify()
        self.settings_window.lift()
        self.settings_window.focus_force()

    def hide_settings(self):
        self.settings_window.withdraw()

    def toggle_enabled(self):
        self.cm.set('enabled', self.enabled_var.get())
        self.effect.set_enabled(self.enabled_var.get())

    def capture_hotkey(self):
        self.hotkey_manager.stop()
        original_enabled = self.cm.get('enabled')
        self.effect.set_enabled(False)
        dialog = HotkeyCaptureDialog(self.settings_window)
        self.settings_window.wait_window(dialog)
        new_hotkey = dialog.result
        if new_hotkey:
            self.hotkey_label.config(text=new_hotkey)
            self.cm.set('hotkey', new_hotkey)
            messagebox.showinfo('成功', f'快捷键已设置为: {new_hotkey}')
        self.effect.set_enabled(original_enabled)
        self.hotkey_manager.update_hotkey(self.cm.get('hotkey'))

    def save_settings(self):
        self.cm.set('hold_duration', float(self.hold_var.get()))
        self.cm.set('recovery_duration', float(self.recovery_var.get()))
        self.cm.set('volume_fade_duration', float(self.recovery_var.get()))
        self.cm.set('autostart', self.autostart_var.get())
        messagebox.showinfo('成功', '设置已保存')

    def toggle_autostart(self):
        self.enable_autostart(self.autostart_var.get())

    def enable_autostart(self, enable):
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "FlashLight"
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
            if enable:
                exe_path = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(sys.argv[0])
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, f'"{exe_path}"')
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception as e:
            print(f"开机自启设置失败: {e}")

    def quit(self):
        self.effect.on_exit()
        self.hotkey_manager.stop()
        if self.tray and self.tray.tray:
            self.tray.tray.stop()
        self.effect.root.quit()

# ================= 入口 =================
if __name__ == '__main__':
    app = FlashLightApp()