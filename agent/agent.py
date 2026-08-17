import sys
import json
import yaml
from pathlib import Path
from dotenv import load_dotenv
from agent.llm import LLM
from agent.memory import Memory
from agent.logger import get_logger

_log = get_logger(__name__)
from agent.planner import Planner
from agent.executor import Executor
from tools.shell import ShellTool
from tools.task_inbox import TaskInboxTool
from tools.obsidian import ObsidianTool
from tools.web_search import WebSearchTool
from tools.file import FileTool
from tools.git import GitTool
from tools.python_runner import PythonRunnerTool
from tools.memory_search import MemorySearchTool
from agent.rag import RAGStore


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

        # 初始化 RAG 长期记忆（Phase 11）：向量化 memory 与 obsidian
        project_root = Path(__file__).parent.parent
        self.rag = RAGStore(
            source_dirs=[
                str(project_root / "memory"),
                str(project_root / "obsidian"),
            ],
        )

        # 初始化工具注册表（Phase 3：供 Planner 了解可用工具）
        self.tools_registry = {
            "shell": ShellTool(),
            "file": FileTool(),
            "git": GitTool(repo=str(project_root)),
            "python_runner": PythonRunnerTool(),
            "obsidian": ObsidianTool(),
            "task_inbox": TaskInboxTool(),
            "web_search": WebSearchTool(),
            "memory_search": MemorySearchTool(rag_store=self.rag),
        }

        # 初始化 Planner（Phase 3）
        self.planner = Planner(
            llm=self.llm,
            planner_prompt=self.prompts.get("planner_prompt", ""),
            tools_registry=self.tools_registry,
            max_steps=self.config.get("agent", {}).get("max_steps", 10),
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
        print("命令: exit | clear | /memory | /refresh | /plan <任务> | /run <任务> | /run -y <任务> | /ask <问题>")
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

                # Phase 11: RAG 长期记忆问答
                if user_input.startswith("/ask"):
                    question = user_input[len("/ask"):].strip()
                    if not question:
                        print("\n用法: /ask <问题>\n")
                        continue
                    self._handle_ask(question)
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

    def _handle_ask(self, question: str):
        """处理 /ask 命令：RAG 检索记忆库 + LLM 生成答案。"""
        print(f"\n[检索记忆中...] {question}\n")
        try:
            # 增量索引（检测新文件/变更）
            stats = self.rag.index()
            if stats["indexed"] > 0:
                print(f"[索引更新] 新增/更新 {stats['indexed']} 个文件\n")
            answer = self.rag.answer(question, self.llm)
            print(f"{answer}\n")
        except Exception as e:
            print(f"[检索失败] {e}\n")

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

    # 自检回路最多轮数（防止无限循环）
    MAX_VERIFY_ROUNDS = 2

    def _handle_run(self, task: str, auto_confirm: bool = False):
        """处理 /run 命令：规划 → 确认 → 执行 → 自检 → 不够就补救。"""
        _log.info("TASK_START task=%s auto=%s", task[:100], auto_confirm)
        current_task = task
        all_results = []  # 累积所有轮次的执行结果
        final_answer = None

        for verify_round in range(self.MAX_VERIFY_ROUNDS + 1):
            is_retry = verify_round > 0
            is_last = verify_round >= self.MAX_VERIFY_ROUNDS

            # 1. 规划
            print(f"\n[规划中...] {current_task}\n")
            try:
                plan = self.plan_task(current_task)
            except ValueError as e:
                print(f"[规划失败] {e}\n")
                return

            self._print_plan(plan)

            # 2. 确认（仅第一轮需要）
            if not is_retry:
                if not auto_confirm:
                    answer = input("是否执行? [Y/n]: ").strip().lower()
                    if answer and answer not in ("y", "yes"):
                        print("[取消] 已取消执行。\n")
                        return
                else:
                    print("[自动确认模式] 跳过所有确认。")
            else:
                print("[自检触发] 上一轮结果不充分，自动补救...")

            # 3. 执行 + 纠错重试
            exec_result = self._execute_with_retry(plan, current_task, auto_confirm)
            self._print_execution_result(exec_result)
            all_results.append(exec_result)

            # 4. 没有成功步骤 → 不再继续
            if exec_result["success_count"] == 0:
                print("[无法继续] 所有步骤均失败。\n")
                return

            # 5. 综合 + 自检
            print(f"\n[自检中...]\n")
            verified = self._verify_and_synthesize(task, all_results, is_last=is_last)

            if verified.get("sufficient") or is_last:
                final_answer = verified.get("answer", "")
                _log.info("VERIFY sufficient=%s round=%s", verified.get("sufficient"), verify_round)
                print(f"{final_answer}\n")
                break
            else:
                gap = verified.get("gap_description", "")
                missing = verified.get("missing_action", "")
                _log.info("VERIFY insufficient round=%s gap=%s", verify_round, gap[:120])
                print(f"[自检: 不充分] {gap}")
                print(f"[自动补救] {missing}\n")
                current_task = missing

        # 6. 写入 Daily 日记（记录最后一轮）
        if all_results:
            self._record_execution(all_results[-1], auto_confirm)

    def _execute_with_retry(self, plan: dict, task: str, auto_confirm: bool) -> dict:
        """执行计划，失败时自动纠错重试。

        只对"真正的执行错误"重试（排除 block 和用户 skip）。
        """
        retry = 0
        exec_result = None

        while retry <= self.MAX_RETRIES:
            if retry > 0:
                print(f"\n[自动纠错 第 {retry}/{self.MAX_RETRIES} 次] 分析错误，重新规划...\n")
                _log.warning("RETRY attempt=%s/%s task=%s", retry, self.MAX_RETRIES, task[:80])
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

    def _verify_and_synthesize(self, task: str, all_results: list[dict],
                                is_last: bool) -> dict:
        """综合所有执行结果 + 自检：答案是否满足用户意图？

        合并多轮执行结果 → LLM 综合答案 → LLM 自检是否充分 →
        如果不充分且不是最后一轮，生成补救任务。

        Returns:
            {
                "sufficient": bool,       # 答案是否满足用户意图
                "answer": str,            # 综合后的自然语言回答
                "gap_description": str,   # 如果不充分，缺少什么
                "missing_action": str,    # 如果不充分，该做什么（可直接作为 Planner 输入）
            }
        """
        # 收集所有轮次、所有成功步骤的结果
        snippets = []
        for er in all_results:
            for r in er.get("results", []):
                if r["success"] and r.get("result") and r["tool"] != "none":
                    output = str(r["result"])[:1500]
                    snippets.append(f"[{r['tool']}] {r['action']}\n{output}")

        if not snippets:
            return {"sufficient": True, "answer": "（无执行结果）",
                    "gap_description": "", "missing_action": ""}

        all_output = "\n\n---\n\n".join(snippets)

        # Step A: 综合答案
        synthesize_prompt = (
            f"用户的问题是：{task}\n\n"
            f"以下是执行该任务得到的原始数据：\n\n{all_output}\n\n"
            f"请根据以上数据，用中文直接回答用户的问题。"
            f"不要说你看到了什么搜索结果——直接综合成答案。"
            f"如果数据不足以完全回答问题，诚实说明已知的部分和缺失的部分。"
        )

        answer = ""
        try:
            answer = self.llm.chat([
                {"role": "system", "content": "你是 Personal Agent。根据给定的执行结果，直接回答用户的问题。简洁、准确、用中文。"},
                {"role": "user", "content": synthesize_prompt},
            ])
        except Exception:
            return {"sufficient": True, "answer": "（综合回答生成失败）",
                    "gap_description": "", "missing_action": ""}

        # 最后一轮不再自检，直接输出
        if is_last:
            return {"sufficient": True, "answer": answer,
                    "gap_description": "", "missing_action": ""}

        # Step B: 自检 —— 答案是否充分？
        verify_prompt = (
            f"用户提出的任务是：\n{task}\n\n"
            f"我们为回答这个问题做了搜索/执行，得到了以下数据：\n{all_output}\n\n"
            f"基于这些数据，我们给出的回答是：\n{answer}\n\n"
            f"请判断：这个回答是否**充分、完整地**满足了用户的任务要求？\n\n"
            f"判断标准：\n"
            f"- 如果用户问'是谁'，回答应该包含身份、背景、关键事实，而不是只有搜索摘要\n"
            f"- 如果用户问'创建文件'，回答应该确认文件已创建、路径、内容\n"
            f"- 如果用户问'检查是否存在'，回答应该明确说明存在与否、在哪里\n"
            f"- 搜索结果只有标题和摘要而缺少详细内容 → 不充分\n"
            f"- 搜索结果摘要本身已经包含了问题的完整答案 → 充分\n\n"
            f"请**只输出**一个 JSON 对象：\n"
            f'{{"sufficient": true/false, '
            f'"gap": "如果不充分，一句话说明缺什么", '
            f'"action": "如果不充分，一句具体的任务描述（如：获取 URL X 的详细内容 或 搜索关键词 Y），供 Planner 直接使用"}}'
        )

        try:
            import json
            raw = self.llm.chat([
                {"role": "system", "content": "你是质量检查器。只输出 JSON，不要其他内容。"},
                {"role": "user", "content": verify_prompt},
            ])
            # 剥离可能的 markdown 代码块
            m = __import__('re').search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, __import__('re').DOTALL)
            if m:
                raw = m.group(1).strip()
            verdict = json.loads(raw)

            return {
                "sufficient": verdict.get("sufficient", True),
                "answer": answer,
                "gap_description": verdict.get("gap", ""),
                "missing_action": verdict.get("action", ""),
            }
        except Exception:
            # 自检失败 → 默认充分，不阻塞
            return {"sufficient": True, "answer": answer,
                    "gap_description": "", "missing_action": ""}

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
