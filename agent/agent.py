import sys
import json
import yaml
from pathlib import Path
from dotenv import load_dotenv
from agent.llm import LLM
from agent.memory import Memory
from agent.planner import Planner
from agent.executor import Executor
from tools.shell import ShellTool
from tools.task_inbox import TaskInboxTool
from tools.obsidian import ObsidianTool


class PersonalAgent:
    """Agent 主控制器，负责任务对话循环。

    Phase 2：基础对话能力
    Phase 3：接入 Planner，/plan 命令拆解任务
    Phase 4：接入 Memory，自动携带用户画像与项目状态
    Phase 6：接入 Tools，shell / obsidian / task_inbox
    Phase 7：接入 Executor，/run 命令全自动执行任务
    """

    def __init__(self, config_dir: str = None, memory_dir: str = None):
        # 加载 .env 中的密钥
        load_dotenv()

        if config_dir is None:
            config_dir = Path(__file__).parent.parent / "config"
        else:
            config_dir = Path(config_dir)

        # 加载配置
        config_path = config_dir / "config.yaml"
        prompts_path = config_dir / "prompts.yaml"

        if not config_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {config_path}")
        if not prompts_path.exists():
            raise FileNotFoundError(f"Prompt 文件不存在: {prompts_path}")

        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        with open(prompts_path, "r", encoding="utf-8") as f:
            self.prompts = yaml.safe_load(f)

        # 初始化 Memory（Phase 4）
        self.memory = Memory(memory_dir)

        # 初始化 LLM
        self.llm = LLM(self.config)

        # 初始化工具注册表（Phase 3：供 Planner 了解可用工具）
        self.tools_registry = {
            "shell": ShellTool(),
            "obsidian": ObsidianTool(),
            "task_inbox": TaskInboxTool(),
        }

        # 初始化 Planner（Phase 3）
        self.planner = Planner(
            llm=self.llm,
            planner_prompt=self.prompts.get("planner_prompt", ""),
            tools_registry=self.tools_registry,
        )

        # 初始化 Executor（Phase 7）
        self.executor = Executor(
            tools=self.tools_registry,
            confirm_callback=self._confirm_cli,
            stop_on_failure=True,
        )

        # 初始化对话历史（含用户画像和项目状态）
        self.messages = [{"role": "system", "content": self._build_system_prompt()}]

    # ── 对话 ──────────────────────────────────────────────

    def chat(self, user_input: str) -> str:
        """单轮对话：将用户输入发送给 LLM，返回回复文本。"""
        self.messages.append({"role": "user", "content": user_input})
        try:
            response = self.llm.chat(self.messages)
            self.messages.append({"role": "assistant", "content": response})
            return response
        except Exception:
            self.messages.pop()
            raise

    def clear_history(self):
        """清空对话历史，保留 system prompt 和 memory 上下文。"""
        self.messages = [{"role": "system", "content": self._build_system_prompt()}]

    # ── 任务规划（Phase 3）────────────────────────────────

    def plan_task(self, task: str) -> dict:
        """将自然语言任务拆解为结构化步骤。

        自动携带 memory 上下文，让规划结果更贴合用户实际情况。
        """
        context = self.memory.get_context()
        return self.planner.plan(task, context=context)

    # ── Memory（Phase 4）───────────────────────────────────

    def refresh_memory(self):
        """重新从磁盘读取 memory 文件，刷新 system prompt 中的上下文。"""
        old_context = self._extract_memory_context()
        new_context = self.memory.get_context()

        if old_context != new_context:
            self.messages[0]["content"] = self._build_system_prompt()
            return True
        return False

    def show_context(self) -> str:
        """返回当前注入 LLM 的完整系统提示词（含记忆上下文）。"""
        return self.messages[0]["content"]

    # ── CLI 交互循环 ──────────────────────────────────────

    def run(self):
        """启动 CLI 交互循环。"""
        agent_name = self.config.get("agent", {}).get("name", "PersonalAgent")
        print(f"[OK] {agent_name} 启动成功！")
        print("命令: exit | clear | /memory | /refresh | /plan <任务> | /run <任务> | /run -y <任务>")
        print()

        while True:
            try:
                user_input = input("你: ").strip()

                if not user_input:
                    continue

                if user_input.lower() in ("exit", "quit"):
                    print("再见！")
                    break

                if user_input.lower() == "clear":
                    self.clear_history()
                    print("[CLEAR] 对话历史已清空。\n")
                    continue

                # Phase 4: 记忆相关指令
                if user_input == "/memory":
                    ctx = self.memory.get_context()
                    if ctx:
                        print(f"\n[记忆上下文]\n{ctx}\n")
                    else:
                        print("\n[记忆上下文] 暂无。请编辑 memory/profile.md 和 memory/projects.md。\n")
                    continue

                if user_input == "/refresh":
                    changed = self.refresh_memory()
                    if changed:
                        print("[REFRESH] 记忆已刷新。\n")
                    else:
                        print("[REFRESH] 记忆无变化。\n")
                    continue

                # Phase 3: 任务规划指令
                if user_input.startswith("/plan"):
                    task = user_input[len("/plan"):].strip()
                    if not task:
                        print("\n用法: /plan <任务描述>\n")
                        continue
                    self._handle_plan(task)
                    continue

                # Phase 7: 任务执行指令（规划 + 执行 + 记录）
                if user_input.startswith("/run"):
                    rest = user_input[len("/run"):].strip()
                    auto_confirm = False
                    if rest.startswith("-y "):
                        auto_confirm = True
                        rest = rest[3:].strip()
                    elif rest == "-y":
                        print("\n用法: /run -y <任务描述>\n")
                        continue
                    if not rest:
                        print("\n用法: /run <任务描述>  或  /run -y <任务描述>\n")
                        continue
                    self._handle_run(rest, auto_confirm=auto_confirm)
                    continue

                response = self.chat(user_input)
                print(f"\nAgent: {response}\n")

            except KeyboardInterrupt:
                print("\n\n再见！")
                break
            except Exception as e:
                print(f"\n[ERROR] {e}\n")

    # ── 内部方法 ──────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        """构建 system prompt：基础 prompt + 用户画像 + 项目状态。"""
        base = self.prompts.get("system_prompt", "你是一个有用的助手。")
        context = self.memory.get_context()

        if context:
            return (
                f"{base}\n\n"
                f"---\n\n"
                f"以下是你对用户的了解，请在对话中主动利用这些信息：\n\n"
                f"{context}"
            )
        return base

    def _extract_memory_context(self) -> str:
        """从当前 system prompt 中提取记忆上下文部分。"""
        content = self.messages[0]["content"]
        separator = "\n\n---\n\n以下是你对用户的了解"
        if separator in content:
            return content.split(separator, 1)[1]
        return ""

    def _handle_plan(self, task: str):
        """处理 /plan 命令：调用 Planner 并格式化输出。"""
        print(f"\n[规划中...] {task}\n")
        try:
            plan = self.plan_task(task)
            self._print_plan(plan)
        except ValueError as e:
            print(f"[规划失败] {e}\n")

    @staticmethod
    def _print_plan(plan: dict):
        """格式化打印规划结果。"""
        print(f"目标: {plan['goal']}")
        print("-" * 50)
        for step in plan["steps"]:
            tool = step.get("tool", "?")
            params = step.get("params", {})
            risk_icon = {"low": "○", "medium": "△", "high": "▲"}.get(step.get("risk", "low"), "?")
            print(f"  [{step['id']}] {risk_icon} {step['action']}")
            print(f"       tool: {tool}", end="")
            if params:
                print(f"  params: {json.dumps(params, ensure_ascii=False)}", end="")
            print()
        print("-" * 50)
        print(f"共 {len(plan['steps'])} 步\n")

    # ── 任务执行（Phase 7）────────────────────────────────

    # 执行失败后自动纠错重试的最大次数
    MAX_RETRIES = 3

    def _handle_run(self, task: str, auto_confirm: bool = False):
        """处理 /run 命令：规划 → 确认 → 执行 → 失败自动纠错重试 → 记录。

        执行失败时会将错误反馈给 Planner，自动修正后重试，最多 3 次。
        """
        # 1. 规划
        print(f"\n[规划中...] {task}\n")
        try:
            plan = self.plan_task(task)
        except ValueError as e:
            print(f"[规划失败] {e}\n")
            return

        self._print_plan(plan)

        # 2. 确认是否执行
        if not auto_confirm:
            answer = input("是否执行? [Y/n]: ").strip().lower()
            if answer and answer not in ("y", "yes"):
                print("[取消] 已取消执行。\n")
                return
        else:
            print("[自动确认模式] 跳过所有确认。")

        # 3. 执行 + 自动纠错重试
        exec_result = self._execute_with_retry(plan, task, auto_confirm)

        # 4. 展示最终结果
        self._print_execution_result(exec_result)

        # 5. 写入 Daily 日记
        self._record_execution(exec_result, auto_confirm)

    def _execute_with_retry(self, plan: dict, task: str, auto_confirm: bool) -> dict:
        """执行计划，失败时自动纠错重试。

        只对"真正的执行错误"重试（排除 block 和用户 skip）。
        """
        retry = 0
        exec_result = None

        while retry <= self.MAX_RETRIES:
            if retry > 0:
                print(f"\n[自动纠错 第 {retry}/{self.MAX_RETRIES} 次] 分析错误，重新规划...\n")
                try:
                    plan = self.planner.repair(task, self._collect_failures(exec_result))
                except ValueError:
                    print("[纠错失败] 无法生成修正方案，停止重试。\n")
                    break
                self._print_plan(plan)

            print(f"[执行中...]\n" if retry == 0 else "")
            exec_result = self.executor.execute_plan(plan, auto_confirm=auto_confirm)

            # 检查是否还有真正的失败（非 block/skip）
            real_failures = self._collect_failures(exec_result)

            if not real_failures:
                # 全部成功（或只有 block/skip，也算完成）
                break

            if retry >= self.MAX_RETRIES:
                print(f"[重试次数已用完 ({self.MAX_RETRIES})]\n")
                break

            retry += 1

        return exec_result

    @staticmethod
    def _collect_failures(exec_result: dict) -> list[dict]:
        """从执行结果中收集真正的失败步骤（排除 block 和 skip）。"""
        if exec_result is None:
            return []
        return [
            r for r in exec_result.get("results", [])
            if not r["success"] and r.get("verdict") not in ("block", "skip")
        ]

    def _confirm_cli(self, prompt: str) -> bool:
        """CLI 确认回调：供 Executor 在执行高风险命令前询问用户。"""
        print(f"\n  ⚠ 需要确认:\n  {prompt}")
        answer = input("  执行? [y/N]: ").strip().lower()
        return answer in ("y", "yes")

    @staticmethod
    def _print_execution_result(result: dict):
        """格式化打印执行结果。"""
        print("-" * 50)
        print(f"目标: {result['goal']}")
        print(f"结果: {result['success_count']}/{result['total']} 步成功"
              f" {'✅' if result['completed'] else '❌'}")
        print("-" * 50)
        for r in result["results"]:
            status_icon = "✅" if r["success"] else "❌"
            verdict_tag = {
                "allow": "",
                "block": " [BLOCKED]",
                "confirm": " [已确认]",
                "skip": " [跳过]",
            }.get(r["verdict"], "")
            print(f"  [{r['step_id']}] {status_icon} {r['action']}{verdict_tag}")
            if r["result"] and r["tool"] != "none":
                # 截断显示
                output = str(r["result"])[:120]
                print(f"       → {output}")
            if r["error"]:
                print(f"       ✗ {r['error'][:120]}")
        print("-" * 50)
        print()

    def _record_execution(self, exec_result: dict, auto_confirm: bool):
        """将执行结果记录到 Obsidian Daily 日记。"""
        try:
            obsidian = self.tools_registry.get("obsidian")
            if obsidian is None:
                return

            # 构建日记条目
            status = "✅ 全部成功" if exec_result["completed"] else f"❌ {exec_result['success_count']}/{exec_result['total']} 成功"
            lines = [
                f"## 任务执行记录",
                f"",
                f"**目标**: {exec_result['goal']}",
                f"**时间**: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}",
                f"**结果**: {status}",
                f"**模式**: {'自动确认' if auto_confirm else '交互确认'}",
                f"",
                f"| 步骤 | 工具 | 结果 |",
                f"|------|------|------|",
            ]
            for r in exec_result["results"]:
                icon = "✅" if r["success"] else "❌"
                lines.append(f"| {r['step_id']}. {r['action'][:40]} | {r['tool']} | {icon} |")

            obsidian.execute("daily", content="\n".join(lines))
        except Exception:
            pass  # 记录失败不影响主流程
