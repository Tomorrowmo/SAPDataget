// 顶栏 LLM 状态指示器 —— 显示当前生效模型 + 就绪状态,点击去「🔑 LLM 设置」。
// (模型注册表/全局切换已移除,改为 DataAgent 式每用户 key+base_url+model 设置)
import { Link } from "react-router-dom";
import { useAuth } from "../auth";

export default function ModelSelector() {
  const { status } = useAuth();
  const llm = status?.llm;
  const ready = llm?.current_ready ?? false;
  const model = llm?.current || "未配置";

  return (
    <Link
      to="/llm-keys"
      className="flex items-center gap-2 px-3 py-1.5 rounded-md border border-zinc-200 bg-white hover:border-brand-500 hover:bg-brand-50 transition text-sm no-underline"
      title="LLM 设置（API key / Base URL / Model）"
    >
      <span className="text-zinc-400">🤖</span>
      <span className="font-medium text-zinc-800 max-w-[200px] truncate">{model}</span>
      {ready ? (
        <span className="text-emerald-600 text-xs">✓ 就绪</span>
      ) : (
        <span className="text-amber-600 text-xs">⚠ 未配置</span>
      )}
    </Link>
  );
}
