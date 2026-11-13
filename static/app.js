const $ = (id) => document.getElementById(id);
const baseFetch = window.fetch.bind(window);
window.fetch = (input, init = {}) => {
  const url = new URL(typeof input === "string" ? input : input.url, window.location.href);
  const method = String(init.method || "GET").toUpperCase();
  if (url.origin === window.location.origin && !["GET", "HEAD", "OPTIONS"].includes(method)) {
    const headers = new Headers(init.headers || {});
    headers.set("X-CSRF-Token", document.querySelector('meta[name="csrf-token"]')?.content || "");
    init = { ...init, headers };
  }
  return baseFetch(input, init).then(response => {
    if (url.origin === window.location.origin && url.pathname.startsWith("/api/") && response.status === 401) {
      window.location.assign("/login");
      throw new Error("Sessão encerrada. Entre novamente.");
    }
    return response;
  });
};
let appStatus = { mode: "demo", ready: false };
let configStatus = { available: false, writes_enabled: false };
let pendingConfigPreview = null;
let cliToken = sessionStorage.getItem("qm_cli_token") || "";
let cliConnected = false;
let cliOccupiedElsewhere = false;
let cliSensitivePrompt = false;

function setMode(status) {
  appStatus = status;
  const isVirtual = status.mode === "virtual";
  const isSerial = status.mode === "serial";
  const isLive = status.mode === "real" || isSerial || isVirtual;
  const pill = $("mode-pill");
  const banner = $("mode-banner");
  pill.textContent = isVirtual ? "SIMULAÇÃO · S3900 VIRTUAL" : status.mode === "real" ? "MODO REAL · SSH" : isSerial ? "MODO REAL · CONSOLE" : "DEMONSTRAÇÃO";
  pill.classList.toggle("real", isLive);
  banner.classList.toggle("real", isLive);
  banner.innerHTML = `<span class="banner-icon">${isLive ? "✓" : "!"}</span><span>${escapeHtml(status.message)}</span>`;
  $("ssh-message").textContent = isLive
    ? (status.ready ? (isSerial ? "Console serial configurado. Ao atualizar, o Manager envia consultas fixas somente de leitura." : "Conector SSH configurado. A interface envia somente os comandos fixos de consulta.") : status.message)
    : "Modo de demonstração: nenhuma sessão é aberta e nenhum dado vem do switch.";
  $("transport-badge").textContent = isVirtual ? "VIRTUAL" : isSerial ? "CONSOLE" : "SSH";
  $("refresh").disabled = isLive && !status.ready;
  $("refresh").title = isLive && !status.ready ? status.message : "Executar leitura segura";
  if (status.mode === "demo") {
    $("device-name").textContent = "S3900 · demonstração";
    $("device-model").textContent = "Exemplo de interface · aparelho não identificado";
    $("ports-count").textContent = "18 / 24";
    $("vlan-count").textContent = "4";
    renderDemo();
  } else if (isVirtual) {
    $("ssh-message").textContent = "Console virtual isolado; não acessa hardware.";
    $("ports-note").textContent = "28 interfaces simuladas. UP indica link virtual e porta habilitada.";
    $("system-page").querySelector(".subtle-tag").textContent = "Simulação";
    $("system-page").querySelector(".system-note").textContent = "Perfil simulado; não executa firmware Huawei original.";
    $("system-page").querySelectorAll(".system-detail-card")[0].innerHTML = '<div class="card-overline">PERFIL VIRTUAL</div><h2>Quidway 3900-SI</h2><p>VRP 3.10(CN) RELEASE 0002 · respostas simuladas.</p>';
    $("system-page").querySelectorAll(".system-detail-card")[1].innerHTML = '<div class="card-overline">HARDWARE</div><h2>Modelo lógico</h2><p>Sem emulação de CPU, memória ou ASIC.</p>';
    $("system-page").querySelectorAll(".system-detail-card")[2].innerHTML = '<div class="card-overline">PORTAS VIRTUAIS</div><h2>28 interfaces</h2><p>24 Fast Ethernet e 4 Gigabit Ethernet simuladas.</p>';
    $("system-page").querySelectorAll(".system-detail-card")[5].innerHTML = '<div class="card-overline">VALIDAÇÃO</div><h2>Laboratório virtual</h2><p>Teste de comandos, não confirmação de compatibilidade física.</p>';
    $("system-page").querySelectorAll(".system-detail-card")[3].innerHTML = '<div class="card-overline">CONSOLE VIRTUAL</div><h2>S3900 Virtual</h2><p>Nenhuma porta COM ou SSH física utilizada.</p><p id="system-read-state">Aguardando leitura</p>';
    $("system-page").querySelectorAll(".system-detail-card")[4].innerHTML = '<div class="card-overline">ISOLAMENTO</div><h2>Sem conexão física</h2><p>O transporte virtual não utiliza SSH nem a rede do laboratório.</p>';
    $("switch-connections-note").textContent = "Links virtuais não representam dispositivos físicos.";
    $("cli-connection-note").textContent = "Console virtual: comandos alteram somente o simulador.";
    document.querySelector(".cli-warning").textContent = "Terminal virtual: comandos alteram apenas a simulação. Envie um comando por vez.";
    loadVirtualState();
    window.setInterval(loadVirtualState, 3000);
  } else if (isLive) {
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
  loadConfigurationStatus();
  if (pageKind === "cli") loadCliStatus();
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

function renderVirtual(data) {
  $("device-name").textContent = data.name;
  $("device-model").textContent = data.model + " · " + data.version;
  $("ports-count").textContent = data.ports.filter(p => p.state === "up").length + " / " + data.ports.length;
  $("vlan-count").textContent = data.vlans.length;
  $("last-read").textContent = new Date().toLocaleTimeString("pt-BR");
  $("connection-state").classList.add("online");
  $("connection-state").querySelector("span").textContent = "Virtual ativo";
  $("port-grid").innerHTML = data.ports.map(p => '<div class="port ' + (p.state === "down" ? "down" : "") + '" title="' + escapeHtml(p.interface + " · " + p.mode + " · PVID " + p.pvid + " · " + (p.admin ? "Habilitada" : "Desabilitada") + " · " + p.description) + '"><span class="port-num">' + String(p.number).padStart(2,"0") + '</span><span class="port-state">' + (p.admin ? p.state.toUpperCase() : "OFF") + '</span></div>').join("");
  $("vlan-body").innerHTML = data.vlans.map(v => "<tr>" + [v.id,v.name,v.members,v.note].map(x=>"<td>"+escapeHtml(x)+"</td>").join("") + "</tr>").join("");
  $("virtual-details").hidden = false;
  $("virtual-save-state").textContent = data.dirty ? "Alterações ainda não salvas no startup virtual" : "Configuração virtual salva";
  $("virtual-ips").textContent = Object.entries(data.ips).map(([v,ip]) => "VLAN " + v + ": " + ip.ip + " / " + ip.mask).join("\n") || "Nenhum IP configurado";
  $("virtual-routes").textContent = data.routes.map(r => r.join(" ")).join("\n") || "Nenhuma rota estática";
}

async function loadVirtualState() {
  if (appStatus.mode !== "virtual") return;
  try {
    const response = await fetch("/api/virtual/state", {cache:"no-store"});
    if (!response.ok) throw new Error("Estado virtual indisponível");
    renderVirtual(await response.json());
  } catch {
    $("connection-state").classList.remove("online");
    $("connection-state").querySelector("span").textContent = "Virtual indisponível";
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

async function loadStatus() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) throw new Error("Serviço indisponível");
    setMode(await response.json());
  } catch {
    $("mode-pill").textContent = "INDISPONÍVEL";
    $("mode-banner").textContent = "Não foi possível consultar o serviço local. Atualize a página.";
    $("refresh").disabled = true;
  }
}

function showOutputs(items) {
  if (!items?.length) {
    $("outputs").innerHTML = '<div class="empty-inline">Sem saída de diagnóstico. A conexão não enviou nenhum comando não autorizado.</div>';
    return;
  }
  $("outputs").innerHTML = items.map(item => `<section class="output-block"><div class="output-title">${escapeHtml(item.command)}</div><pre>${escapeHtml(item.output || "(sem saída)")}</pre></section>`).join("");
}

const pageKind = document.documentElement.dataset.page || "dashboard";

async function loadConnections() {
  try {
    const response = await fetch("/api/connections", { cache: "no-store" });
    if (!response.ok) throw new Error("A sessão expirou ou a lista não está disponível.");
    const data = await response.json();
    const manager = data.manager || {};
    const count = Number(manager.active_sessions || 0);
    $("active-session-count").textContent = `${count} ${count === 1 ? "sessão" : "sessões"}`;
    const users = manager.users || [];
    $("manager-session-list").innerHTML = users.length
      ? users.map(user => {
          const lastActivity = new Date(user.last_activity).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
          return `<div class="manager-session-row"><span class="session-indicator"></span><strong>${escapeHtml(user.username)}</strong><span>${user.sessions} ${user.sessions === 1 ? "sessão" : "sessões"}</span><small>Atividade às ${escapeHtml(lastActivity)}</small></div>`;
        }).join("")
      : '<div class="empty-inline">Nenhuma sessão foi registrada nos últimos 5 minutos.</div>';
    $("switch-device-count").textContent = "N/D";
    $("switch-connections-note").textContent = appStatus.mode === "virtual" ? "Links virtuais não representam dispositivos físicos." : data.switch?.message || "A coleta de dispositivos ainda não está disponível.";
  } catch (error) {
    $("manager-session-list").innerHTML = `<div class="empty-inline">${escapeHtml(error.message)}</div>`;
    $("active-session-count").textContent = "—";
    $("switch-device-count").textContent = "N/D";
  }
}

function syncNavigation() {
  document.querySelectorAll(".main-nav .nav-item").forEach(link => {
    const targetPage = link.dataset.pageLink;
    link.classList.toggle("active", targetPage === pageKind);
  });
  const page = pageKind === "configuration"
    ? ["Configuração", "GERENCIAMENTO <span>/</span> CONFIGURAÇÃO"]
    : pageKind === "system"
      ? ["Sistema", "EQUIPAMENTO <span>/</span> INFORMAÇÕES DO SISTEMA"]
    : pageKind === "cli"
      ? ["CLI do Quidway", "GERENCIAMENTO <span>/</span> TERMINAL INTERATIVO"]
      : pageKind === "monitoring"
      ? ["Monitoramento", "SWITCHING <span>/</span> VISÃO GERAL"]
      : ["Dashboard", "SWITCHES <span>/</span> QUIDWAY 3900-SI"];
  $("page-title").textContent = page[0];
  $("page-breadcrumb").innerHTML = page[1];
}

syncNavigation();

$("refresh").addEventListener("click", async () => {
  const button = $("refresh");
  button.disabled = true;
  button.innerHTML = "<span>◌</span> Consultando...";
  try {
    const response = await fetch("/api/refresh", { method: "POST", headers: { "Accept": "application/json" } });
    const result = await response.json();
    $("ssh-message").textContent = result.message || result.error || "Não foi possível consultar o switch.";
    $("last-read").textContent = result.ok ? new Date(result.collected_at).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }) : "Falha";
    const systemReadState = $("system-read-state");
    if (systemReadState) systemReadState.textContent = result.ok ? `Consultado às ${new Date(result.collected_at).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}` : "Falha na consulta";
    if (result.mode === "virtual") {
      if (result.snapshot) renderVirtual(result.snapshot);
      if (result.outputs?.length) showOutputs(result.outputs);
    } else if (result.mode === "real" || result.mode === "serial") {
      $("connection-state").classList.toggle("online", result.ok);
      $("connection-state").querySelector("span").textContent = result.ok ? (result.mode === "serial" ? "Console consultado" : "SSH consultado") : "Sem conexão";
      showOutputs(result.outputs);
    } else {
      $("mode-banner").innerHTML = `<span class="banner-icon">!</span><span>${escapeHtml(result.message || result.error || "Não foi possível concluir a consulta.")}</span>`;
    }
  } catch {
    $("ssh-message").textContent = "Não foi possível falar com o serviço local.";
  } finally {
    button.innerHTML = "<span>↻</span> Atualizar leitura";
    button.disabled = (appStatus.mode === "real" || appStatus.mode === "serial") && !appStatus.ready;
  }
});

loadStatus();
if (pageKind === "dashboard") {
  loadConnections();
  window.setInterval(loadConnections, 30_000);
}

function parseVlanCsv(value, label) {
  const items = String(value || "").split(/[ ,]+/).filter(Boolean).map(Number);
  if (!items.length || items.some(value => !Number.isInteger(value) || value < 1 || value > 4094)) {
    throw new Error(`Informe ${label} como IDs inteiros de 1 a 4094, separados por vírgula.`);
  }
  return [...new Set(items)];
}

async function loadConfigurationStatus() {
  try {
    const response = await fetch("/api/config/status", { cache: "no-store" });
    configStatus = await response.json();
  } catch {
    configStatus = { available: false, writes_enabled: false, message: "Não foi possível consultar o modo de configuração." };
  }
  const state = $("config-state");
  const banner = $("config-banner");
  state.textContent = configStatus.writes_enabled ? "Alterações habilitadas" : "Prévia · gravação bloqueada";
  banner.textContent = configStatus.message;
  banner.classList.toggle("ready", configStatus.writes_enabled);
  banner.classList.toggle("locked", !configStatus.writes_enabled);
  $("backup-button").disabled = !configStatus.available;
  $("save-config-button").disabled = !configStatus.available;
  for (const id of ["vlan-form", "port-form", "system-form", "route-form"]) {
    $(id).querySelector('button[type="submit"]').disabled = !configStatus.available;
  }
  updateApplyAvailability();
}

function setFieldsVisible(selector, visible) {
  document.querySelectorAll(selector).forEach(element => { element.hidden = !visible; });
}

function updateConfigurationFields() {
  const vlanAction = $("vlan-action").value;
  setFieldsVisible(".vlan-name-field", vlanAction !== "delete_vlan");
  const portAction = $("port-action").value;
  setFieldsVisible(".port-vlan-field", portAction === "set_trunk_port");
  setFieldsVisible(".port-access-field", portAction === "set_access_port");
  setFieldsVisible(".port-pvid-field", portAction === "set_trunk_port" || portAction === "set_hybrid_port");
  setFieldsVisible(".port-hybrid-field", portAction === "set_hybrid_port");
  setFieldsVisible(".port-description-field", portAction === "set_port_description");
  setFieldsVisible(".port-state-field", portAction === "set_port_state");
  const systemAction = $("system-action").value;
  setFieldsVisible(".system-name-field", systemAction === "set_sysname");
  setFieldsVisible(".management-field", systemAction === "set_management_ip");
}

$("vlan-action").addEventListener("change", updateConfigurationFields);
$("port-action").addEventListener("change", updateConfigurationFields);
$("system-action").addEventListener("change", updateConfigurationFields);
updateConfigurationFields();

async function previewConfiguration(payload) {
  pendingConfigPreview = null;
  $("config-result").textContent = "";
  $("config-preview").hidden = true;
  try {
    const response = await fetch("/api/config/preview", {
      method: "POST", headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify(payload), cache: "no-store",
    });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || "Não foi possível criar a prévia.");
    pendingConfigPreview = result;
    $("preview-summary").textContent = result.summary;
    $("preview-warnings").replaceChildren(...result.warnings.map(warning => {
      const item = document.createElement("li"); item.textContent = warning; return item;
    }));
    $("preview-commands").textContent = result.commands.join("\n");
    $("config-result").textContent = "";
    $("config-preview").hidden = false;
    $("config-preview").scrollIntoView({ behavior: "smooth", block: "start" });
    updateApplyAvailability();
  } catch (error) {
    $("config-result").classList.add("error");
    $("config-result").textContent = error.message;
  }
}

$("vlan-form").addEventListener("submit", event => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.currentTarget));
  data.vlan_id = Number(data.vlan_id);
  previewConfiguration(data);
});

$("port-form").addEventListener("submit", event => {
  event.preventDefault();
  try {
    const data = Object.fromEntries(new FormData(event.currentTarget));
    data.port = Number(data.port);
    if (data.action === "set_access_port") data.vlan_id = Number(data.vlan_id);
    if (data.action === "set_trunk_port") {
      data.vlans = parseVlanCsv(data.vlans, "as VLANs permitidas");
      data.pvid = Number(data.pvid);
    }
    if (data.action === "set_hybrid_port") {
      data.tagged_vlans = data.tagged_vlans ? parseVlanCsv(data.tagged_vlans, "as VLANs tagged") : [];
      data.untagged_vlans = data.untagged_vlans ? parseVlanCsv(data.untagged_vlans, "as VLANs untagged") : [];
      data.pvid = Number(data.pvid);
    }
    previewConfiguration(data);
  } catch (error) {
    $("config-result").classList.add("error");
    $("config-result").textContent = error.message;
    $("config-preview").hidden = true;
  }
});

$("system-form").addEventListener("submit", event => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.currentTarget));
  if (data.action === "set_management_ip") {
    data.vlan_id = Number(data.vlan_id);
    data.prefix = Number(data.prefix);
  }
  previewConfiguration(data);
});

$("route-form").addEventListener("submit", event => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.currentTarget));
  data.prefix = Number(data.prefix);
  previewConfiguration(data);
});

$("save-config-button").addEventListener("click", () => previewConfiguration({ action: "save" }));

$("backup-button").addEventListener("click", async () => {
  const resultBox = $("config-result");
  resultBox.classList.remove("error");
  resultBox.textContent = "";
  try {
    const response = await fetch("/api/config/backup", {
      method: "POST", headers: { "Accept": "application/json" }, cache: "no-store",
    });
    if (!response.ok) {
      const error = await response.json(); throw new Error(error.error || "Falha ao exportar backup.");
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = response.headers.get("Content-Disposition")?.match(/filename="?([^";]+)"?/)?.[1] || "quidway-config.txt";
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    resultBox.textContent = "Backup exportado. Guarde o arquivo em local privado; ele pode conter credenciais ou hashes.";
  } catch (error) {
    resultBox.classList.add("error");
    resultBox.textContent = error.message;
  }
});

function updateApplyAvailability() {
  if (!$("apply-config-button")) return;
  const button = $("apply-config-button");
  const hint = $("apply-hint");
  button.disabled = !configStatus.writes_enabled || !pendingConfigPreview;
  if (!configStatus.writes_enabled) {
    hint.textContent = configStatus.message || "O envio de alterações está desabilitado na configuração local.";
  } else if (!pendingConfigPreview) {
    hint.textContent = "Gere uma prévia; depois o botão Aplicar fica disponível.";
  } else {
    hint.textContent = "Prévia pronta. Backup é opcional e recomendado; revise os comandos antes de aplicar.";
  }
  button.title = button.disabled ? hint.textContent : "Aplicar a operação mostrada na prévia";
}

$("apply-config-button").addEventListener("click", async () => {
  if (!pendingConfigPreview || !configStatus.writes_enabled) return;
  const applyButton = $("apply-config-button");
  const previewToApply = pendingConfigPreview;
  pendingConfigPreview = null;
  applyButton.disabled = true;
  applyButton.textContent = "Enviando...";
  const resultBox = $("config-result");
  resultBox.classList.remove("error");
  resultBox.textContent = "";
  try {
    const response = await fetch("/api/config/apply", {
      method: "POST", headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({
        token: previewToApply.token,
      }), cache: "no-store",
    });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || "O switch não aceitou a operação.");
    pendingConfigPreview = null;
    resultBox.textContent = result.message;
    const output = document.createElement("pre");
    output.textContent = result.outputs.map(item => `${item.command}\n${item.output}`).join("\n\n");
    resultBox.append(output);
    if (result.snapshot) renderVirtual(result.snapshot);
    await loadConfigurationStatus();
  } catch (error) {
    resultBox.classList.add("error");
    resultBox.textContent = error.message;
  } finally {
    applyButton.textContent = "Aplicar no switch";
    updateApplyAvailability();
  }
});

let cliAvailable = false;
function setCliConnected(connected, note = "") {
  cliConnected = connected;
  if (connected) cliOccupiedElsewhere = false;
  $("cli-state").textContent = connected ? (appStatus.mode === "virtual" ? "Conectado · virtual" : "Conectado · serial") : "Desconectado";
  $("cli-state").classList.toggle("cli-online", connected);
  $("cli-open").disabled = connected || cliOccupiedElsewhere || !cliAvailable || !["serial","virtual"].includes(appStatus.mode);
  $("cli-close").disabled = !connected;
  $("cli-command").disabled = !connected;
  $("cli-enter").disabled = !connected;
  $("cli-send").disabled = !connected;
  if (note) $("cli-connection-note").textContent = note;
}

function appendCliOutput(text, command = "") {
  const output = $("cli-output");
  const hint = output.querySelector(".cli-hint");
  if (hint) hint.remove();
  const block = document.createElement("div");
  block.className = "cli-block";
  if (command) {
    const sent = document.createElement("div");
    sent.className = "cli-sent";
    const redact = cliSensitivePrompt || /password|passwd|secret|community|usm-user|cipher|key-string/i.test(command);
    sent.textContent = redact ? "> [entrada oculta]" : `> ${command}`;
    block.append(sent);
  }
  const received = document.createElement("pre");
  const redactEcho = cliSensitivePrompt || /password|passwd|secret|community|usm-user|cipher|key-string/i.test(command);
  received.textContent = (redactEcho && command ? text.split(command).join("[entrada oculta]") : text) || "(sem saída)";
  block.append(received);
  output.append(block);
  output.scrollTop = output.scrollHeight;
  cliSensitivePrompt = /password|passwd|secret/i.test(text);
}

async function loadCliStatus() {
  try {
    const response = await fetch("/api/cli/status", { cache: "no-store" });
    const status = await response.json();
    cliAvailable = Boolean(status.available);
    if (!["serial","virtual"].includes(status.mode) || !status.available) {
      sessionStorage.removeItem("qm_cli_token");
      cliToken = "";
      setCliConnected(false, status.mode === "serial" ? status.message : "Configure QM_MODE=serial e QM_ENABLE_CLI=true somente se precisar do terminal direto.");
    } else if (status.connected && cliToken) {
      setCliConnected(true, "Sessão serial recuperada nesta aba. Envie somente comandos destinados a este Quidway.");
    } else if (status.connected) {
      cliOccupiedElsewhere = true;
      setCliConnected(false, "Já existe uma sessão serial ativa em outra aba. Volte à aba original e encerre a sessão primeiro.");
    } else {
      cliOccupiedElsewhere = false;
      sessionStorage.removeItem("qm_cli_token");
      cliToken = "";
      setCliConnected(false, status.mode === "virtual" ? "Console virtual disponível. Nenhum cabo necessário." : "Console serial configurado. Feche MobaXterm/Tera Term e conecte quando estiver pronto.");
    }
  } catch {
    setCliConnected(false, "Não foi possível consultar o serviço local.");
  }
}

$("cli-open").addEventListener("click", async () => {
  const button = $("cli-open");
  button.disabled = true;
  button.textContent = "Conectando…";
  $("cli-result").textContent = "";
  try {
    const response = await fetch("/api/cli/open", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}", cache: "no-store" });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || "Não foi possível abrir o console serial.");
    cliToken = result.token;
    sessionStorage.setItem("qm_cli_token", cliToken);
    appendCliOutput(result.output, "");
    setCliConnected(true, `Conectado a ${result.port} · ${result.baud_rate} baud. Se aparecer Username/Password, digite um valor por linha.`);
    $("cli-command").focus();
  } catch (error) {
    $("cli-result").classList.add("error");
    $("cli-result").textContent = error.message;
    setCliConnected(false);
  } finally {
    button.textContent = "Conectar ao console";
  }
});

$("cli-close").addEventListener("click", async () => {
  if (!cliToken) return;
  try {
    const response = await fetch("/api/cli/close", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: cliToken }), cache: "no-store" });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || "Não foi possível encerrar a sessão.");
    $("cli-result").textContent = result.message;
  } catch (error) {
    $("cli-result").classList.add("error");
    $("cli-result").textContent = error.message;
  } finally {
    cliToken = "";
    sessionStorage.removeItem("qm_cli_token");
    setCliConnected(false);
  }
});

$("cli-command-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (!cliConnected || !cliToken) return;
  const typed = $("cli-command").value.trim();
  if (!typed) return;
  const command = typed === "<ENTER>" ? "" : typed;
  const sendButton = $("cli-send");
  sendButton.disabled = true;
  sendButton.textContent = "Aguardando…";
  $("cli-result").classList.remove("error");
  $("cli-result").textContent = "";
  try {
    const response = await fetch("/api/cli/send", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: cliToken, command }), cache: "no-store" });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || "Não foi possível enviar o comando.");
    appendCliOutput(result.output, command || "↵ Enter");
    if (appStatus.mode === "virtual") loadVirtualState();
    if (result.timed_out) $("cli-result").textContent = "O tempo de leitura terminou antes do prompt. Mostrei a saída parcial e não reenviei o comando; verifique a tela do switch antes de continuar.";
    $("cli-command").value = "";
  } catch (error) {
    $("cli-result").classList.add("error");
    $("cli-result").textContent = error.message;
    if (/sessão expirou|encerrada|falhou/i.test(error.message)) {
      cliToken = "";
      sessionStorage.removeItem("qm_cli_token");
      setCliConnected(false);
    }
  } finally {
    sendButton.textContent = "Enviar";
    sendButton.disabled = !cliConnected;
    $("cli-command").focus();
  }
});

$("cli-enter").addEventListener("click", () => {
  if (!cliConnected) return;
  $("cli-command").value = "<ENTER>";
  $("cli-command-form").requestSubmit();
});

$("cli-command").addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("cli-command-form").requestSubmit();
  }
});

$("cli-clear").addEventListener("click", () => {
  $("cli-output").replaceChildren();
  $("cli-result").textContent = "Saída removida somente da tela. Nenhum comando foi enviado ao switch.";
});

window.addEventListener("pagehide", () => {
  if (cliToken) {
    fetch("/api/cli/close", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: cliToken }), keepalive: true });
    sessionStorage.removeItem("qm_cli_token");
  }
});
