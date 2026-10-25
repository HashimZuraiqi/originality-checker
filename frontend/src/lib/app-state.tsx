import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, ApiError, setCsrf, type Me, type Preferences } from "./api";

// ---------------------------------------------------------------- auth
type AuthState = {
  me: Me | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (name: string, email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  setPreferences: (p: Preferences) => void;
  refresh: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const accept = (m: Me | null) => {
    setMe(m);
    setCsrf(m?.csrf_token ?? "");
  };

  const refresh = useCallback(async () => {
    try {
      const s = await api.get<({ authenticated: true } & Me) | { authenticated: false }>("/api/auth/session");
      accept(s.authenticated ? s : null);
    } catch (e) {
      if (!(e instanceof ApiError) || e.status !== 401) console.warn(e);
      accept(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const value = useMemo<AuthState>(
    () => ({
      me,
      loading,
      refresh,
      signIn: async (email, password) => accept(await api.post<Me>("/api/auth/login", { email, password })),
      signUp: async (name, email, password) => accept(await api.post<Me>("/api/auth/register", { name, email, password })),
      signOut: async () => {
        try {
          await api.post("/api/auth/logout");
        } finally {
          accept(null);
        }
      },
      setPreferences: (p) => setMe((m) => (m ? { ...m, preferences: p } : m)),
    }),
    [me, loading, refresh],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}

// --------------------------------------------------------------- theme
type Theme = "light" | "dark" | "system";
const ThemeContext = createContext<{ theme: Theme; setTheme: (t: Theme) => void; dark: boolean } | null>(null);

function readTheme(): Theme {
  try {
    const t = localStorage.getItem("oc-theme");
    if (t === "light" || t === "dark" || t === "system") return t;
  } catch {
    /* storage unavailable */
  }
  return "system";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(readTheme);
  const [systemDark, setSystemDark] = useState(() => window.matchMedia("(prefers-color-scheme: dark)").matches);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const on = (e: MediaQueryListEvent) => setSystemDark(e.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);

  const dark = theme === "dark" || (theme === "system" && systemDark);
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    document.documentElement.style.colorScheme = dark ? "dark" : "light";
  }, [dark]);

  const setTheme = (t: Theme) => {
    setThemeState(t);
    try {
      localStorage.setItem("oc-theme", t);
    } catch {
      /* ignore */
    }
  };
  return <ThemeContext.Provider value={{ theme, setTheme, dark }}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme outside ThemeProvider");
  return ctx;
}

// -------------------------------------------------------------- toasts
type Toast = { id: number; message: string; tone: "info" | "success" | "error" };
const ToastContext = createContext<((message: string, tone?: Toast["tone"]) => void) | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((message: string, tone: Toast["tone"] = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, message, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tone === "error" ? 7000 : 3500);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div aria-live="polite" className="pointer-events-none fixed inset-x-0 bottom-4 z-[60] flex flex-col items-center gap-2 px-4">
        {toasts.map((t) => (
          <div
            key={t.id}
            role="status"
            className={`pointer-events-auto max-w-md animate-fade-in rounded-xl px-4 py-3 text-sm font-medium shadow-lift ${
              t.tone === "error"
                ? "bg-red-600 text-white"
                : t.tone === "success"
                  ? "bg-emerald-600 text-white"
                  : "bg-slate-900 text-white dark:bg-white dark:text-slate-900"
            }`}
          >
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast outside ToastProvider");
  return ctx;
}

// A file chosen before signing in, uploaded right after authentication.
type PendingUpload = { file?: File; text?: string } | null;
const PendingContext = createContext<{ pending: PendingUpload; setPending: (p: PendingUpload) => void } | null>(null);

export function PendingUploadProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<PendingUpload>(null);
  return <PendingContext.Provider value={{ pending, setPending }}>{children}</PendingContext.Provider>;
}

export function usePendingUpload() {
  const ctx = useContext(PendingContext);
  if (!ctx) throw new Error("usePendingUpload outside provider");
  return ctx;
}
