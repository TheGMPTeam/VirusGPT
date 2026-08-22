/* services_ui.js — Connected Services panel inside the Settings modal.
 *
 * Pulls the registry from GET /api/services, then for each service renders:
 *   - a header with live connection status (GET /api/services/{name}/status)
 *   - editable settings (base_url / enabled / timeout) saved via POST .../settings
 *   - the tool list with a Run button (read-only tools run immediately;
 *     confirm-flagged tools ask for confirmation before POST .../tools/run)
 *
 * Secrets (api_key/token) are never shown or sent back from the client — the
 * server masks them and ignores them on write.
 */

const SERVICE_LABELS = {
  n8n: "n8n", comfyui: "ComfyUI",
  youtube: "YouTube", gmail: "Gmail", snapchat: "Snapchat",
};

function _serviceLabel(name) { return SERVICE_LABELS[name] || name; }

async function loadServicesPanel() {
  const list = document.getElementById("st-services-list");
  if (!list) return;
  list.innerHTML = '<span class="st-muted">loading…</span>';
  let idx;
  try {
    const r = await fetch("api/services", { cache: "no-store" });
    idx = await r.json();
  } catch (e) {
    list.innerHTML = '<span class="st-muted">failed to load services</span>';
    return;
  }
  const names = Object.keys(idx || {});
  if (!names.length) {
    list.innerHTML = '<span class="st-muted">no services registered</span>';
    return;
  }
  list.innerHTML = "";
  for (const name of names) {
    list.appendChild(await buildServiceCard(name));
  }
}

async function buildServiceCard(name) {
  const card = document.createElement("div");
  card.className = "st-svc-card";
  card.dataset.svc = name;

  // header
  const head = document.createElement("div");
  head.className = "st-svc-head";
  const title = document.createElement("span");
  title.className = "st-svc-title";
  title.textContent = _serviceLabel(name);
  const status = document.createElement("span");
  status.className = "st-svc-status";
  status.textContent = "…";
  head.appendChild(title);
  head.appendChild(status);
  card.appendChild(head);

  // body (settings + tools) — built async
  const body = document.createElement("div");
  body.className = "st-svc-body";
  card.appendChild(body);

  // fetch settings + status + tools in parallel
  let settings = {}, st = {}, tools = { tools: [] };
  try {
    const [rs, rstat, rt] = await Promise.all([
      fetch(`api/services/${name}/settings`, { cache: "no-store" }),
      fetch(`api/services/${name}/status`, { cache: "no-store" }),
      fetch(`api/services/${name}/tools`, { cache: "no-store" }),
    ]);
    settings = await rs.json();
    st = await rstat.json();
    tools = await rt.json();
  } catch (e) { /* keep defaults */ }

  // status pill
  const connected = (st && (st.connected !== undefined ? st.connected : st.healthy));
  if (connected) { status.classList.add("ok"); status.textContent = "● connected"; }
  else if (st && st.enabled) { status.classList.add("warn"); status.textContent = "○ enabled, not connected"; }
  else { status.classList.add("off"); status.textContent = "○ off"; }

  // editable settings (skip secret/internal keys)
  const skip = new Set(["_service", "api_key", "api_token", "token", "secret", "password"]);
  const editable = ["base_url", "enabled", "timeout", "default_model", "connection_id"];
  const fields = document.createElement("div");
  fields.className = "st-svc-fields";
  for (const key of editable) {
    if (!(key in settings)) continue;
    const row = document.createElement("div");
    row.className = "st-svc-field";
    const lab = document.createElement("label");
    lab.textContent = key;
    const inp = document.createElement(key === "enabled" ? "input" : "input");
    if (key === "enabled") {
      inp.type = "checkbox";
      inp.checked = !!settings[key];
    } else if (key === "timeout") {
      inp.type = "number";
      inp.value = settings[key] ?? "";
    } else {
      inp.type = "text";
      inp.value = settings[key] ?? "";
    }
    inp.dataset.key = key;
    row.appendChild(lab);
    row.appendChild(inp);
    fields.appendChild(row);
  }
  if (fields.childElementCount) body.appendChild(fields);

  // save button
  const saveBtn = document.createElement("button");
  saveBtn.className = "btn-accent st-svc-save";
  saveBtn.textContent = "Save settings";
  saveBtn.onclick = async () => {
    const patch = {};
    fields.querySelectorAll("input[data-key]").forEach(inp => {
      const k = inp.dataset.key;
      if (k === "enabled") patch[k] = inp.checked;
      else if (k === "timeout") patch[k] = inp.value === "" ? null : Number(inp.value);
      else patch[k] = inp.value;
    });
    saveBtn.disabled = true;
    saveBtn.textContent = "saving…";
    try {
      const r = await fetch(`api/services/${name}/settings`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      const d = await r.json();
      if (d && d.error) alert(`${_serviceLabel(name)}: ${d.error}`);
      else { saveBtn.textContent = "✓ saved"; }
    } catch (e) {
      saveBtn.textContent = "save failed";
    } finally {
      setTimeout(() => { saveBtn.textContent = "Save settings"; saveBtn.disabled = false; }, 1200);
    }
  };
  body.appendChild(saveBtn);

  // tools
  const toolList = (tools && tools.tools) || [];
  if (toolList.length) {
    const tWrap = document.createElement("div");
    tWrap.className = "st-svc-tools";
    const tTitle = document.createElement("div");
    tTitle.className = "st-svc-tools-title";
    tTitle.textContent = "Tools";
    tWrap.appendChild(tTitle);
    for (const t of toolList) {
      const tb = document.createElement("button");
      tb.className = "st-svc-tool";
      tb.textContent = t.name + (t.confirm ? " ⚠" : "");
      tb.title = t.description || "";
      tb.onclick = async () => {
        if (t.confirm && !confirm(`"${t.name}" is an external action (${_serviceLabel(name)}). Run it?`)) return;
        tb.disabled = true; tb.textContent = t.name + " …";
        try {
          const r = await fetch(`api/services/${name}/tools/run`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ tool: t.name }),
          });
          const d = await r.json();
          tb.textContent = (t.name) + " → " + ((d && d.status) || r.status);
        } catch (e) {
          tb.textContent = t.name + " → error";
        } finally {
          setTimeout(() => { tb.textContent = t.name + (t.confirm ? " ⚠" : ""); tb.disabled = false; }, 1500);
        }
      };
      tWrap.appendChild(tb);
    }
    body.appendChild(tWrap);
  }

  return card;
}

function initServicesPanel() {
  const refresh = document.getElementById("st-services-refresh");
  if (refresh) refresh.onclick = loadServicesPanel;
  // Reload whenever the settings modal is opened.
  const openBtn = document.getElementById("btn-settings");
  if (openBtn) {
    const orig = openBtn.onclick;
    openBtn.onclick = () => { if (typeof orig === "function") orig(); loadServicesPanel(); };
  }
}
