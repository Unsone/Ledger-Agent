import sys
from agent.agent import PersonalAgent
from agent.logger import setup_logging, get_logger


def main():
    # Windows 下强制 UTF-8 输出，避免 GBK 编码报错
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # 初始化日志系统
    setup_logging()

    log = get_logger(__name__)
    log.info("PersonalAgent 启动")

    try:
        agent = PersonalAgent()
        agent.run()
    except Exception:
        log.exception("Agent 异常退出")
        raise


if __name__ == "__main__":
    main()
