import { useState } from "react";
import { useMutation } from "@apollo/client";
import { motion } from "motion/react";
import { CircleAlert, KeyRound, LoaderCircle, Mail, UserRound } from "lucide-react";
import { LOGIN, ME, REGISTER } from "../graphql.js";
import { errorText } from "../format.js";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import Dialog from "./Dialog.jsx";

function Field({ id, label, icon: Icon, hint, ...props }) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <div className="relative">
        <Icon className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
        <Input id={id} className="pl-10" {...props} />
      </div>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export default function AuthDialog({ onClose, onSuccess, reason }) {
  const [mode, setMode] = useState("login");
  const [form, setForm] = useState({ fullName: "", email: "", password: "" });
  const [error, setError] = useState("");
  const opts = { refetchQueries: [{ query: ME }], awaitRefetchQueries: true };
  const [login, { loading: l1 }] = useMutation(LOGIN, opts);
  const [register, { loading: l2 }] = useMutation(REGISTER, opts);
  const loading = l1 || l2;

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  async function submit(event) {
    event.preventDefault();
    setError("");
    try {
      if (mode === "login") {
        await login({ variables: { input: { email: form.email, password: form.password } } });
      } else {
        await register({ variables: { input: { email: form.email, password: form.password, fullName: form.fullName } } });
      }
      onSuccess();
    } catch (e) {
      setError(errorText(e));
    }
  }

  const tabs = [["login", "Ya tengo cuenta"], ["register", "Soy nuevo"]];

  return (
    <Dialog title={mode === "login" ? "Ingresar" : "Crear cuenta"} onClose={onClose}
      description={reason || "Tu sesión es segura: se renueva al ingresar y la contraseña se guarda con Argon2id."}>
      <div className="mb-5 grid grid-cols-2 rounded-full bg-secondary p-1" role="tablist" aria-label="Acceso">
        {tabs.map(([value, label]) => (
          <button key={value} type="button" role="tab" aria-selected={mode === value} onClick={() => setMode(value)}
            className={cn("relative rounded-full px-3 py-2 text-sm font-medium transition-colors",
              mode === value ? "text-foreground" : "text-muted-foreground hover:text-foreground")}>
            {mode === value && (
              <motion.span layoutId="auth-tab" className="absolute inset-0 rounded-full bg-card shadow-sm"
                transition={{ type: "spring", duration: 0.35, bounce: 0.15 }} />
            )}
            <span className="relative">{label}</span>
          </button>
        ))}
      </div>

      <form onSubmit={submit} className="space-y-4">
        {mode === "register" && (
          <Field id="fullName" label="Nombre completo" icon={UserRound} required maxLength={120} autoComplete="name"
            value={form.fullName} onChange={set("fullName")} />
        )}
        <Field id="email" label="Correo" icon={Mail} type="email" required autoComplete="email"
          value={form.email} onChange={set("email")} />
        <Field id="password" label="Contraseña" icon={KeyRound} type="password" required
          minLength={mode === "register" ? 12 : 1} maxLength={128}
          autoComplete={mode === "login" ? "current-password" : "new-password"}
          value={form.password} onChange={set("password")}
          hint={mode === "register" ? "Mínimo 12 caracteres, con letras y números." : undefined} />
        {error && (
          <p className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/8 px-3.5 py-2.5 text-sm text-destructive" role="alert">
            <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />{error}
          </p>
        )}
        <Button type="submit" variant="coral" size="lg" className="w-full" disabled={loading}>
          {loading && <LoaderCircle className="animate-spin" aria-hidden="true" />}
          {loading ? "Un momento…" : mode === "login" ? "Ingresar" : "Crear cuenta"}
        </Button>
      </form>
    </Dialog>
  );
}
