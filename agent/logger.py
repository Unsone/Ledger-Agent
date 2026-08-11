"""统一日志模块。所有模块通过 get_logger(__name__) 获取 logger。

日志写入 logs/ 目录，按天轮转，同时输出到控制台。
"""

import logging
import sys
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler


# 全局标记，防止重复初始化
_log_initialized = False


def setup_logging(log_dir: str = None, level: str = "INFO"):
    """初始化日志系统（main.py 启动时调用一次）。

    Args:
        log_dir: 日志目录，默认项目根目录下的 logs/
        level: 日志级别，DEBUG / INFO / WARNING / ERROR
    """
    global _log_initialized
    if _log_initialized:
        return

    if log_dir is None:
        log_dir = Path(__file__).parent.parent / "logs"

    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    # 根 logger 配置
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 格式：[2026-08-12 10:30:00] [INFO] [agent.agent] 任务开始
    fmt = logging.Formatter(
        "[%(asctime)s] [%(levelname)-5s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 文件处理器：按天轮转，保留 30 天
    file_handler = TimedRotatingFileHandler(
        filename=log_dir / "agent.log",
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # 控制台处理器：只输出 WARNING 及以上（避免干扰 CLI）
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(fmt)
    root.addHandler(console_handler)

    # 为当前项目设置 INFO 级别
    for prefix in ("agent", "tools", "config"):
        logging.getLogger(prefix).setLevel(logging.DEBUG)

    _log_initialized = True


def get_logger(name: str) -> logging.Logger:
    """获取指定模块的 logger。"""
    return logging.getLogger(name)
