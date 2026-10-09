/*
 * Window A/C guide behavior: saved checklists, copy buttons, the LED current
 * estimate, section highlighting and the preview-only command console.
 *
 * The console only ever POSTs PREVIEW_BODY. Real transmission is a deliberate
 * Terminal step in the guide, never a button on this page.
 */
(() => {
  "use strict";

  const PREVIEW_BODY = '{"preview":true}';
  const STORAGE_PREFIX = "vg.ac.";
  const SUPPLY_VOLTS = 5.0;

  // Console grouping and catalog notes are presentation only. Names missing
  // from the catalog are skipped; catalog names not listed here go to "Other".
  const GROUPS = [
    ["Temperature", ["temp_up", "temp_down"]],
    ["Power and mode", ["power_toggle", "mode_cool", "mode_fan", "energy_saver"]],
    ["Fan", ["fan_up", "fan_down", "fan_auto"]],
    ["Timers", ["sleep", "timer"]],
  ];
  const NOTES = {
    power_toggle: "Toggle only; no discrete on/off. Two sends undo each other.",
    mode_cool: "May also start the unit.",
    temp_up: "Relative step; no absolute setpoint.",
    temp_down: "Relative step; no absolute setpoint.",
    sleep: "Validate carefully; state isn't tracked.",
    timer: "Validate carefully; state isn't tracked.",
  };
  const FRAME_FIELDS = ["Address (low)", "Address (high)", "Command", "Inverse"];

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else node.setAttribute(key, value);
    }
    node.append(...children);
    return node;
  }

  function svgEl(tag, attrs = {}) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
    return node;
  }

  const codeEl = (text) => el("code", { text });

  // ---------- per-browser storage (may be unavailable) ----------

  const store = {
    read(name) {
      try {
        return JSON.parse(localStorage.getItem(STORAGE_PREFIX + name) || "{}") || {};
      } catch {
        return {};
      }
    },
    write(name, value) {
      try {
        localStorage.setItem(STORAGE_PREFIX + name, JSON.stringify(value));
      } catch {
        /* storage blocked: ticks just won't persist */
      }
    },
  };

  // ---------- checklists ----------

  function updateProgress(name, boxes) {
    const done = boxes.filter((box) => box.checked).length;
    for (const badge of $$(`[data-progress="${name}"]`)) badge.textContent = `${done}/${boxes.length}`;
    for (const box of boxes) {
      const step = box.closest(".step");
      if (step) step.classList.toggle("is-done", box.checked);
    }
  }

  function bindChecklist(list) {
    const name = list.dataset.checklist;
    const boxes = $$("input[type=checkbox][data-check]", list);
    const saved = store.read(name);
    for (const box of boxes) {
      box.checked = saved[box.dataset.check] === true;
      box.addEventListener("change", () => {
        const state = store.read(name);
        state[box.dataset.check] = box.checked;
        store.write(name, state);
        updateProgress(name, boxes);
      });
    }
    for (const button of $$(`[data-clear="${name}"]`)) {
      button.addEventListener("click", () => {
        for (const box of boxes) box.checked = false;
        store.write(name, {});
        updateProgress(name, boxes);
      });
    }
    updateProgress(name, boxes);
  }

  // ---------- copy buttons and origin-aware examples ----------

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    // Plain-HTTP LAN pages have no Clipboard API; fall back to a selection copy.
    return new Promise((resolve, reject) => {
      const area = el("textarea", { readonly: "", "aria-hidden": "true" });
      area.value = text;
      area.style.cssText = "position:fixed;top:0;left:0;opacity:0";
      document.body.append(area);
      area.select();
      const copied = document.execCommand("copy");
      area.remove();
      if (copied) resolve();
      else reject(new Error("copy failed"));
    });
  }

  function addCopyButtons() {
    for (const pre of $$("pre[data-copy]")) {
      const button = el("button", { type: "button", class: "copy", text: "Copy" });
      button.addEventListener("click", async () => {
        let label = "Copied";
        try {
          await copyText(pre.textContent.replace(/\n+$/, ""));
        } catch {
          label = "Select it";
        }
        button.textContent = label;
        setTimeout(() => { button.textContent = "Copy"; }, 1400);
      });
      pre.parentElement.append(button);
    }
  }

  function fillOrigin() {
    if (!/^https?:$/.test(location.protocol)) return;
    for (const span of $$(".origin")) span.textContent = location.origin;
  }

  // ---------- LED current estimate ----------

  function bindCurrentEstimate() {
    const form = $("#current-calc");
    if (!form) return;
    const format = (milliamps) =>
      Number.isFinite(milliamps) && milliamps >= 0 ? `${milliamps.toFixed(1)} mA` : "–";
    const update = () => {
      const ohms = Number(form.elements.r.value);
      const ledDrop = Number(form.elements.vf.value);
      const measured = form.elements.vr.value;
      $("#calc-est").textContent = format(((SUPPLY_VOLTS - ledDrop) / ohms) * 1000);
      $("#calc-meas").textContent = measured === "" ? "–" : format((Number(measured) / ohms) * 1000);
    };
    form.addEventListener("input", update);
    update();
  }

  // ---------- section highlighting ----------

  function bindScrollSpy() {
    const toc = $(".toc");
    if (!toc || !("IntersectionObserver" in window)) return;
    const links = new Map($$("a[href^='#']", toc).map((link) => [link.hash.slice(1), link]));
    const headings = [...links.keys()].map((id) => document.getElementById(id)).filter(Boolean);
    const activate = (id) => {
      for (const [key, link] of links) link.classList.toggle("active", key === id);
      const link = links.get(id);
      if (link && toc.scrollWidth > toc.clientWidth) {
        toc.scrollLeft = link.offsetLeft - (toc.clientWidth - link.offsetWidth) / 2;
      }
    };
    const observer = new IntersectionObserver(() => {
      const passed = headings.filter((h) => h.getBoundingClientRect().top < window.innerHeight * 0.35);
      activate((passed[passed.length - 1] || headings[0]).id);
    }, { rootMargin: "0px 0px -60% 0px", threshold: [0, 1] });
    for (const heading of headings) observer.observe(heading);
  }

  // ---------- NEC frame decoding ----------

  const hex2 = (n) => n.toString(16).toUpperCase().padStart(2, "0");

  function reverse8(n) {
    let out = 0;
    for (let i = 0; i < 8; i += 1) out |= ((n >> i) & 1) << (7 - i);
    return out;
  }

  // A 32-bit NEC frame is a leader pair, 32 mark/space bits and a final mark:
  // 67 durations. A long space is a 1. Bytes are sent LSB first.
  function decodeNec(durations) {
    if (!Array.isArray(durations) || durations.length !== 67) return null;
    const bits = [];
    for (let i = 0; i < 32; i += 1) {
      const mark = durations[2 + i * 2];
      const space = durations[3 + i * 2];
      bits.push(space > mark * 1.8 ? 1 : 0);
    }
    const bytes = [0, 1, 2, 3].map((k) =>
      bits.slice(k * 8, k * 8 + 8).reduce((acc, bit, j) => acc | (bit << j), 0));
    return {
      bits,
      bytes,
      inverseOk: (bytes[2] ^ bytes[3]) === 0xff,
      necx: `necx:0x${bytes.slice(0, 3).map(hex2).join("").toLowerCase()}`,
      lirc: bytes.map((b) => hex2(reverse8(b))).join(""),
    };
  }

  function frameSegments(durations, decoded) {
    const sum = (from, to) => durations.slice(from, to).reduce((a, b) => a + b, 0);
    if (!decoded) return [{ label: "raw", start: 0, end: durations.length, time: sum(0, durations.length) }];
    const segments = [{ label: "leader", start: 0, end: 2 }];
    ["addr", "addr", "cmd", "inv"].forEach((label, k) => {
      segments.push({ label, value: hex2(decoded.bytes[k]), start: 2 + k * 16, end: 18 + k * 16 });
    });
    segments.push({ label: "", start: 66, end: 67 });
    return segments.map((s) => ({ ...s, time: sum(s.start, s.end) }));
  }

  // ---------- waveform drawing ----------

  function renderWave(durations, decoded) {
    const total = durations.reduce((a, b) => a + b, 0);
    const scale = 1000 / total;
    const segments = frameSegments(durations, decoded);
    const svg = svgEl("svg", { viewBox: "0 0 1000 56", preserveAspectRatio: "none", "aria-hidden": "true" });
    let time = 0;
    segments.forEach((segment, index) => {
      svg.append(svgEl("rect", {
        x: time * scale, y: 0, width: segment.time * scale, height: 56,
        class: index % 2 ? "band-a" : "band-b",
      }));
      time += segment.time;
    });
    time = 0;
    durations.forEach((duration, index) => {
      if (index % 2 === 0) {
        svg.append(svgEl("rect", {
          x: time * scale, y: 8, width: Math.max(duration * scale, 0.9), height: 38, class: "mark",
        }));
      }
      time += duration;
    });
    svg.append(svgEl("line", { x1: 0, y1: 46, x2: 1000, y2: 46, class: "base", "vector-effect": "non-scaling-stroke" }));
    const labels = el("div", { class: "wave-labels" });
    for (const segment of segments) {
      const span = el("span", { title: `${segment.label} ${segment.value || ""}`.trim() });
      span.style.width = `${(segment.time / total) * 100}%`;
      span.append(segment.label ? `${segment.label} ` : "", segment.value ? el("b", { text: segment.value }) : "");
      labels.append(span);
    }
    return el("div", { class: "wave" }, svg, labels);
  }

  function renderDecodeTable(decoded) {
    const rows = FRAME_FIELDS.map((field, k) =>
      el("tr", {},
        el("td", { text: field }),
        el("td", { text: decoded.bits.slice(k * 8, k * 8 + 8).join("") }),
        el("td", { class: "num", text: hex2(decoded.bytes[k]) })));
    return el("div", { class: "table-wrap" },
      el("table", { class: "decode" },
        el("thead", {}, el("tr", {},
          el("th", { text: "Field" }), el("th", { text: "Bits, first sent → last" }), el("th", { class: "num", text: "Byte" }))),
        el("tbody", {}, ...rows)));
  }

  function renderChecks(command, decoded) {
    const line = el("p", { class: "checks-line" });
    if (!decoded) {
      line.append(el("span", { text: "Not a 32-bit NEC frame; showing raw timing only." }));
      return line;
    }
    const scancodeOk = decoded.necx === command.linux_scancode;
    line.append(
      el("span", { text: `Inverse check ${decoded.inverseOk ? "✓" : "✗"} (${hex2(decoded.bytes[2])} XOR ${hex2(decoded.bytes[3])} = ${hex2(decoded.bytes[2] ^ decoded.bytes[3])})` }),
      el("span", {}, "Linux ", codeEl(decoded.necx), scancodeOk ? " ✓ catalog" : ` ✗ catalog says ${command.linux_scancode}`),
      el("span", {}, "LIRC ", codeEl(decoded.lirc)));
    return line;
  }

  // ---------- console result panel ----------

  function resultChips(response) {
    const chips = [];
    if (!response) {
      chips.push(el("span", { class: "tag", text: "catalog only, no request sent" }));
      return chips;
    }
    const ok = response.status === 200;
    chips.push(el("span", { class: ok ? "tag ok" : "tag warn", text: `HTTP ${response.status}` }));
    if (response.body && response.body.delivery) chips.push(el("span", { class: "tag", text: `delivery: ${response.body.delivery}` }));
    if (response.body && "confirmed" in response.body) chips.push(el("span", { class: "tag", text: `confirmed: ${response.body.confirmed}` }));
    return chips;
  }

  function renderResult(command, response) {
    const panel = $("#result");
    const decoded = decodeNec(command.durations_us);
    const total = command.durations_us.reduce((a, b) => a + b, 0);
    const summary = `${command.pulse_count} durations · ${(total / 1000).toFixed(1)} ms · ${command.carrier_hz / 1000} kHz carrier · ${command.encoding}`;
    const children = [
      el("div", { class: "result-head" }, el("strong", { text: command.label }), codeEl(command.name), ...resultChips(response)),
      renderWave(command.durations_us, decoded),
      el("p", { class: "small", text: summary }),
      renderChecks(command, decoded),
    ];
    if (decoded) children.push(renderDecodeTable(decoded));
    const raw = response ? response.body : command;
    children.push(el("details", {},
      el("summary", { text: response ? "Raw API response" : "Raw catalog entry" }),
      el("pre", { text: JSON.stringify(raw, null, 2) })));
    if (!response) children.push(el("p", { class: "small", text: "Pick a command to request its preview from the API." }));
    panel.replaceChildren(...children);
  }

  function renderError(message) {
    $("#result").replaceChildren(el("p", { class: "small", text: message }));
  }

  // ---------- API calls ----------

  async function getJson(path) {
    const response = await fetch(path, { headers: { Accept: "application/json" }, cache: "no-store" });
    const body = await response.json().catch(() => null);
    if (!response.ok || !body) throw new Error(body && body.error ? body.error : `HTTP ${response.status}`);
    return body;
  }

  async function postPreview(name) {
    const response = await fetch(`/api/ac/commands/${encodeURIComponent(name)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: PREVIEW_BODY,
      cache: "no-store",
    });
    const body = await response.json().catch(() => null);
    return { status: response.status, body };
  }

  function setStatus(id, text, tone) {
    const node = document.getElementById(id);
    node.textContent = text;
    node.className = tone ? `is-${tone}` : "";
  }

  async function loadStatus() {
    try {
      const health = await getJson("/api/health");
      setStatus("st-api", health.ok ? "ok" : "not ok", health.ok ? "ok" : "bad");
    } catch (error) {
      setStatus("st-api", `unreachable (${error.message})`, "bad");
    }
    try {
      const status = await getJson("/api/ac/status");
      setStatus("st-hw", status.hardware_enabled ? "enabled on this server" : "off, preview only", status.hardware_enabled ? "bad" : "ok");
      setStatus("st-ac", status.ac_state || "unknown");
      document.getElementById("st-ac").title = status.state_note || "";
    } catch (error) {
      setStatus("st-hw", `unknown (${error.message})`, "bad");
    }
  }

  // ---------- console buttons ----------

  function groupCommands(commands) {
    const byName = new Map(commands.map((command) => [command.name, command]));
    const listed = new Set(GROUPS.flatMap(([, names]) => names));
    const groups = GROUPS
      .map(([title, names]) => [title, names.filter((name) => byName.has(name)).map((name) => byName.get(name))])
      .filter(([, members]) => members.length);
    const other = commands.filter((command) => !listed.has(command.name));
    if (other.length) groups.push(["Other", other]);
    return groups;
  }

  async function onPick(button, command, buttons) {
    for (const b of buttons) {
      b.setAttribute("aria-pressed", String(b === button));
      b.disabled = true;
    }
    try {
      const response = await postPreview(command.name);
      const waveform = response.body && response.body.waveform;
      if (response.status === 200 && waveform) renderResult(waveform, response);
      else renderError(`Preview refused: HTTP ${response.status} ${(response.body && response.body.error) || ""}`.trim());
    } catch (error) {
      renderError(`Preview request failed: ${error.message}`);
    } finally {
      for (const b of buttons) b.disabled = false;
    }
  }

  function renderButtons(commands) {
    const container = $("#cmd-groups");
    const buttons = [];
    const fieldsets = groupCommands(commands).map(([title, members]) => {
      const row = el("div", { class: "cmd-buttons" });
      for (const command of members) {
        const button = el("button", { type: "button", class: "cmd", "aria-pressed": "false", text: command.label, title: command.name });
        button.addEventListener("click", () => onPick(button, command, buttons));
        buttons.push(button);
        row.append(button);
      }
      return el("fieldset", {}, el("legend", { text: title }), row);
    });
    container.replaceChildren(...fieldsets);
  }

  // ---------- catalog table ----------

  function catalogRow(command) {
    const decoded = decodeNec(command.durations_us);
    const mismatch = decoded && decoded.necx !== command.linux_scancode;
    const linux = el("td", { "data-label": "Linux" }, codeEl(command.linux_scancode || "–"));
    if (mismatch) linux.append(el("small", { text: `waveform decodes as ${decoded.necx}` }));
    return el("tr", {},
      el("td", { class: "lead-cell", text: command.label }),
      el("td", { "data-label": "API name" }, codeEl(command.name)),
      el("td", { class: "num", "data-label": "Byte", text: decoded ? hex2(decoded.bytes[2]) : "?" }),
      linux,
      el("td", { "data-label": "LIRC" }, decoded ? codeEl(decoded.lirc) : "?"),
      el("td", { "data-label": "Notes", text: NOTES[command.name] || "" }));
  }

  function renderCatalog(commands) {
    $("#catalog-rows").replaceChildren(...commands.map(catalogRow));
  }

  async function loadConsole() {
    if (!$("#cmd-groups")) return;
    loadStatus();
    try {
      const { commands } = await getJson("/api/ac/commands");
      if (!Array.isArray(commands) || !commands.length) throw new Error("catalog is empty");
      renderButtons(commands);
      renderCatalog(commands);
      renderResult(commands.find((command) => command.name === "temp_up") || commands[0], null);
    } catch (error) {
      const message = `Couldn't load the command catalog: ${error.message}`;
      renderError(message);
      $("#catalog-rows").replaceChildren(el("tr", {}, el("td", { colspan: "6", text: message })));
    }
  }

  // ---------- start ----------

  for (const list of $$("[data-checklist]")) bindChecklist(list);
  addCopyButtons();
  fillOrigin();
  bindCurrentEstimate();
  bindScrollSpy();
  loadConsole();
})();
