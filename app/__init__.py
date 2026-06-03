"""SAP BW 智能取数平台 —— 核心包。

模块布局（按 §6 总体架构）:
  app.config         应用配置 (Settings)
  app.bw             BW 数据层抽象 (Live / Mock 双实现)
  app.llm            LLM 抽象层 (LiteLLM)
  app.excel          Excel 生成
  app.skills         Skills 子系统
  app.orchestrator   任务编排（状态机）
  app.agent          LLM tool-use loop
"""

__version__ = "0.2.0"
