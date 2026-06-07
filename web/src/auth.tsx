// 全局身份 Context
import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { api, ApiError } from "./api";
import type { Identity, SystemStatus } from "./types";

interface AuthCtx {
  identity: Identity | null;
  loading: boolean;
  status: SystemStatus | null;
  refresh: () => Promise<void>;
  refreshStatus: () => Promise<void>;
  login: (u: string, p: string) => Promise<void>;
  logout: () => Promise<void>;
}

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    try {
      const me = await api.me();
      setIdentity(me);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setIdentity(null);
      } else {
        console.error(e);
      }
    }
  };

  const refreshStatus = async () => {
    try {
      setStatus(await api.status());
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    (async () => {
      setLoading(true);
      await Promise.allSettled([refresh(), refreshStatus()]);
      setLoading(false);
    })();
  }, []);

  const login = async (username: string, password: string) => {
    const id = await api.login(username, password);
    setIdentity(id);
    await refreshStatus();
  };

  const logout = async () => {
    await api.logout();
    setIdentity(null);
  };

  return (
    <Ctx.Provider value={{ identity, status, loading, refresh, refreshStatus, login, logout }}>
      {children}
    </Ctx.Provider>
  );
}

export function useAuth() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth must be inside <AuthProvider>");
  return v;
}
