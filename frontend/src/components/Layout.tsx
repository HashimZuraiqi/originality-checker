import { BookOpen, FolderKanban, History, LogOut, Menu, Monitor, Moon, PenLine, Plus, Settings, ShieldCheck, Sun, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { useAuth, useTheme } from "../lib/app-state";
import { cx } from "./ui";

const NAV = [
  { to: "/", label: "New check", icon: Plus, end: true },
  { to: "/history", label: "History", icon: History },
  { to: "/collections", label: "Collections", icon: FolderKanban },
  { to: "/library", label: "Library", icon: BookOpen },
  { to: "/settings", label: "Settings", icon: Settings },
];

export function Logo({ compact = false }: { compact?: boolean }) {
  return (
    <span className="flex items-center gap-2.5">
      <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-gradient-to-br from-brand-500 to-violet-600 text-white shadow-sm">
        <ShieldCheck className="h-[18px] w-[18px]" aria-hidden />
      </span>
      {!compact && <span className="text-[15px] font-bold tracking-tight">Originality Checker</span>}
    </span>
  );
}

export function ThemeSwitch() {
  const { theme, setTheme } = useTheme();
  const next = theme === "light" ? "dark" : theme === "dark" ? "system" : "light";
  const Icon = theme === "light" ? Sun : theme === "dark" ? Moon : Monitor;
  return (
    <button
      onClick={() => setTheme(next)}
      className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-800 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-white"
      aria-label={`Theme: ${theme}. Switch to ${next}.`}
      title={`Theme: ${theme}`}
    >
      <Icon className="h-5 w-5" />
    </button>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { me, signOut } = useAuth();
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();

  const links = (onClick?: () => void) =>
    NAV.map(({ to, label, icon: Icon, end }) => (
      <NavLink
        key={to}
        to={to}
        end={end}
        onClick={onClick}
        className={({ isActive }) =>
          cx(
            "flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition",
            isActive
              ? "bg-brand-50 text-brand-700 dark:bg-brand-500/10 dark:text-brand-300"
              : "text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-white",
          )
        }
      >
        <Icon className="h-4 w-4" aria-hidden />
        {label}
      </NavLink>
    ));

  return (
    <div className="min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-white focus:px-4 focus:py-2 focus:shadow">
        Skip to content
      </a>
      <header className="sticky top-0 z-40 border-b border-slate-200/70 bg-white/80 backdrop-blur dark:border-slate-800 dark:bg-slate-950/80">
        <div className="mx-auto flex h-16 max-w-7xl items-center gap-4 px-4 sm:px-6">
          <Link to="/" className="shrink-0" aria-label="Originality Checker home">
            <Logo />
          </Link>
          <nav className="ml-6 hidden items-center gap-1 lg:flex" aria-label="Main">
            {links()}
          </nav>
          <div className="ml-auto flex items-center gap-1">
            <Link
              to="/studio"
              className="hidden items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800 sm:flex"
            >
              <PenLine className="h-4 w-4" aria-hidden /> Writing studio
            </Link>
            <ThemeSwitch />
            {me && (
              <div className="hidden items-center gap-2 pl-2 sm:flex">
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-slate-200 text-xs font-bold text-slate-700 dark:bg-slate-800 dark:text-slate-200" title={me.user.email}>
                  {me.user.name.slice(0, 1).toUpperCase()}
                </span>
                <button
                  onClick={async () => {
                    await signOut();
                    navigate("/");
                  }}
                  className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-800 dark:text-slate-400 dark:hover:bg-slate-800"
                  aria-label="Sign out"
                  title="Sign out"
                >
                  <LogOut className="h-5 w-5" />
                </button>
              </div>
            )}
            <button className="rounded-lg p-2 text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800 lg:hidden" onClick={() => setOpen(true)} aria-label="Open menu">
              <Menu className="h-5 w-5" />
            </button>
          </div>
        </div>
      </header>

      {open && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-slate-950/40" onClick={() => setOpen(false)} aria-hidden />
          <nav className="absolute right-0 top-0 flex h-full w-72 max-w-[85vw] flex-col gap-1 bg-white p-4 shadow-lift dark:bg-slate-900" aria-label="Mobile">
            <div className="mb-3 flex items-center justify-between">
              <Logo />
              <button onClick={() => setOpen(false)} className="rounded-lg p-2 hover:bg-slate-100 dark:hover:bg-slate-800" aria-label="Close menu">
                <X className="h-5 w-5" />
              </button>
            </div>
            {links(() => setOpen(false))}
            <NavLink to="/studio" onClick={() => setOpen(false)} className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800">
              <PenLine className="h-4 w-4" /> Writing studio
            </NavLink>
            {me && (
              <button
                onClick={async () => {
                  setOpen(false);
                  await signOut();
                  navigate("/");
                }}
                className="mt-auto flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
              >
                <LogOut className="h-4 w-4" /> Sign out ({me.user.email})
              </button>
            )}
          </nav>
        </div>
      )}

      <main id="main" className="mx-auto max-w-7xl px-4 py-8 sm:px-6 sm:py-10">
        {children}
      </main>
      <footer className="mx-auto max-w-7xl px-4 pb-10 text-xs text-slate-400 sm:px-6">
        Similarity and AI-writing indicators are evidence to review, never proof of misconduct.
      </footer>
    </div>
  );
}
