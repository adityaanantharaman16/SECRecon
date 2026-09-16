"use strict";
const csrf = document.querySelector('meta[name="csrf-token"]').content;
const result = document.getElementById("action-result");
async function submit(url, body, element) {
  // Keep the same key after an uncertain network outcome. Editing a form resets it.
  element.dataset.key ||= crypto.randomUUID();
  const buttons = element.matches("button") ? [element] : element.querySelectorAll("button");
  buttons.forEach(button => { button.disabled = true; });
  result.hidden = false;
  result.textContent = "Submitting durable operation…";
  try {
    const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf, "Idempotency-Key": element.dataset.key }, body: JSON.stringify(body) });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error?.message || "Operation rejected");
    result.textContent = "Operation accepted. ";
    const link = document.createElement("a");
    link.href = "/ui/operations/" + encodeURIComponent(payload.operation_id);
    link.textContent = "Inspect progress and outcome →";
    result.append(link);
  } catch (error) { result.textContent = error.message + ". You can retry this request safely."; }
  finally { buttons.forEach(button => { button.disabled = false; }); }
}
document.querySelectorAll("form[data-api]").forEach(form => {
  form.addEventListener("input", () => { delete form.dataset.key; });
  form.addEventListener("submit", event => {
    event.preventDefault();
    const body = Object.fromEntries(new FormData(form));
    if (body.ciks) body.ciks = body.ciks.split(",").map(value => value.trim()).filter(Boolean);
    if (body.max_jobs) body.max_jobs = Number(body.max_jobs);
    submit(form.dataset.api, body, form);
  });
});
document.querySelectorAll("button[data-api-button]").forEach(button => button.addEventListener("click", () => submit(button.dataset.apiButton, {}, button)));
document.getElementById("logout")?.addEventListener("click", async () => {
  const response = await fetch("/ui/logout", {method:"POST",headers:{"X-CSRF-Token":csrf}});
  if(response.ok) location.href="/ui/overview";
});
