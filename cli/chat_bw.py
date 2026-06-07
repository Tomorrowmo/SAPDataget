"""SAP BW 智能取数 —— 命令行入口 (运维诊断 + 演示用)。

用法:
    python -m cli.chat_bw                       交互式问答
    python -m cli.chat_bw "上月华东大区销售前 10"  单次提问
    python -m cli.chat_bw --ping                测试 BW 连通(mock/live 共用)
    python -m cli.chat_bw --list-services       直接列出可用 OData 服务(不走 LLM)
    python -m cli.chat_bw --list-skills         列出可用 Skill
    python -m cli.chat_bw --run-skill <id> key=val key=val
                                                直接跑 Skill,不走 LLM(快速验证)
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Any

# Windows 控制台 UTF-8 修复
if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")              # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

from app.agent import Agent
from app.bw.factory import make_bw_client
from app.config import load_settings, Settings
from app.llm import LLMClient
from app.orchestrator import TaskOrchestrator
from app.skills.registry import SkillRegistry

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


# ============================== 启动期组件装配 ==============================


def _bootstrap() -> tuple[Settings, Agent, SkillRegistry]:
    settings = load_settings()
    errors = settings.validate()
    if errors:
        print("[配置错误]", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(2)

    bw = make_bw_client(settings)
    skills = SkillRegistry(settings.skills_dir)
    loaded = skills.reload()

    llm = LLMClient(settings.llm)
    orchestrator = TaskOrchestrator(settings, bw, skills)

    def _ask_user(question: str, options: list[str] | None) -> str:
        prompt = f"[助手追问] {question}"
        if options:
            prompt += f"  (可选: {', '.join(options)})"
        print(prompt, flush=True)
        return input("你> ").strip()

    def _on_event(kind: str, payload: dict[str, Any]) -> None:
        if kind == "tool_call":
            args_repr = json.dumps(payload["arguments"], ensure_ascii=False)
            if len(args_repr) > 160:
                args_repr = args_repr[:160] + "...}"
            print(f"  ⚙ {payload['name']}({args_repr})", flush=True)

    agent = Agent(
        settings=settings, llm=llm, bw=bw, skills=skills,
        orchestrator=orchestrator,
        ask_user_callback=_ask_user,
        on_event=_on_event,
    )
    print(f"[启动] {bw.describe()}")
    print(f"[启动] {llm.describe()}")
    print(f"[启动] Skills 已加载: {loaded} 个")
    if settings.bw.mode == "mock":
        print("[启动] ⚠️ MOCK 数据 —— 数字仅供演示,不可作为业务依据")
    return settings, agent, skills


# ============================== 子命令 ==============================


def cmd_ping(settings: Settings) -> int:
    """快速验证 BW 连通,不走 LLM。"""
    bw = make_bw_client(settings)
    print(f"[ping] {bw.describe()}")
    resp = bw.list_services(top=1)
    print(f"  status_code = {resp.status_code}")
    if resp.error:
        print(f"  error       = {resp.error}")
        return 1
    if resp.json:
        sample = json.dumps(resp.json, ensure_ascii=False)
        if len(sample) > 300:
            sample = sample[:300] + "..."
        print(f"  sample      = {sample}")
    return 0


def cmd_list_services(settings: Settings) -> int:
    bw = make_bw_client(settings)
    resp = bw.list_services(top=50)
    if resp.error:
        print(f"错误: {resp.error}", file=sys.stderr)
        return 1
    print(f"{'TechnicalServiceName':<22} {'Title':<25} Description")
    print("-" * 80)
    for s in (resp.json or {}).get("services", []):
        print(f"{(s.get('TechnicalServiceName') or '')[:22]:<22} "
              f"{(s.get('Title') or '')[:25]:<25} "
              f"{(s.get('Description') or '')[:40]}")
    return 0


def cmd_list_skills(settings: Settings) -> int:
    skills = SkillRegistry(settings.skills_dir)
    n = skills.reload()
    if n == 0:
        print(f"未在 {settings.skills_dir} 找到 Skill")
        return 0
    for s in skills.list():
        print(f"\n📊 {s.id}  (v{s.version})")
        print(f"   {s.title}")
        print(f"   {s.description}")
        if s.params:
            print("   参数:")
            for p in s.params:
                req = "必填" if p.required else f"可选, 默认 {p.default!r}"
                enum_part = f" 选项{p.enum}" if p.enum else ""
                print(f"     - {p.name} ({req}){enum_part}: {p.description}")
    return 0


def cmd_run_skill(settings: Settings, skill_id: str, params: dict[str, Any]) -> int:
    """直接跑 Skill,不经 LLM,验证 Mock + Skills + Excel 链路。"""
    bw = make_bw_client(settings)
    skills = SkillRegistry(settings.skills_dir)
    skills.reload()
    orch = TaskOrchestrator(settings, bw, skills)
    print(f"[run-skill] {skill_id}  params={params}")
    result = orch.run_skill(skill_id, params, username="cli_user")
    if result.status != "done":
        print(f"❌ 失败: {result.error}", file=sys.stderr)
        return 1
    print(f"✅ 完成: {result.row_count} 行")
    if result.excel:
        print(f"   文件: {result.excel.path}")
        print(f"   大小: {result.excel.size_bytes:,} bytes")
    print("   预览前 5 行:")
    for row in result.rows_preview[:5]:
        print(f"     {row}")
    return 0


def cmd_chat(settings: Settings, message: str, agent: Agent) -> int:
    print(f"\n你> {message}\n")
    result = agent.run(message)
    print("\n助手>")
    print(result.final_text)
    if result.task and result.task.excel:
        print(f"\n📎 Excel: {result.task.excel.path}")
        print(f"   {result.task.row_count} 行, {result.task.excel.size_bytes:,} bytes")
    print(f"\n[stats] iterations={result.iterations} "
          f"in_tokens={result.total_input_tokens} out_tokens={result.total_output_tokens}")
    return 0


def cmd_interactive(settings: Settings, agent: Agent) -> int:
    print("\n" + "=" * 60)
    print(" SAP BW 智能取数助手  (Ctrl+C 退出)")
    print(" 示例:")
    print('   - "列出所有销售相关的服务"')
    print('   - "查询 2026 年 5 月华东大区销售前 10 办事处"')
    print('   - "上月各工厂良率"')
    print("=" * 60)
    while True:
        try:
            line = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line.lower() in (":exit", ":quit", "exit", "quit"):
            return 0
        try:
            cmd_chat(settings, line, agent)
        except Exception as e:                              # noqa: BLE001
            print(f"[运行时错误] {e}", file=sys.stderr)


# ============================== main ==============================


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:]) if argv is None else argv

    # 不走 LLM 的子命令,先处理
    if argv and argv[0] in ("--ping", "-p"):
        return cmd_ping(load_settings())
    if argv and argv[0] in ("--list-services",):
        return cmd_list_services(load_settings())
    if argv and argv[0] in ("--list-skills",):
        return cmd_list_skills(load_settings())
    if argv and argv[0] in ("--run-skill",):
        if len(argv) < 2:
            print("用法: --run-skill <skill_id> key=value key=value ...", file=sys.stderr)
            return 2
        skill_id = argv[1]
        params: dict[str, Any] = {}
        for kv in argv[2:]:
            if "=" not in kv:
                print(f"参数应为 key=value,收到 {kv!r}", file=sys.stderr)
                return 2
            k, v = kv.split("=", 1)
            params[k] = v
        return cmd_run_skill(load_settings(), skill_id, params)

    # LLM 路径
    settings, agent, _skills = _bootstrap()
    if argv:
        return cmd_chat(settings, " ".join(argv), agent)
    return cmd_interactive(settings, agent)


if __name__ == "__main__":
    raise SystemExit(main())
