"""Windows Job Object 保证受控 worker 的后代随宿主句柄关闭而终止。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes


class WindowsJob:
    """在发送 stdin 前绑定进程树；配置失败时拒绝命令执行。"""

    def __init__(self, pid: int):
        """创建带 KILL_ON_JOB_CLOSE 的进程作业。

        Args:
            pid：刚启动且尚未获得控制输入的 worker PID。
        """
        class BasicLimits(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                        ('flags', wintypes.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
                        ('active', wintypes.DWORD), ('affinity', ctypes.c_size_t),
                        ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('basic', BasicLimits), ('io', ctypes.c_uint64 * 6),
                        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]

        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        for name, args, result in [
            ('CreateJobObjectW', [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            ('OpenProcess', [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            ('SetInformationJobObject', [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            ('AssignProcessToJobObject', [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            ('CloseHandle', [wintypes.HANDLE], wintypes.BOOL),
        ]:
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = args, result
        self.handle = self.kernel.CreateJobObjectW(None, None)
        process = self.kernel.OpenProcess(0x0101, False, pid)
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000
        try:
            if not self.handle or not process or not self.kernel.SetInformationJobObject(
                self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits),
            ) or not self.kernel.AssignProcessToJobObject(self.handle, process):
                raise OSError('COMMAND_NOT_SUPPORTED')
        except OSError:
            self.close()
            raise
        finally:
            if process:
                self.kernel.CloseHandle(process)

    def close(self) -> None:
        """幂等关闭作业句柄并终止其进程树。"""
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
