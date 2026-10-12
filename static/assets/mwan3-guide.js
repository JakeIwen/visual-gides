/* Illustrations and downloadable commands only; this guide has no router API. */
"use strict";

const byId = id => document.getElementById(id);
let announcementTimer;
const scenarios = {
  available: [60, "Both links receive new connections. Existing sessions stay where they started."],
  congested: [85, "More new connections favor A. Sessions already on B remain there; B keeps an exploration share."],
  offline: [100, "Once mwan3 removes B from the active policy, new connections use A. Existing sessions on B may fail."]
};

function updateSetup() {
  const all = byId("all-policies").checked;
  const name = byId("policy").value.trim();
  const valid = all || /^[A-Za-z0-9_]{1,15}$/.test(name);
  byId("policy").disabled = all;
  byId("policy").setAttribute("aria-invalid", String(!valid));
  byId("policy-error").textContent = valid ? "" : "Use 1–15 ASCII letters, digits or underscores.";
  document.querySelector('[data-download="observe"]').disabled = !valid;
  if (!valid) {
    byId("observe").textContent = "Enter a valid policy name above.";
    return;
  }
  const commands = [
    "uci set mwan3-adaptive.main.enabled='1'",
    "uci set mwan3-adaptive.main.mode='observe'",
    "(uci -q delete mwan3-adaptive.main.policy || true)"
  ];
  if (!all) commands.push(`uci add_list mwan3-adaptive.main.policy='${name}'`);
  commands.push(
    "uci commit mwan3-adaptive",
    "/etc/init.d/mwan3-adaptive stop",
    "/usr/sbin/mwan3-adaptive check",
    "/etc/init.d/mwan3-adaptive enable",
    "/etc/init.d/mwan3-adaptive start"
  );
  byId("observe").textContent = commands.join(" &&\n");
}

function downloadScript(id) {
  const contents = "#!/bin/sh\nset -eu\n" + byId(id).textContent.trim() + "\n";
  const name = `mwan3-${id}.sh`;
  const url = URL.createObjectURL(new Blob([contents], {type: "text/x-shellscript"}));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  byId("announcement").textContent = `Download prepared: ${name}. Review and run it in the shell indicated above its commands.`;
  clearTimeout(announcementTimer);
  announcementTimer = setTimeout(() => { byId("announcement").textContent = ""; }, 7000);
}

function setScenario(name) {
  const [a, note] = scenarios[name];
  byId("share-a").style.width = `${a}%`;
  byId("share-a").textContent = `A · ${a}%`;
  byId("share-b").style.width = `${100 - a}%`;
  byId("share-b").textContent = a === 100 ? "" : `B · ${100 - a}%`;
  byId("allocation").setAttribute("aria-label", `Illustration: ${a} percent link A, ${100 - a} percent link B`);
  byId("scenario-note").textContent = note;
  document.querySelectorAll("[data-scenario]").forEach(button => {
    button.setAttribute("aria-pressed", String(button.dataset.scenario === name));
  });
  document.querySelectorAll(".sessions .b").forEach(session => {
    session.classList.toggle("failed", name === "offline");
  });
}

document.querySelectorAll("[data-download]").forEach(button => {
  button.addEventListener("click", () => downloadScript(button.dataset.download));
});
document.querySelectorAll("[data-scenario]").forEach(button => {
  button.addEventListener("click", () => setScenario(button.dataset.scenario));
});
byId("policy").addEventListener("input", updateSetup);
byId("all-policies").addEventListener("change", updateSetup);
byId("print-guide").addEventListener("click", () => {
  document.querySelectorAll("details").forEach(detail => { detail.open = true; });
  window.print();
});
updateSetup();
