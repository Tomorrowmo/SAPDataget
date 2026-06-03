// 主应用：路由 + AuthProvider + Layout
import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import Layout from "./components/Layout";

import Login from "./pages/Login";
import Home from "./pages/Home";
import SkillDetail from "./pages/SkillDetail";
import Chat from "./pages/Chat";
import Tasks from "./pages/Tasks";
import AdminSkills from "./pages/AdminSkills";
import AdminAudit from "./pages/AdminAudit";
import AdminSensitive from "./pages/AdminSensitive";
import MyLlmKeys from "./pages/MyLlmKeys";

function Protected({ children }: { children: JSX.Element }) {
  const { identity, loading } = useAuth();
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-zinc-500">
        加载中...
      </div>
    );
  }
  if (!identity) return <Navigate to="/login" replace />;
  return children;
}

function AdminOnly({ children }: { children: JSX.Element }) {
  const { identity } = useAuth();
  if (identity?.role !== "admin") {
    return (
      <div className="p-6">
        <div className="max-w-md mx-auto px-4 py-6 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
          ❌ 此页面仅管理员可访问。当前账号: {identity?.username}
        </div>
      </div>
    );
  }
  return children;
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/*"
          element={
            <Protected>
              <Layout>
                <Routes>
                  <Route path="/" element={<Home />} />
                  <Route path="/skills/:skill_id" element={<SkillDetail />} />
                  <Route path="/chat" element={<Chat />} />
                  <Route path="/tasks" element={<Tasks />} />
                  <Route path="/llm-keys" element={<MyLlmKeys />} />
                  <Route path="/admin/skills" element={<AdminOnly><AdminSkills /></AdminOnly>} />
                  <Route path="/admin/audit" element={<AdminOnly><AdminAudit /></AdminOnly>} />
                  <Route path="/admin/sensitive" element={<AdminOnly><AdminSensitive /></AdminOnly>} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              </Layout>
            </Protected>
          }
        />
      </Routes>
    </AuthProvider>
  );
}
