import sys
from agent.agent import PersonalAgent


def main():
    # Windows 下强制 UTF-8 输出，避免 GBK 编码报错
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    agent = PersonalAgent()
    agent.run()


if __name__ == "__main__":
    main()
