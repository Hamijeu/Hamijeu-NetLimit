from pathlib import Path
import ctypes
import json
import os
from queue import Empty, SimpleQueue
import subprocess
import sys
from threading import Thread
import tkinter as tk
from tkinter import messagebox, ttk


APP_NAME = "Hamijeu NetLimit V1.1"
APP_ICON = Path(__file__).with_name("app.ico")


def command(address, network, laptop, hotspot, ipv6_addresses=(), low_latency=False):
    args = [sys.executable, "-u", str(Path(__file__).with_name("limiter.py"))]
    if getattr(sys, "frozen", False):
        args = [sys.executable, "--worker"]
    if laptop:
        args += ["--upstream-ip", address, "--download", laptop[0], "--upload", laptop[1]]
        for ipv6 in ipv6_addresses:
            args += ["--upstream-ipv6", ipv6]
    if hotspot:
        args += ["--hotspot-upstream-ip", address]
        args += ["--hotspot-network", network, "--hotspot-download", hotspot[0], "--hotspot-upload", hotspot[1]]
    if low_latency:
        args.append("--low-latency")
    return args


def autostart(enabled=None, executable=None):
    action = "status" if enabled is None else "enable" if enabled else "disable"
    script = """
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$name = 'Hamijeu NetLimit - ' + $identity.User.Value
$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
switch ($env:NETLIMIT_AUTOSTART_ACTION) {
    'enable' {
        $exe = $env:NETLIMIT_AUTOSTART_EXE
        $launch = New-ScheduledTaskAction -Execute $exe -WorkingDirectory (Split-Path -LiteralPath $exe)
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity.Name
        $principal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Highest
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
        Register-ScheduledTask -TaskName $name -Action $launch -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
        'enabled'
    }
    'disable' {
        if ($existing) { Unregister-ScheduledTask -TaskName $name -Confirm:$false }
        'disabled'
    }
    'status' {
        if ($existing -and $existing.State -ne 'Disabled') { 'enabled' } else { 'disabled' }
    }
}
"""
    output = subprocess.check_output(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, "NETLIMIT_AUTOSTART_ACTION": action,
             "NETLIMIT_AUTOSTART_EXE": executable or sys.executable},
        stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW, timeout=15,
    )
    return output.strip() == b"enabled"


def set_taskbar_icon(root):
    from ctypes import wintypes

    user = ctypes.windll.user32
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    user.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user.LoadImageW.restype = wintypes.HANDLE
    user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.SendMessageW.restype = ctypes.c_ssize_t
    window = user.GetAncestor(root.winfo_id(), 2)
    for kind, width, height in ((0, 49, 50), (1, 11, 12)):
        icon = user.LoadImageW(None, str(APP_ICON), 1,
                               user.GetSystemMetrics(width), user.GetSystemMetrics(height), 0x10)
        if window and icon:
            user.SendMessageW(window, 0x0080, kind, icon)


def acquire_instance(name=r"Local\HamijeuNetLimit"):
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    ctypes.set_last_error(0)
    handle = kernel.CreateMutexW(None, False, name)
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
    if error == 183:
        kernel.CloseHandle(ctypes.c_void_p(handle))
        return None
    return handle


def main():
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Hamijeu.NetLimit")
    instance_handle = acquire_instance()
    if instance_handle is None:
        ctypes.windll.user32.MessageBoxW(
            None, "Hamijeu NetLimit is already running. Open it from the system tray.",
            APP_NAME, 0x40)
        return
    root = tk.Tk()
    root.title(APP_NAME)
    root.iconbitmap(default=str(APP_ICON))

    root.resizable(False, False)
    frame = ttk.Frame(root, padding=16)
    frame.grid(sticky="nsew")
    addresses = {}
    ipv6_by_adapter = {}
    adapter = tk.StringVar()
    network = tk.StringVar(value="192.168.137.0/24")
    laptop = tk.BooleanVar(value=True)
    hotspot = tk.BooleanVar()
    rates = [tk.StringVar(value="5") for _ in range(4)]
    profile = tk.StringVar(value="Normal")
    status = tk.StringVar(value="Ready")
    process = None

    ttk.Label(frame, text="Internet adapter (Wi-Fi, Ethernet or USB)").grid(row=0, columnspan=3, sticky="w")
    selector = ttk.Combobox(frame, textvariable=adapter, state="readonly", width=38)
    selector.grid(row=1, columnspan=2, sticky="ew")
    ttk.Label(frame, text="Windows hotspot IPv4 subnet").grid(row=2, columnspan=2, sticky="w", pady=(10, 0))
    ttk.Entry(frame, textvariable=network).grid(row=3, columnspan=2, sticky="ew")
    ttk.Label(frame, text="Download (Mbps)").grid(row=4, column=1, pady=(10, 0))
    ttk.Label(frame, text="Upload (Mbps)").grid(row=4, column=2, pady=(10, 0))
    for row, label, enabled, offset in [(5, "Laptop", laptop, 0), (6, "Hotspot clients", hotspot, 2)]:
        ttk.Checkbutton(frame, text=label, variable=enabled).grid(row=row, column=0, sticky="w")
        for column in (1, 2):
            ttk.Entry(frame, width=12, textvariable=rates[offset + column - 1]).grid(row=row, column=column, padx=4)

    def refresh():
        try:
            output = subprocess.check_output(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                 "$route = @(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue).InterfaceIndex; "
                 "Get-NetIPAddress | Where-Object { $_.InterfaceIndex -in $route } | "
                 "Select-Object InterfaceAlias,InterfaceIndex,IPAddress | ConvertTo-Json -Compress"],
                creationflags=subprocess.CREATE_NO_WINDOW, timeout=10,
            )
            rows = json.loads(output or b"[]")
            if isinstance(rows, dict):
                rows = [rows]
            addresses.clear()
            ipv6_by_adapter.clear()
            for row in rows:
                ip = row["IPAddress"]
                if ":" in ip or ip.startswith(("127.", "169.254.")):
                    continue
                label = f"{row['InterfaceAlias']} ({ip})"
                addresses[label] = ip
                ipv6_by_adapter[label] = [
                    peer["IPAddress"].split("%")[0] for peer in rows
                    if peer["InterfaceIndex"] == row["InterfaceIndex"] and ":" in peer["IPAddress"]
                ]
            selector["values"] = list(addresses)
            if adapter.get() not in addresses:
                adapter.set(next(iter(addresses), ""))
            return True
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            messagebox.showerror("Adapter refresh failed", str(exc))
            return False

    def collect_output(worker, messages):
        for line in worker.stdout:
            messages.put(line.strip())
        messages.put(None)

    def poll(messages, output):
        nonlocal process
        while True:
            try:
                line = messages.get_nowait()
            except Empty:
                root.after(200, poll, messages, output)
                return
            if line is None:
                break
            if line.startswith("STATUS "):
                status.set(line.removeprefix("STATUS "))
            elif line:
                output.append(line)
        code = process.wait()
        process.stdout.close()
        if process.stdin is not None:
            process.stdin.close()
        status.set("Stopped" if code == 0 else output[-1] if output else "Limiter exited unexpectedly.")
        process = None
        start_button.configure(state="normal")
        stop_button.configure(state="disabled")

    def start():
        nonlocal process
        if not laptop.get() and not hotspot.get():
            messagebox.showinfo("Select a mode", "Enable Laptop, Hotspot clients, or both.")
            return
        if (laptop.get() or hotspot.get()) and not refresh():
            return
        if adapter.get() not in addresses:
            messagebox.showinfo("Select an adapter", "Choose the adapter that supplies internet.")
            return
        if not ctypes.windll.shell32.IsUserAnAdmin():
            messagebox.showerror("Administrator required", "Open PowerShell as Administrator and run ui.py again.")
            return
        values = [rate.get() for rate in rates]
        args = command(addresses.get(adapter.get(), ""), network.get(),
                       values[:2] if laptop.get() else None, values[2:] if hotspot.get() else None,
                       ipv6_by_adapter.get(adapter.get(), ()), profile.get() == "Low latency")
        try:
            process = subprocess.Popen(
                args + ["--stop-on-input", "--verify-speed"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except OSError as exc:
            messagebox.showerror("Could not start limiter", str(exc))
            return
        status.set("Starting limiter and checking connection...")
        messages = SimpleQueue()
        Thread(target=collect_output, args=(process, messages), daemon=True).start()
        start_button.configure(state="disabled")
        stop_button.configure(state="normal")
        root.after(200, poll, messages, [])

    def stop():
        if process is not None and process.poll() is None and process.stdin is not None:
            try:
                process.stdin.close()
                process.stdin = None
            except OSError:
                process.terminate()

    tray = None
    tray_actions = SimpleQueue()

    def restore():
        tray.visible = False
        root.deiconify()
        root.lift()

    def minimize(event):
        nonlocal tray
        if event.widget is not root or root.state() != "iconic":
            return
        if tray is None:
            from PIL import Image
            import pystray

            with Image.open(APP_ICON) as source:
                image = source.ico.getimage((32, 32)).copy()
            tray = pystray.Icon("HamijeuNetLimit", image, APP_NAME,
                menu=pystray.Menu(
                    pystray.MenuItem("Open", lambda: tray_actions.put(restore), default=True),
                    pystray.MenuItem("Exit", lambda: tray_actions.put(close)),
                ))
            tray.run_detached()
        else:
            from PIL import Image
            with Image.open(APP_ICON) as source:
                tray.icon = source.ico.getimage((32, 32)).copy()
            tray.title = APP_NAME
            tray.visible = True
        root.withdraw()

    def handle_tray_actions():
        try:
            action = tray_actions.get_nowait()
        except Empty:
            root.after(200, handle_tray_actions)
        else:
            if action is close:
                close()
            else:
                action()
                root.after(200, handle_tray_actions)

    def close():
        if tray is not None:
            tray.stop()
        stop()
        if process is not None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        root.destroy()

    ttk.Button(frame, text="Refresh", command=refresh).grid(row=1, column=2)
    ttk.Label(frame, text="Download profile").grid(row=7, column=0, sticky="w", pady=(8, 0))
    ttk.Radiobutton(frame, text="Normal", variable=profile, value="Normal").grid(row=7, column=1, pady=(8, 0))
    ttk.Radiobutton(frame, text="Low latency", variable=profile, value="Low latency").grid(row=7, column=2, pady=(8, 0))
    start_button = ttk.Button(frame, text="Start", command=start)
    start_button.grid(row=8, column=0, pady=(12, 0), sticky="w")
    stop_button = ttk.Button(frame, text="Stop", command=stop, state="disabled")
    stop_button.grid(row=8, column=1, pady=(12, 0), sticky="w")
    ttk.Label(frame, textvariable=status, wraplength=380).grid(row=10, columnspan=3, sticky="w", pady=(8, 0))
    startup_label = tk.StringVar(value="Start with Windows")
    startup_enabled = False

    def update_autostart(change=False):
        nonlocal startup_enabled
        try:
            startup_enabled = autostart(not startup_enabled if change else None)
        except (OSError, subprocess.SubprocessError) as exc:
            detail = getattr(exc, "output", None)
            if change:
                messagebox.showerror("Start with Windows", detail.decode(errors="replace").strip() if detail else str(exc))
            return
        startup_label.set(f"Start with Windows: {'On' if startup_enabled else 'Off'}")

    if getattr(sys, "frozen", False):
        ttk.Button(frame, textvariable=startup_label, command=lambda: update_autostart(True)).grid(
            row=9, columnspan=3, sticky="w", pady=(8, 0))
        root.after(0, update_autostart)

    root.bind("<Unmap>", minimize)
    root.after(200, handle_tray_actions)
    root.protocol("WM_DELETE_WINDOW", close)
    refresh()
    root.update_idletasks()
    set_taskbar_icon(root)
    try:
        root.mainloop()
    finally:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(instance_handle))


if __name__ == "__main__":
    if sys.argv[1:2] == ["--worker"]:
        import msvcrt
        from limiter import main as run_worker

        # PyInstaller's windowed bootloader leaves Python's streams unset.
        get_handle = ctypes.windll.kernel32.GetStdHandle
        get_handle.restype = ctypes.c_void_p
        sys.stdin = os.fdopen(msvcrt.open_osfhandle(get_handle(-10), os.O_RDONLY), "r")
        sys.stdout = os.fdopen(msvcrt.open_osfhandle(get_handle(-11), os.O_WRONLY), "w", buffering=1)
        sys.stderr = sys.stdout
        del sys.argv[1]
        sys.exit(run_worker())
    elif getattr(sys, "frozen", False) and not ctypes.windll.shell32.IsUserAnAdmin():
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, None, None, 1)
    else:
        main()
