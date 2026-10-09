"use strict";
const ui = Object.fromEntries(
  [
    "mode-status",
    "server-status",
    "controls",
    "token",
    "real-send",
    "result-label",
    "result",
    "protocol-table",
  ].map((id) => [id, document.getElementById(id)]),
);
let hardwareEnabled = false;
let busy = false;

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    cache: "no-store",
    signal: AbortSignal.timeout(10000),
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function updateMode() {
  if (!hardwareEnabled || !ui.token.value.trim())
    ui["real-send"].checked = false;
  ui["real-send"].disabled = !hardwareEnabled || !ui.token.value.trim() || busy;
  ui["mode-status"].textContent = ui["real-send"].checked
    ? "REAL IR SENDS"
    : "PREVIEW ONLY";
  ui["mode-status"].classList.toggle("amber", ui["real-send"].checked);
  document.querySelectorAll("#controls button").forEach((button) => {
    button.disabled = busy;
  });
}

async function sendCommand(command) {
  if (busy) return;
  const preview = !ui["real-send"].checked;
  busy = true;
  updateMode();
  ui["result-label"].classList.remove("result-error");
  ui["result-label"].textContent = preview
    ? "Building preview…"
    : "Transmitting one frame…";
  const headers = { "Content-Type": "application/json" };
  if (!preview) headers.Authorization = `Bearer ${ui.token.value.trim()}`;
  try {
    const data = await request(
      `/api/ac/commands/${encodeURIComponent(command.name)}`,
      {
        method: "POST",
        headers,
        body: JSON.stringify({ preview }),
      },
    );
    ui["result-label"].textContent =
      data.delivery === "dry_run"
        ? `${command.label}: preview ready. No IR was sent.`
        : `${command.label}: transmitted. Check the A/C; reception is unconfirmed.`;
    ui.result.textContent = JSON.stringify(data, null, 2);
  } catch (error) {
    ui["result-label"].classList.add("result-error");
    ui["result-label"].textContent = preview
      ? `Preview failed: ${error.message}`
      : `Send not confirmed: ${error.message}. Inspect the A/C before retrying.`;
    ui.result.textContent = "No automatic retry was made.";
  } finally {
    busy = false;
    updateMode();
  }
}

function renderCatalog(commands) {
  ui.controls.replaceChildren();
  ui["protocol-table"].replaceChildren();
  for (const command of commands) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent =
      command.name === "power_toggle" ? "Power toggle" : command.label;
    if (command.name.startsWith("temp_")) button.classList.add("primary");
    button.addEventListener("click", () => sendCommand(command));
    ui.controls.append(button);
    const row = document.createElement("tr");
    const values = [
      command.name,
      command.linux_scancode || "See waveform preview",
      command.label,
    ];
    values.forEach((value, index) => {
      const cell = document.createElement("td");
      const content = document.createElement(index < 2 ? "code" : "span");
      content.textContent = value;
      cell.append(content);
      row.append(cell);
    });
    ui["protocol-table"].append(row);
  }
}

async function loadController() {
  try {
    const [status, catalog] = await Promise.all([
      request("/api/ac/status"),
      request("/api/ac/commands"),
    ]);
    hardwareEnabled = status.hardware_enabled === true;
    renderCatalog(catalog.commands);
    ui["server-status"].textContent = hardwareEnabled
      ? "Pi transmitter mode enabled. This page still starts in preview. A/C state: unknown."
      : "Pi transmitter mode disabled. Previews work now; complete hardware setup before real sends.";
    updateMode();
  } catch (error) {
    ui["mode-status"].textContent = "API UNAVAILABLE";
    ui["server-status"].textContent =
      `Could not load the controller: ${error.message}. Reload to retry.`;
    ui["protocol-table"].textContent =
      "Command catalog unavailable. Start the guide server to view it.";
  }
}
ui.token.addEventListener("input", updateMode);
ui["real-send"].addEventListener("change", updateMode);
loadController();
