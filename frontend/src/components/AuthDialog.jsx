import { useState } from "react";
import { useMutation } from "@apollo/client";
import { LOGIN, ME, REGISTER } from "../graphql.js";
import { errorText } from "../format.js";
import Dialog from "./Dialog.jsx";

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

  return (
    <Dialog title={mode === "login" ? "Ingresar" : "Crear cuenta"} onClose={onClose}>
      {reason && <p className="hint">{reason}</p>}
      <div className="tabs" role="tablist" aria-label="Acceso">
        <button type="button" role="tab" aria-selected={mode === "login"} className={mode === "login" ? "tab tab-on" : "tab"} onClick={() => setMode("login")}>Ya tengo cuenta</button>
        <button type="button" role="tab" aria-selected={mode === "register"} className={mode === "register" ? "tab tab-on" : "tab"} onClick={() => setMode("register")}>Soy nuevo</button>
      </div>
      <form onSubmit={submit} className="auth-form">
        {mode === "register" && (
          <div className="field">
            <label htmlFor="fullName">Nombre completo</label>
            <input id="fullName" required maxLength={120} autoComplete="name" value={form.fullName} onChange={set("fullName")} />
          </div>
        )}
        <div className="field">
          <label htmlFor="email">Correo</label>
          <input id="email" type="email" required autoComplete="email" value={form.email} onChange={set("email")} />
        </div>
        <div className="field">
          <label htmlFor="password">Contraseña</label>
          <input
            id="password" type="password" required minLength={mode === "register" ? 12 : 1} maxLength={128}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            value={form.password} onChange={set("password")}
          />
          {mode === "register" && <span className="hint">Mínimo 12 caracteres, con letras y números.</span>}
        </div>
        {error && <p className="alert alert-error" role="alert">{error}</p>}
        <button type="submit" className="btn btn-primary" disabled={loading}>
          {loading ? "Un momento…" : mode === "login" ? "Ingresar" : "Crear cuenta"}
        </button>
      </form>
    </Dialog>
  );
}
