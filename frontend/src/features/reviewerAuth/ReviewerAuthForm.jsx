import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import "./ReviewerAuth.css";

const API_URL = "http://127.0.0.1:8000";
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const EMPTY = { name: "", email: "", college: "", unit: "", designation: "", empId: "", phone: "", password: "", confirm: "" };
const errText = (d, fb) => (typeof d.message === "string" && d.message) || (typeof d.detail === "string" && d.detail) || (Array.isArray(d.detail) && d.detail[0]?.msg) || fb;

export default function ReviewerAuthForm({ cfg, mode }) {
  const nav = useNavigate(), isReg = mode === "register";
  const [f, setF] = useState(EMPTY), [errs, setErrs] = useState({}), [show, setShow] = useState(false), [busy, setBusy] = useState(false), [msg, setMsg] = useState(""), [done, setDone] = useState(false);
  const set = k => e => { setF({ ...f, [k]: e.target.value }); setErrs({ ...errs, [k]: "" }); setMsg(""); };

  const validate = () => {
    const e = {};
    if (isReg && f.name.trim().length < 3) e.name = "Enter your full name.";
    if (!EMAIL_RE.test(f.email.trim())) e.email = "Enter a valid email address.";
    if (isReg) {
      if (f.college.trim().length < 3) e.college = "Enter your college / university name.";
      if (!f.designation.trim()) e.designation = "Enter your designation.";
      if (f.phone.trim() && !/^[+\d][\d\s-]{8,14}$/.test(f.phone.trim())) e.phone = "Enter a valid phone number.";
      if (!f.unit.trim()) e.unit = `${cfg.unitOptions ? "Select" : "Enter"} your ${cfg.unitLabel.toLowerCase()}.`;
      else if (cfg.unitOptions && !cfg.unitOptions.includes(f.unit)) e.unit = `Select your ${cfg.unitLabel.toLowerCase()} from the list.`;
      if (!f.empId.trim()) e.empId = "Enter your employee ID.";
      if (f.password.length < 8 || !/[a-z]/.test(f.password) || !/[A-Z]/.test(f.password) || !/\d/.test(f.password)) e.password = "Use 8+ characters with upper-case, lower-case and a number.";
      if (f.confirm !== f.password) e.confirm = "Passwords do not match.";
    } else if (!f.password) e.password = "Enter your password.";
    return e;
  };

  const login = async () => {
    const r = await fetch(`${API_URL}/api/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: f.email.trim(), password: f.password, role: cfg.role }) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok || !(d.success === true || d.status === "success")) return setMsg(errText(d, "Invalid email or password."));
    if (!d.token) return setMsg("Login succeeded, but authentication token was not received.");
    if (d.role && d.role !== cfg.role) return setMsg(`This account is not a ${cfg.label} account.`);
    localStorage.setItem("token", d.token); localStorage.setItem("userEmail", f.email.trim()); localStorage.setItem("userRole", cfg.role);
    nav(cfg.base);
  };
  const register = async () => {
    const r = await fetch(`${API_URL}${cfg.registerPath}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: f.name.trim(), email: f.email.trim(), college: f.college.trim(), department: f.unit.trim(), designation: f.designation.trim(), employee_id: f.empId.trim(), phone: f.phone.trim() || null, password: f.password, role: cfg.role }) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) return setMsg(errText(d, "Registration failed. Please try again."));
    setDone(true); // shown only after the server accepts the request
  };
  const submit = async ev => {
    ev.preventDefault(); const e = validate(); setErrs(e); if (Object.keys(e).length) return;
    setBusy(true); setMsg("");
    try { await (isReg ? register() : login()); } catch { setMsg("Unable to connect to the server. Please make sure the backend is running."); } finally { setBusy(false); }
  };

  const field = (k, label, o = {}) => <div className="ra-f"><label htmlFor={`${cfg.role}-${k}`}>{label}</label>
    <div className="ra-in">{o.options ? <select id={`${cfg.role}-${k}`} value={f[k]} onChange={set(k)} aria-invalid={!!errs[k]} aria-describedby={errs[k] ? `${cfg.role}-${k}-e` : undefined}>
        <option value="">{o.ph}</option>
        {o.options.map(x => <option key={x} value={x}>{x}</option>)}
      </select> : <input id={`${cfg.role}-${k}`} type={o.pw ? (show ? "text" : "password") : o.type || "text"} value={f[k]} onChange={set(k)} placeholder={o.ph} autoComplete={o.ac} aria-invalid={!!errs[k]} aria-describedby={errs[k] ? `${cfg.role}-${k}-e` : undefined} />}
      {o.pw && <button type="button" className="ra-eye" onClick={() => setShow(!show)} aria-label={show ? "Hide password" : "Show password"}>{show ? "🙈" : "👁️"}</button>}</div>
    {errs[k] && <span className="ra-e" id={`${cfg.role}-${k}-e`}>{errs[k]}</span>}</div>;

  return (
    <div className={`rauth ${cfg.role}`}>
      <aside className="ra-brand">
        <div className="ra-logo">🎓 <b>CampusPath</b> AI</div>
        <div className="ra-ic" aria-hidden="true">{cfg.icon}</div>
        <h1>{cfg.title}</h1><p>{cfg.blurb}</p>
        <ul>{cfg.points.map(p => <li key={p}>✓ {p}</li>)}</ul>
      </aside>
      <section className="ra-card">
        {done ? <div className="ra-done"><div style={{ fontSize: 40 }}>✅</div><h2>Request submitted</h2>
          <p>Your {cfg.label} account must be approved by an Admin before you can sign in.</p><Link className="ra-btn" to={cfg.loginRoute}>Go to sign in</Link></div> : <>
          <h2>{isReg ? `Request ${cfg.label} access` : `${cfg.label} sign in`}</h2>
          <p className="ra-sub">{isReg ? "Accounts are verified by the Admin before activation." : "Use your official college email."}</p>
          <form onSubmit={submit} noValidate>
            {isReg && field("name", "Full name", { ac: "name", ph: "Your full name" })}
            {field("email", isReg ? "Official college email" : "College email", { type: "email", ac: "email", ph: "you@college.edu" })}
            {isReg && field("college", "College / University", { ac: "organization", ph: "e.g. ABC Institute of Technology" })}
            {isReg && field("unit", cfg.unitLabel, { ph: cfg.unitPh, options: cfg.unitOptions })}
            {isReg && field("designation", "Designation", { ph: cfg.role === "dean" ? "e.g. Dean, School of Engineering" : "e.g. Professor & HOD" })}
            {isReg && field("empId", "Employee ID", { ph: "e.g. EMP-1024" })}
            {isReg && field("phone", "Phone (optional)", { type: "tel", ac: "tel", ph: "+91 98765 43210" })}
            {field("password", "Password", { pw: true, ac: isReg ? "new-password" : "current-password", ph: "Password" })}
            {isReg && field("confirm", "Confirm password", { pw: true, ac: "new-password", ph: "Re-enter password" })}
            {msg && <div className="ra-msg" role="alert">{msg}</div>}
            <button className="ra-btn" type="submit" disabled={busy}>{busy ? (isReg ? "Submitting…" : "Checking…") : isReg ? "Submit request" : "Continue"}</button>
          </form>
          <p className="ra-alt">{isReg ? <>Already approved? <Link to={cfg.loginRoute}>Sign in</Link></> : <>No account yet? <Link to={cfg.registerRoute}>Request access</Link></>}</p>
          <p className="ra-alt"><Link to="/login">← Main login</Link> · <Link to={cfg.other.path}>{cfg.other.label} login</Link></p></>}
      </section>
    </div>
  );
}