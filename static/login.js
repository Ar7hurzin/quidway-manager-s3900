const password = document.getElementById("password");
const toggle = document.getElementById("show-password");
if (password && toggle) toggle.addEventListener("click", () => {
  const visible = password.type === "password";
  password.type = visible ? "text" : "password";
  toggle.textContent = visible ? "Ocultar" : "Mostrar";
  toggle.setAttribute("aria-pressed", String(visible));
});
