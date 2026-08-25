"""应用配置包的稳定导入入口。

业务模块从这里读取设置与运行目录；日志实现放在子模块中，避免初始化配置时
隐式创建 handler 或日志文件。
"""

from .settings import DATA_DIR, LOG_DIR, ROOT_DIR, WORLDS_DIR, Settings, settings

__all__ = ["DATA_DIR", "LOG_DIR", "ROOT_DIR", "WORLDS_DIR", "Settings", "settings"]
