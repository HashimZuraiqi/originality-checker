import { useState, type FormEvent } from "react";
import { useAuth } from "../lib/app-state";
import { Button, Field, Input } from "./ui";

export function AuthForm({ initialMode = "signup", onDone }: { initialMode?: "signin" | "signup"; onDone?: () => void }) {
  const { signIn, signUp } = useAuth();
  const [mode, setMode] = useState(initialMode);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "signup") await signUp(name, email, password);
      else await signIn(email, password);
      onDone?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      {mode === "signup" && (
        <Field label="Name" htmlFor="auth-name">
          <Input id="auth-name" autoComplete="name" required value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
      )}
      <Field label="Email" htmlFor="auth-email">
        <Input id="auth-email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
      </Field>
      <Field label="Password" htmlFor="auth-password" hint={mode === "signup" ? "At least 10 characters with upper- and lower-case letters and a number." : undefined}>
        <Input
          id="auth-password"
          type="password"
          autoComplete={mode === "signup" ? "new-password" : "current-password"}
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </Field>
      {error && (
        <p role="alert" className="rounded-xl bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-500/10 dark:text-red-300">
          {error}
        </p>
      )}
      <Button type="submit" className="w-full" size="lg" loading={busy}>
        {mode === "signup" ? "Create account" : "Sign in"}
      </Button>
      <p className="text-center text-sm text-slate-500">
        {mode === "signup" ? "Already have an account?" : "New here?"}{" "}
        <button type="button" className="font-semibold text-brand-600 hover:underline dark:text-brand-400" onClick={() => setMode(mode === "signup" ? "signin" : "signup")}>
          {mode === "signup" ? "Sign in" : "Create an account"}
        </button>
      </p>
      <p className="text-center text-xs text-slate-400">Your documents are private, encrypted at rest and never used for training.</p>
    </form>
  );
}
