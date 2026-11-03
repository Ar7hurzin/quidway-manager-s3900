const $ = (id) => document.getElementById(id);
let appStatus = { mode: "demo", ready: false };

function setMode(status) {
  appStatus = status;
  const pill = $("mode-pill");
  const banner = $("mode-banner");
  pill.textContent = status.mode === "real" ? "MODO REAL · SSH" : "DEMONSTRAÇÃO";
  pill.classList.toggle("real", status.mode === "real");
  banner.classList.toggle("real", status.mode === "real");
  banner.innerHTML = `<span class="banner-icon">${status.mode === "real" ? "✓" : "!"}</span><span>${escapeHtml(status.message)}</span>`;
  $("ssh-message").textContent = status.mode === "real"
    ? (status.ready ? "Conector ativo. A interface enviará somente os comandos fixos de consulta." : status.message)
    : "Modo de demonstração: nenhuma sessão SSH é aberta e nenhum dado vem do switch.";
  $("refresh").disabled = status.mode === "real" && !status.ready;
  $("refresh").title = status.mode === "real" && !status.ready ? status.message : "Executar leitura segura";
  if (status.mode === "demo") {
    $("device-name").textContent = "S3900 · demonstração";
    $("device-model").textContent = "Exemplo de interface · aparelho não identificado";
    $("ports-count").textContent = "18 / 24";
    $("vlan-count").textContent = "4";
    renderDemo();
  } else {
    $("device-name").textContent = "Switch não consultado";
    $("device-model").textContent = "O modelo e o firmware só aparecem após uma leitura real";
    $("ports-count").textContent = "—";
    $("vlan-count").textContent = "—";
    $("last-read").textContent = "—";
    $("port-grid").innerHTML = '<div class="empty-inline">O mapa ficará vazio até validar o parser deste firmware.</div>';
    $("vlan-body").innerHTML = '<tr><td colspan="4" class="table-empty">Sem leitura real. Dados de demonstração desativados.</td></tr>';
    $("outputs").innerHTML = '<div class="empty-inline">Nenhuma consulta executada nesta sessão.</div>';
    $("connection-state").classList.remove("online");
    $("connection-state").querySelector("span").textContent = "Sem conexão";
  }
}

function renderDemo() {
  const ports = Array.from({ length: 24 }, (_, i) => {
    const num = i + 1;
    const down = [3, 6, 10, 13, 17, 20].includes(num);
    return `<div class="port ${down ? "down" : ""}" title="Porta ${num} · ${down ? "exemplo sem link" : "exemplo ativa"}"><span class="port-num">${String(num).padStart(2, "0")}</span><span class="port-state">${down ? "DOWN" : "UP"}</span></div>`;
  }).join("");
  $("port-grid").innerHTML = ports;
  const rows = [
    ["1", "default", "23–24", "Ilustrativo"],
    ["10", "ADMIN", "23 (tagged), 1–4", "Ilustrativo"],
    ["20", "USUARIOS", "23 (tagged), 5–16", "Ilustrativo"],
    ["99", "GESTAO", "24 (tagged), 17–18", "Ilustrativo"],
  ];
  $("vlan-body").innerHTML = rows.map(row => `<tr>${row.map(v => `<td>${escapeHtml(v)}</td>`).join("")}</tr>`).join("");
  $("last-read").textContent = "Demonstração";
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

async function loadStatus() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    setMode(await response.json());
  } catch {
    setMode({ mode: "demo", ready: false, message: "Não foi possível consultar o serviço local." });
  }
}

function showOutputs(items) {
  if (!items?.length) {
    $("outputs").innerHTML = '<div class="empty-inline">Sem saída de diagnóstico. A conexão não enviou nenhum comando não autorizado.</div>';
    return;
  }
  $("outputs").innerHTML = items.map(item => `<section class="output-block"><div class="output-title">${escapeHtml(item.command)}</div><pre>${escapeHtml(item.output || "(sem saída)")}</pre></section>`).join("");
}

$("refresh").addEventListener("click", async () => {
  const button = $("refresh");
  button.disabled = true;
  button.innerHTML = "<span>◌</span> Consultando...";
  try {
    const response = await fetch("/api/refresh", { method: "POST", headers: { "Accept": "application/json" } });
    const result = await response.json();
    $("ssh-message").textContent = result.message;
    $("last-read").textContent = result.ok ? new Date(result.collected_at).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }) : "Falha";
    if (result.mode === "real") {
      $("connection-state").classList.toggle("online", result.ok);
      $("connection-state").querySelector("span").textContent = result.ok ? "SSH consultado" : "Sem conexão";
      showOutputs(result.outputs);
    } else {
      $("mode-banner").innerHTML = `<span class="banner-icon">!</span><span>${escapeHtml(result.message)}</span>`;
    }
  } catch {
    $("ssh-message").textContent = "Não foi possível falar com o serviço local.";
  } finally {
    button.innerHTML = "<span>↻</span> Atualizar leitura";
    button.disabled = appStatus.mode === "real" && !appStatus.ready;
  }
});

loadStatus();
