const $ = (id) => document.getElementById(id);
const views = ["connect", "agent", "chat", "a2a", "tasks", "state", "logs", "settings"];
const tables = ["sessions", "entries", "a2a_tasks", "a2a_task_messages", "a2a_task_outputs", "a2a_task_attempts", "a2a_task_events", "snapshots"];
let config = { profiles: [], selectedProfile: "" };
let activeView = "connect";
let chatAbort = null;
let taskAbort = null;
let stateOffset = 0;
let stateTotal = 0;
let selectedRow = null;
let activeA2AProfile = "";
let activeChatProfile = "";
let settingsBaseline = new Map();
let selectedTaskId = "";
let selectedTaskData = null;
let taskLogOffset = 0;
let taskLogRecords = [];
let taskHasMore = false;
let logOffset = 0;
let logRecords = [];
let logHasMore = false;
let requestedLogSession = "";
let agentInspection = null;
let selectedTool = null;
let toolConfirmation = "";
let currentToolTest = "";
let toolTestAbort = null;
const a2aTaskState = new Map();
const catalogs = new Map();
const desiredSelection = { chat: null, a2a: null };

function clear(node) { node.replaceChildren(); }
function node(tag, value = "", className = "") {
  const item = document.createElement(tag);
  if (value) item.textContent = value;
  if (className) item.className = className;
  return item;
}
function notice(message, kind = "is-info") {
  const item = $("notice");
  item.textContent = message;
  item.className = `notification ${kind}`;
}
function dismissNotice() { $("notice").className = "notification is-hidden"; }
function errorMessage(data, status) {
  if (data && typeof data === "object") {
    const value = data.error || data.message || data.detail;
    if (value && typeof value === "object") return value.message || JSON.stringify(value);
    return value || `HTTP ${status}`;
  }
  return String(data || `HTTP ${status}`);
}
async function json(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(errorMessage(data, response.status));
  return data;
}
function profileByName(name) { return config.profiles.find((item) => item.name === name); }
function profileFor(kind) { return profileByName($(`${kind}-profile`).value); }
function tauHeaders(kind, extra = {}) {
  return { "X-Tau-Board-Profile": $(`${kind}-profile`).value, ...extra };
}
function option(value, label = value) {
  const item = node("option", label);
  item.value = value;
  return item;
}
function populateSelect(select, values, selected) {
  clear(select);
  for (const value of values) select.append(option(value));
  select.value = selected;
}
function renderProfiles() {
  for (const id of ["connect-select", "agent-profile", "chat-profile", "a2a-profile", "tasks-profile", "log-profile"]) {
    const select = $(id);
    const selected = id === "connect-select" ? config.selectedProfile : (select.value || config.selectedProfile);
    clear(select);
    for (const profile of config.profiles) {
      select.append(option(profile.name, `${profile.name} · ${profile.url}`));
    }
    select.value = profileByName(selected) ? selected : config.selectedProfile;
  }
  fillConnect();
  for (const kind of ["chat", "a2a"]) updateSelection(kind);
}
function fillConnect() {
  const profile = profileByName($("connect-select").value);
  if (!profile) return;
  $("profile-name").value = profile.name;
  $("profile-url").value = profile.url;
  $("profile-provider").value = profile.provider || "";
  $("profile-model").value = profile.model || "";
  $("profile-thinking").value = profile.thinking || "";
  $("profile-state-dir").value = profile.stateDir || "";
}
function updateSelection(kind) {
  const profile = profileFor(kind);
  if (!profile) return;
  const catalog = catalogs.get(profile.name);
  const choice = desiredSelection[kind] || { provider: profile.provider || "", model: profile.model || "", thinking: profile.thinking || "" };
  desiredSelection[kind] = choice;
  const providerSelect = $(`${kind}-provider`);
  clear(providerSelect);
  if (!catalog) providerSelect.append(option(choice.provider, choice.provider || "Catalog unavailable"));
  for (const item of catalog?.providers || []) {
    const available = item.enabled && item.authenticated;
    const entry = option(item.provider, `${item.display_name || item.provider}${available ? "" : " · unavailable"}`);
    entry.disabled = !available;
    providerSelect.append(entry);
  }
  providerSelect.value = choice.provider;
  if (!providerSelect.value && providerSelect.options.length) providerSelect.value = providerSelect.options[0].value;
  choice.provider = providerSelect.value;
  const provider = (catalog?.providers || []).find((item) => item.provider === choice.provider);
  const modelSelect = $(`${kind}-model`);
  clear(modelSelect);
  if (!catalog) modelSelect.append(option(choice.model, choice.model || "Current model"));
  for (const item of provider?.models || []) modelSelect.append(option(item.model, item.display_name || item.model));
  modelSelect.value = choice.model;
  if (!modelSelect.value && modelSelect.options.length) modelSelect.value = provider?.default_model || modelSelect.options[0].value;
  choice.model = modelSelect.value;
  const model = (provider?.models || []).find((item) => item.model === choice.model);
  const thinkingSelect = $(`${kind}-thinking`);
  clear(thinkingSelect);
  thinkingSelect.append(option("", "Default"));
  for (const level of model?.thinking_levels || []) thinkingSelect.append(option(level));
  thinkingSelect.value = choice.thinking || "";
  if (thinkingSelect.value !== (choice.thinking || "")) thinkingSelect.value = "";
  choice.thinking = thinkingSelect.value;
  if (kind === "chat") {
    if (activeChatProfile !== profile.name) {
      $("chat-session").value = sessionStorage.getItem(`tau-board-session:${profile.name}`) || "";
      activeChatProfile = profile.name;
    }
    $("chat-effective").textContent = `${profile.url} · selected ${choice.provider || "unknown"} / ${choice.model || "unknown"} / ${choice.thinking || "default thinking"}`;
  } else {
    $("a2a-effective").textContent = `${profile.url} · selected ${choice.provider || "unknown"} / ${choice.model || "unknown"} / ${choice.thinking || "default thinking"}`;
  }
  if (kind === "a2a" && activeA2AProfile !== profile.name) {
    $("a2a-context").value = "";
    $("a2a-task-id").value = "";
    $("task-output").textContent = "Choose an action to begin.";
    clear($("task-list"));
    activeA2AProfile = profile.name;
  }
}
async function loadCatalog(kind) {
  const profile = profileFor(kind);
  if (!profile) return;
  const catalog = await json("/tau/api/chat/model-providers", { headers: tauHeaders(kind) });
  catalogs.set(profile.name, catalog);
  desiredSelection[kind] = null;
  updateSelection(kind);
}
function selectionChanged(kind, field) {
  const choice = desiredSelection[kind] || { provider: "", model: "", thinking: "" };
  choice[field] = $(`${kind}-${field}`).value;
  if (field === "provider") { choice.model = ""; choice.thinking = ""; }
  if (field === "model") choice.thinking = "";
  desiredSelection[kind] = choice;
  updateSelection(kind);
}
async function saveConfig(profiles, selectedProfile) {
  config = await json("/api/board/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ profiles, selectedProfile }) });
  catalogs.clear();
  activeChatProfile = "";
  activeA2AProfile = "";
  desiredSelection.chat = null;
  desiredSelection.a2a = null;
  renderProfiles();
  await Promise.allSettled([loadCatalog("chat").catch(report), loadCatalog("a2a").catch(report)]);
}
async function selectProfile() {
  config = await json("/api/board/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ selectedProfile: $("connect-select").value }) });
  fillConnect();
  await checkConnection();
}
async function saveProfile() {
  const original = $("connect-select").value;
  const profile = {
    name: $("profile-name").value.trim(), url: $("profile-url").value.trim(),
    provider: $("profile-provider").value.trim(), model: $("profile-model").value.trim(),
    thinking: $("profile-thinking").value.trim(), stateDir: $("profile-state-dir").value.trim() || null,
  };
  if (!profile.name || !profile.url) throw new Error("A profile name and Tau URL are required.");
  const profiles = config.profiles.filter((item) => item.name !== original && item.name !== profile.name);
  profiles.push(profile);
  await saveConfig(profiles, profile.name);
  notice(`Saved ${profile.name}.`, "is-success");
  await checkConnection();
}
async function deleteProfile() {
  if (config.profiles.length <= 1) throw new Error("At least one endpoint profile is required.");
  const name = $("connect-select").value;
  const profiles = config.profiles.filter((item) => item.name !== name);
  await saveConfig(profiles, profiles[0].name);
  notice(`Deleted ${name}.`, "is-info");
}
function detailLine(parent, label, value) {
  const row = node("div");
  row.append(node("strong", label), node("span", String(value ?? "—")));
  parent.append(row);
}
async function checkConnection() {
  const name = $("connect-select").value;
  const result = await json(`/api/board/connection?profile=${encodeURIComponent(name)}`);
  const box = $("connection-detail"); clear(box);
  detailLine(box, "Endpoint", result.profile.url);
  detailLine(box, "Mode", result.health.mode);
  detailLine(box, "Version", result.health.version);
  detailLine(box, "Ready", result.ready ? "Yes" : "No");
  detailLine(box, "Workspace digest", result.health.workspace_digest);
  detailLine(box, "State directory", result.stateDir);
  detailLine(box, "MCP tools", result.health.mcp_tool_count);
  if (result.stateError) detailLine(box, "State warning", result.stateError);
  $("global-status").textContent = result.ready ? `Ready · ${name}` : `Connected · ${name}`;
  $("global-status").className = `tag ${result.ready ? "is-success" : "is-warning"} is-light`;
}
function showView(view) {
  activeView = views.includes(view) ? view : "connect";
  for (const item of views) {
    $(`view-${item}`).classList.toggle("is-hidden", item !== activeView);
    document.querySelector(`[data-nav="${item}"]`).classList.toggle("is-active", item === activeView);
  }
  if (activeView === "a2a") refreshTasks().catch(report);
  if (activeView === "agent") refreshAgentSessions().catch(report);
  if (activeView === "tasks") refreshTaskExplorer().catch(report);
  if (activeView === "state") refreshState().catch(report);
  if (activeView === "logs") refreshLogChoices().then(() => refreshLogs()).catch(report);
  if (activeView === "settings") refreshSettings().catch(report);
}
function agentHeaders(extra = {}) { return { "X-Tau-Board-Profile": $("agent-profile").value, ...extra }; }
function agentSession() { return $("agent-session").value; }
function invalidateToolConfirmation() {
  toolConfirmation = "";
  $("tool-run").disabled = true;
  clear($("tool-test-status"));
}
async function refreshAgentSessions() {
  const previous = agentSession() || $("chat-session").value.trim() || $("a2a-context").value.trim();
  const result = await json("/api/board/sessions", { headers: agentHeaders() });
  const select = $("agent-session"); clear(select);
  select.append(option("", "Select a loaded local session"));
  for (const session of result.sessions || []) select.append(option(session.uid, `${session.uid} · ${session.model || "unknown model"}`));
  select.value = previous;
  if (!select.value && select.options.length === 2) select.selectedIndex = 1;
  if (select.value) await inspectAgent();
}
function renderAgentCard(card) {
  const box = $("agent-card"); clear(box);
  if (!card) { box.append(node("p", "No Agent Card was returned.", "help")); $("agent-card-json").textContent = "null"; return; }
  for (const [label, value] of [["Name", card.name], ["Description", card.description], ["Version", card.version], ["URL", card.url]]) detailLine(box, label, value);
  const capabilities = card.capabilities || {};
  detailLine(box, "Capabilities", Object.entries(capabilities).filter(([, value]) => typeof value === "boolean" && value).map(([name]) => name).join(", ") || "None declared");
  detailLine(box, "Protocol extensions", (capabilities.extensions || []).map((item) => item.uri || "unnamed").join(", ") || "None");
  detailLine(box, "Agent Card skills", (card.skills || []).map((item) => item.name || item.id || "unnamed").join(", ") || "None");
  $("agent-card-json").textContent = JSON.stringify(card, null, 2);
}
function renderExtensions(items, diagnostics) {
  const box = $("agent-extensions"); clear(box);
  for (const item of items || []) {
    const card = node("div", "", "extension-card");
    card.append(node("strong", item.name), node("span", ` ${item.origin}`, "tag is-light ml-2"));
    card.append(node("p", item.entryPath || "Source is not exposed", "mono"));
    card.append(node("p", `Tools: ${(item.tools || []).join(", ") || "none"}`, "help")); box.append(card);
  }
  for (const item of diagnostics || []) {
    const card = node("div", "", "extension-card has-text-danger");
    card.textContent = `${item.severity} · ${item.name} · ${item.errorType}${item.path ? ` · ${item.path}` : ""}`; box.append(card);
  }
  if (!(items || []).length && !(diagnostics || []).length) box.append(node("p", "No project extensions or diagnostics.", "help"));
}
function renderTools(tools) {
  const box = $("agent-tools"); clear(box);
  const labels = { project_extension: "Project extensions", tau_coding: "Tau coding", mainsequence_mcp: "Main Sequence MCP", protocol: "A2A Task controls", user_extension: "User extensions", built_in_extension: "Tau extensions", explicit_extension: "Explicit extensions", unknown_extension: "Other extensions" };
  const groups = new Map();
  for (const tool of tools || []) {
    if (!groups.has(tool.category)) groups.set(tool.category, []);
    groups.get(tool.category).push(tool);
  }
  const order = ["project_extension", "tau_coding", "mainsequence_mcp", "protocol", ...groups.keys()];
  for (const category of [...new Set(order)]) {
    if (!groups.has(category)) continue;
    box.append(node("p", labels[category] || category, "tool-group"));
    for (const tool of groups.get(category)) {
      const button = node("button", "", "button is-light"); button.type = "button";
      button.append(node("strong", tool.label || tool.name), node("span", `\n${tool.name}`, "mono help"));
      button.addEventListener("click", () => selectTool(tool, button)); box.append(button);
    }
  }
  if (!(tools || []).length) box.append(node("p", "This session has no executable tools.", "help"));
}
function defaultFieldValue(schema) {
  if (Object.hasOwn(schema, "default")) return schema.default;
  if (Array.isArray(schema.examples) && schema.examples.length) return schema.examples[0];
  return "";
}
function renderToolForm(schema) {
  const form = $("tool-form"); clear(form);
  const required = new Set(schema.required || []);
  for (const [name, field] of Object.entries(schema.properties || {})) {
    const wrapper = node("div", "", "field tool-field");
    wrapper.append(node("label", `${name}${required.has(name) ? " *" : ""}`, "label"));
    let input;
    if (Array.isArray(field.enum)) {
      const control = node("div", "", "select is-fullwidth"); input = node("select");
      if (!required.has(name)) input.append(option("", "Not set"));
      for (const value of field.enum) input.append(option(JSON.stringify(value), String(value)));
      control.append(input); wrapper.append(control); input.dataset.encoding = "json";
    } else if (field.type === "boolean") {
      const control = node("div", "", "select is-fullwidth"); input = node("select");
      if (!required.has(name)) input.append(option("", "Not set"));
      input.append(option("true", "True"), option("false", "False")); control.append(input); wrapper.append(control); input.dataset.encoding = "json";
    } else if (field.type === "object" || field.type === "array") {
      input = node("textarea", "", "textarea mono"); input.rows = 3; input.dataset.encoding = "json"; wrapper.append(input);
    } else {
      input = node("input", "", "input"); input.type = field.type === "number" || field.type === "integer" ? "number" : "text";
      if (field.type === "integer") input.step = "1"; input.dataset.encoding = field.type || "string"; wrapper.append(input);
    }
    input.dataset.toolField = name; input.dataset.required = required.has(name) ? "true" : "false";
    const initial = defaultFieldValue(field);
    if (initial !== "") input.value = input.dataset.encoding === "json" ? JSON.stringify(initial) : String(initial);
    if (field.description) wrapper.append(node("p", field.description, "help"));
    input.addEventListener("input", () => { invalidateToolConfirmation(); try { $("tool-arguments").value = JSON.stringify(formArguments(), null, 2); } catch (_) { /* raw editor remains available */ } });
    form.append(wrapper);
  }
  if (!Object.keys(schema.properties || {}).length) form.append(node("p", "This tool has no declared fields. Use the JSON editor for additional properties.", "help mb-3"));
  try { $("tool-arguments").value = JSON.stringify(formArguments(), null, 2); } catch (_) { $("tool-arguments").value = "{}"; }
}
function formArguments() {
  const result = {};
  for (const input of document.querySelectorAll("[data-tool-field]")) {
    if (input.value === "" && input.dataset.required !== "true") continue;
    let value = input.value;
    if (input.dataset.encoding === "json") value = JSON.parse(value || "null");
    else if (input.dataset.encoding === "number") value = Number(value);
    else if (input.dataset.encoding === "integer") value = Number.parseInt(value, 10);
    result[input.dataset.toolField] = value;
  }
  return result;
}
function selectTool(tool, button) {
  selectedTool = tool; invalidateToolConfirmation(); currentToolTest = "";
  for (const item of $("agent-tools").querySelectorAll("button")) item.classList.toggle("is-selected", item === button);
  $("tool-title").textContent = tool.label || tool.name;
  $("tool-description").textContent = tool.description || "No description.";
  const meta = $("tool-meta"); clear(meta);
  for (const value of [tool.name, tool.category, tool.executionMode, tool.extension]) if (value) meta.append(node("span", value, "tag is-light"));
  $("tool-schema").textContent = JSON.stringify(tool.parameters, null, 2);
  $("tool-source").disabled = !tool.sourceUid; $("tool-source-output").classList.add("is-hidden");
  renderToolForm(tool.parameters || { type: "object", properties: {} });
  $("tool-validate").disabled = !tool.testable;
  $("tool-confirm").checked = false; $("tool-cancel").disabled = true;
  $("tool-output").textContent = tool.testable ? "Fill the arguments, then validate before running." : (tool.testingReason || "This tool is inspect-only.");
}
async function inspectAgent() {
  const session = agentSession();
  if (!session) throw new Error("Select a local session to inspect.");
  agentInspection = await json(`/tau/api/local/v1/sessions/${encodeURIComponent(session)}/agent-inspection`, { headers: agentHeaders() });
  if (!agentInspection.available) {
    clear($("agent-tools")); clear($("agent-card")); clear($("agent-extensions"));
    $("agent-overview").textContent = "Session is not loaded. Send a Chat or A2A message, then inspect again."; return;
  }
  const overview = $("agent-overview"); clear(overview);
  for (const [label, value] of [["Agent", agentInspection.agentUid], ["Session", agentInspection.sessionUid], ["Provider", agentInspection.model?.provider], ["Model", agentInspection.model?.model], ["Catalog", agentInspection.catalogDigest], ["State", agentInspection.busy ? "Busy" : "Idle"]]) detailLine(overview, label, value);
  renderAgentCard(agentInspection.agentCard); renderExtensions(agentInspection.extensions, agentInspection.diagnostics); renderTools(agentInspection.tools);
}
async function viewToolSource() {
  if (!selectedTool?.sourceUid) return;
  const result = await json(`/tau/api/local/v1/sessions/${encodeURIComponent(agentSession())}/extension-sources/${selectedTool.sourceUid}`, { headers: agentHeaders() });
  const output = $("tool-source-output"); output.textContent = `${result.path}\n\n${result.content}`; output.classList.remove("is-hidden");
}
function rawToolArguments() {
  const value = JSON.parse($("tool-arguments").value || "{}");
  if (!value || Array.isArray(value) || typeof value !== "object") throw new Error("Tool arguments must be a JSON object.");
  return value;
}
async function validateTool() {
  if (!selectedTool?.testable) throw new Error("Select a testable project tool.");
  const body = { catalogDigest: agentInspection.catalogDigest, arguments: rawToolArguments() };
  const result = await json(`/tau/api/local/v1/sessions/${encodeURIComponent(agentSession())}/tools/${encodeURIComponent(selectedTool.name)}:validate`, { method: "POST", headers: agentHeaders({ "Content-Type": "application/json" }), body: JSON.stringify(body) });
  toolConfirmation = result.confirmation; $("tool-arguments").value = JSON.stringify(result.arguments, null, 2);
  const status = $("tool-test-status"); clear(status); status.append(node("span", `Validated · confirmation expires in ${result.expiresInSeconds}s`, "tag is-success is-light"));
  $("tool-run").disabled = !$("tool-confirm").checked;
}
async function runTool() {
  if (!toolConfirmation || !$("tool-confirm").checked) throw new Error("Validate the arguments and confirm the side-effect warning first.");
  const body = { catalogDigest: agentInspection.catalogDigest, arguments: rawToolArguments(), confirmation: toolConfirmation };
  toolConfirmation = ""; $("tool-run").disabled = true; $("tool-validate").disabled = true; $("tool-output").textContent = "Starting tool…";
  toolTestAbort = new AbortController();
  try {
    const response = await fetch(`/tau/api/local/v1/sessions/${encodeURIComponent(agentSession())}/tools/${encodeURIComponent(selectedTool.name)}:test`, { method: "POST", headers: agentHeaders({ "Content-Type": "application/json" }), body: JSON.stringify(body), signal: toolTestAbort.signal });
    if (!response.ok) throw new Error(errorMessage(await response.json().catch(() => ({})), response.status));
    currentToolTest = response.headers.get("X-Tau-Tool-Test-Uid") || ""; $("tool-cancel").disabled = !currentToolTest;
    await readSSE(response, (event) => {
      if (event.type === "started") $("tool-output").textContent = `Running ${event.toolName}…`;
      if (event.type === "update") $("tool-output").textContent += `\n\nUpdate:\n${JSON.stringify(event.result, null, 2)}`;
      if (event.type === "completed") $("tool-output").textContent = `${event.truncated ? "TRUNCATED\n" : ""}${JSON.stringify(event.result, null, 2)}`;
      if (event.type === "error") $("tool-output").textContent = `${event.errorType || event.error}: ${event.message}`;
      if (event.type === "cancelled") $("tool-output").textContent += "\n\nCancelled.";
      if (event.type === "finished") { const status = $("tool-test-status"); clear(status); status.append(node("span", event.outcome, `tag ${event.outcome === "completed" ? "is-success" : "is-warning"} is-light`)); }
    });
  } finally {
    currentToolTest = ""; toolTestAbort = null; $("tool-cancel").disabled = true; $("tool-validate").disabled = !selectedTool?.testable; $("tool-confirm").checked = false;
  }
}
async function cancelTool() {
  if (!currentToolTest) return;
  await json(`/tau/api/local/v1/sessions/${encodeURIComponent(agentSession())}/tool-tests/${encodeURIComponent(currentToolTest)}:cancel`, { method: "POST", headers: agentHeaders({ "Content-Type": "application/json" }), body: "{}" });
}
function report(error) { notice(error.message || String(error), "is-danger"); }
function chatLine(who, type = "agent") {
  const item = node("div", "", `chat-line ${type}`);
  const label = node("span", who, "who");
  const body = node("span");
  item.append(label, body);
  $("chat-messages").append(item);
  $("chat-messages").scrollTop = $("chat-messages").scrollHeight;
  return body;
}
async function readSSE(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      buffer = buffer.replaceAll("\r\n", "\n");
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const text = frame.split("\n").filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n");
        if (!text || text === "[DONE]") continue;
        try { await onEvent(JSON.parse(text)); } catch (error) { if (!(error instanceof SyntaxError)) throw error; }
      }
    }
  } finally { reader.releaseLock(); }
}
async function verifySelection(kind, sessionUid, allowMissing = false) {
  if (!sessionUid) return;
  const response = await fetch(`/tau/api/chat/session-model?sessionUid=${encodeURIComponent(sessionUid)}`, { headers: tauHeaders(kind) });
  if (allowMissing && response.status === 404) return;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(errorMessage(data, response.status));
  const effective = data.model || {};
  $(`${kind}-effective`).textContent = `Tau confirmed ${effective.provider} / ${effective.model} / ${effective.thinkingLevel || "default"}`;
}
async function applyModelSelection(kind, sessionUid) {
  const choice = desiredSelection[kind];
  if (!choice?.provider || !choice?.model) return sessionUid;
  const provider = (catalogs.get(profileFor(kind)?.name)?.providers || []).find((item) => item.provider === choice.provider);
  if (!provider?.enabled || !provider?.authenticated) throw new Error("Selected provider is unavailable. Check its backend credential status.");
  if (!(provider.models || []).some((item) => item.model === choice.model)) throw new Error("Selected model is not in the backend catalog.");
  const current = await fetch(`/tau/api/chat/session-model?sessionUid=${encodeURIComponent(sessionUid)}`, { headers: tauHeaders(kind) });
  if (current.ok) {
    const data = await current.json();
    if (data.model?.provider === choice.provider && data.model?.model === choice.model && (data.model?.thinkingLevel || "") === choice.thinking) {
      $(`${kind}-effective`).textContent = `Tau confirmed ${choice.provider} / ${choice.model} / ${choice.thinking || "default"}`;
      return data.sessionUid || sessionUid;
    }
  } else if (current.status !== 404) throw new Error(errorMessage(await current.json().catch(() => ({})), current.status));
  const data = await json("/tau/api/chat/session-model", { method: "PUT", headers: tauHeaders(kind, { "Content-Type": "application/json" }), body: JSON.stringify({ sessionUid, provider: choice.provider, model: choice.model, thinkingLevel: choice.thinking || null }) });
  $(`${kind}-effective`).textContent = `Tau confirmed ${data.model.provider} / ${data.model.model} / ${data.model.thinkingLevel || "default"}`;
  return data.sessionUid || sessionUid;
}
async function sendChat(event) {
  event.preventDefault(); dismissNotice();
  const message = $("chat-input").value.trim();
  if (!message) return;
  const profile = profileFor("chat");
  const existingUid = $("chat-session").value.trim();
  const sessionUid = await applyModelSelection("chat", existingUid || `board-chat-${crypto.randomUUID()}`);
  $("chat-session").value = sessionUid;
  sessionStorage.setItem(`tau-board-session:${profile.name}`, sessionUid);
  chatLine("You", "user").textContent = message;
  const answer = chatLine("Tau");
  $("chat-input").value = "";
  $("chat-progress").textContent = "Working";
  chatAbort = new AbortController();
  try {
    const response = await fetch("/tau/api/chat", { method: "POST", headers: tauHeaders("chat", { "Content-Type": "application/json" }), body: JSON.stringify({ sessionUid, message }), signal: chatAbort.signal });
    if (!response.ok) throw new Error(errorMessage(await response.json().catch(() => ({})), response.status));
    const effectiveUid = response.headers.get("X-Agent-Session-Uid") || sessionUid;
    $("chat-session").value = effectiveUid;
    sessionStorage.setItem(`tau-board-session:${profile.name}`, effectiveUid);
    await readSSE(response, (frame) => {
      if (frame.type === "text-delta") answer.textContent += frame.textDelta || "";
      if (frame.type === "tool-call-start") chatLine(`Tool · ${frame.toolName || "unknown"}`, "meta").textContent = frame.toolCallId || "";
      if (frame.type === "tool-result") chatLine("Tool result", "meta").textContent = JSON.stringify(frame.result ?? "").slice(0, 1200);
      if (frame.type === "error") notice(frame.errorText || "Tau reported an error", "is-danger");
      $("chat-messages").scrollTop = $("chat-messages").scrollHeight;
    });
    await verifySelection("chat", effectiveUid);
  } catch (error) { if (error.name !== "AbortError") report(error); }
  finally { chatAbort = null; $("chat-progress").textContent = "Idle"; }
}
async function cancelChat() {
  const sessionUid = $("chat-session").value.trim();
  if (!sessionUid) throw new Error("No chat session is selected.");
  await json("/tau/api/chat/session/cancel", { method: "POST", headers: tauHeaders("chat", { "Content-Type": "application/json" }), body: JSON.stringify({ sessionUid }) });
  chatAbort?.abort();
  notice("Cancellation requested.", "is-warning");
}
function taskText(task) {
  const parts = (task.artifacts || []).flatMap((artifact) => artifact.parts || []);
  return parts.map((part) => part.text ?? (part.data ? JSON.stringify(part.data, null, 2) : "")).filter(Boolean).join("\n");
}
const terminalTaskStates = new Set([
  "TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED",
]);
function taskTiming(task) {
  const local = a2aTaskState.get(task.id) || {};
  const statusTime = task.status?.timestamp || local.status_timestamp || "";
  const state = task.status?.state || "TASK_STATE_UNKNOWN";
  return {
    created: local.created_at || "",
    statusTime,
    terminal: terminalTaskStates.has(state),
    completed: state === "TASK_STATE_COMPLETED",
  };
}
function displayTime(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString();
}
function timedCell(value, label = "") {
  const cell = node("td", label ? `${label}: ${displayTime(value)}` : displayTime(value));
  if (value) { cell.classList.add("mono"); cell.title = value; }
  return cell;
}
function displayTask(task) {
  if (!task) return;
  $("a2a-task-id").value = task.id || "";
  $("a2a-context").value = task.contextId || "";
  const timing = taskTiming(task);
  const statusLabel = timing.completed ? "Completed" : (timing.terminal ? "Terminal status" : "Status recorded");
  const statusText = (task.status?.message?.parts || []).map((part) => part.text || "").filter(Boolean).join("\n");
  $("task-output").textContent = `${task.id} · ${task.status?.state || "unknown"}\nCreated: ${displayTime(timing.created)}\n${statusLabel}: ${displayTime(timing.statusTime)}${statusText ? `\nReason: ${statusText}` : ""}\n\n${taskText(task)}`;
}
function handleA2AFrame(frame) {
  const value = frame.result || frame;
  if (value.task) displayTask(value.task);
  if (value.statusUpdate) $("task-output").textContent += `\n${value.statusUpdate.status?.state || "status changed"}`;
  if (value.artifactUpdate) {
    const text = (value.artifactUpdate.artifact?.parts || []).map((part) => part.text || (part.data ? JSON.stringify(part.data) : "")).join("");
    if (text) $("task-output").textContent += text;
  }
  if (value.error) notice(value.error.message || "A2A stream error", "is-danger");
}
async function sendA2A(event) {
  event.preventDefault(); dismissNotice();
  const kind = $("a2a-kind").value;
  const text = $("a2a-input").value.trim();
  if (!text) return;
  const existingContext = $("a2a-context").value.trim();
  const contextId = await applyModelSelection("a2a", existingContext || `board-${crypto.randomUUID()}`);
  $("a2a-context").value = contextId;
  const isTask = kind === "task" || kind === "continue";
  const taskId = $("a2a-task-id").value.trim() || `board-task-${crypto.randomUUID()}`;
  const message = { messageId: crypto.randomUUID(), contextId, parts: [{ text }] };
  const body = { message };
  if (kind === "continue") {
    if (!$("a2a-task-id").value.trim()) throw new Error("Choose a Task to continue.");
    message.taskId = taskId;
  } else if (kind === "task" || kind === "stream") body.taskId = taskId;
  if (isTask) body.configuration = { responseKind: "task", returnImmediately: true };
  const headers = tauHeaders("a2a", { "Content-Type": "application/json" });
  if (isTask) headers["A2A-Extensions"] = "https://mainsequence.ai/a2a/extensions/response-kind/v1";
  const path = kind === "stream" ? "/tau/api/a2a/v1/message:stream" : "/tau/api/a2a/v1/message:send";
  $("task-output").textContent = "Sending…\n";
  taskAbort = new AbortController();
  try {
    const response = await fetch(path, { method: "POST", headers, body: JSON.stringify(body), signal: taskAbort.signal });
    if (!response.ok) throw new Error(errorMessage(await response.json().catch(() => ({})), response.status));
    if (kind === "stream") {
      await readSSE(response, handleA2AFrame);
    } else {
      const value = await response.json();
      if (value.task) { displayTask(value.task); if (value.task.status?.state?.includes("WORKING") || value.task.status?.state?.includes("SUBMITTED")) subscribeTask(value.task.id).catch(report); }
      if (value.message) $("task-output").textContent = (value.message.parts || []).map((part) => part.text || JSON.stringify(part.data || "")).join("\n");
    }
    $("a2a-input").value = "";
    await refreshTasks();
    await verifySelection("a2a", contextId);
  } catch (error) { if (error.name !== "AbortError") report(error); }
  finally { taskAbort = null; }
}
async function getTask(id) {
  const value = await json(`/tau/api/a2a/v1/tasks/${encodeURIComponent(id)}`, { headers: tauHeaders("a2a") });
  displayTask(value.task);
  return value.task;
}
async function subscribeTask(id) {
  const response = await fetch(`/tau/api/a2a/v1/tasks/${encodeURIComponent(id)}:subscribe`, { headers: tauHeaders("a2a") });
  if (!response.ok) { await getTask(id); return; }
  await readSSE(response, handleA2AFrame);
  await getTask(id);
  await refreshTasks();
}
async function cancelTask(id) {
  const value = await json(`/tau/api/a2a/v1/tasks/${encodeURIComponent(id)}:cancel`, { method: "POST", headers: tauHeaders("a2a") });
  displayTask(value.task); await refreshTasks();
}
async function refreshTasks() {
  const contextId = $("a2a-context").value.trim();
  const url = "/tau/api/a2a/v1/tasks" + (contextId ? `?contextId=${encodeURIComponent(contextId)}` : "");
  const headers = tauHeaders("a2a");
  const localUrl = "/api/board/tasks" + (contextId ? `?sessionUid=${encodeURIComponent(contextId)}` : "");
  const [value, localValue] = await Promise.all([
    json(url, { headers }),
    json(localUrl, { headers }),
  ]);
  a2aTaskState.clear();
  for (const task of localValue.tasks || []) a2aTaskState.set(task.task_id, task);
  const body = $("task-list"); clear(body);
  for (const task of (value.tasks || []).slice(-100).reverse()) {
    const row = node("tr");
    const timing = taskTiming(task);
    const statusLabel = timing.completed ? "Completed" : (timing.terminal ? "Terminal" : "Status");
    row.append(
      node("td", task.id, "mono"),
      node("td", task.contextId || "", "mono"),
      node("td", task.status?.state || ""),
      timedCell(timing.created),
      timedCell(timing.statusTime, statusLabel),
    );
    const actions = node("td");
    for (const [label, action] of [["Open", () => getTask(task.id)], ["Inspect", () => { $("tasks-profile").value = $("a2a-profile").value; selectedTaskId = task.id; showView("tasks"); }], ["Follow", () => subscribeTask(task.id)], ["Cancel", () => cancelTask(task.id)], ["Continue", () => {
      $("a2a-kind").value = "continue"; $("a2a-task-id").value = task.id; $("a2a-context").value = task.contextId || ""; $("a2a-input").focus();
    }]]) {
      const button = node("button", label, "button is-small is-light mr-1 mb-1");
      button.addEventListener("click", () => Promise.resolve(action()).catch(report));
      actions.append(button);
    }
    row.append(actions); body.append(row);
  }
  if (!(value.tasks || []).length) { const row = node("tr"); const cell = node("td", "No Tasks in this context yet."); cell.colSpan = 6; row.append(cell); body.append(row); }
  const currentId = $("a2a-task-id").value.trim();
  const current = (value.tasks || []).find((task) => task.id === currentId);
  if (current) displayTask(current);
}
async function refreshState() {
  const result = await json("/api/board/state");
  $("state-path").textContent = result.directory || "";
  if (result.warning) notice(result.warning, "is-warning");
  const counts = $("state-counts"); clear(counts);
  if (!result.available) { counts.append(node("span", "SQLite store has not been created yet.", "tag is-warning is-light")); return; }
  for (const [table, count] of Object.entries(result.tables || {})) counts.append(node("span", `${table}: ${count}`, "tag is-light"));
  const select = $("state-table");
  if (!select.options.length) for (const table of tables) select.append(option(table));
  await loadStateRows();
}
async function loadStateRows() {
  const table = $("state-table").value || tables[0];
  const result = await json(`/api/board/state/${table}?limit=50&offset=${stateOffset}`);
  stateTotal = result.total || 0;
  $("state-page").textContent = `${stateTotal ? stateOffset + 1 : 0}–${Math.min(stateOffset + 50, stateTotal)} of ${stateTotal}`;
  const head = $("state-head"), body = $("state-body"); clear(head); clear(body);
  const rows = result.rows || [];
  if (!rows.length) { const tr = node("tr"); const td = node("td", "No rows."); tr.append(td); body.append(tr); return; }
  const keys = Object.keys(rows[0]);
  const header = node("tr");
  for (const key of keys) header.append(node("th", key));
  header.append(node("th", "")); head.append(header);
  for (const row of rows) {
    const tr = node("tr");
    for (const key of keys) tr.append(node("td", String(row[key] ?? "")));
    const td = node("td"); const button = node("button", "Inspect", "button is-small is-light");
    button.addEventListener("click", () => inspectRow(table, row.board_rowid).catch(report));
    td.append(button); tr.append(td); body.append(tr);
  }
}
async function inspectRow(table, rowid) {
  const result = await json(`/api/board/state/${table}/${rowid}`);
  selectedRow = result;
  $("state-json").textContent = JSON.stringify(result.row, null, 2);
  $("state-detail").open = true;
  $("state-use-session").disabled = !["sessions", "entries"].includes(table);
  $("state-use-task").disabled = table !== "a2a_tasks";
  $("state-detail").scrollIntoView({ behavior: "smooth", block: "nearest" });
}
function useStateSession() {
  if (!selectedRow) return;
  $("chat-profile").value = config.selectedProfile;
  updateSelection("chat");
  const uid = selectedRow.row.session_uid || selectedRow.row.uid;
  $("chat-session").value = uid || "";
  showView("chat");
}
async function useStateTask() {
  if (!selectedRow) return;
  $("tasks-profile").value = config.selectedProfile;
  selectedTaskId = selectedRow.row.task_id;
  showView("tasks");
}

async function refreshTaskExplorer() {
  const currentSession = $("tasks-session").value;
  const headers = { "X-Tau-Board-Profile": $("tasks-profile").value };
  const sessions = await json("/api/board/sessions", { headers });
  const sessionSelect = $("tasks-session");
  clear(sessionSelect);
  sessionSelect.append(option("", "All sessions"));
  for (const item of sessions.sessions || []) {
    sessionSelect.append(option(item.uid, `${item.uid} · ${item.model}`));
  }
  sessionSelect.value = currentSession;
  const query = new URLSearchParams({ sessionUid: sessionSelect.value });
  const result = await json(`/api/board/tasks?${query}`, { headers });
  const list = $("tasks-list"); clear(list);
  for (const task of result.tasks || []) {
    const threshold = task.status === "submitted" ? result.policy?.pending_timeout_seconds : result.policy?.stale_after_seconds;
    const recorded = task.status === "submitted" ? task.created_at : task.status_timestamp;
    const ageSeconds = recorded ? (Date.now() - new Date(recorded).valueOf()) / 1000 : 0;
    const stale = ["submitted", "working"].includes(task.status) && threshold && ageSeconds > threshold;
    const suffix = task.failure_code ? `\n${task.failure_code}` : (stale ? "\nRecovery overdue" : "");
    const button = node("button", `${task.task_id} · ${task.status}\n${task.context_id}${suffix}`, stale ? "button is-warning" : "button is-light");
    button.type = "button";
    button.classList.toggle("is-selected", selectedTaskId === task.task_id);
    button.addEventListener("click", () => inspectTask(task.task_id).catch(report));
    list.append(button);
  }
  if (!(result.tasks || []).length) list.append(node("p", sessionSelect.value ? "No Tasks for this session." : "No Tasks yet.", "help"));
  if (selectedTaskId) await inspectTask(selectedTaskId);
}

function taskEventItem(label, when, title, detail, status = "") {
  const item = node("details", "", "log-item");
  const summary = node("summary");
  summary.append(node("span", when || "", "mono"), node("span", label, "tag is-light"));
  if (status) summary.append(node("span", status, "tag is-info is-light"));
  summary.append(node("span", title || "event", "event"));
  item.append(summary, node("pre", JSON.stringify(detail, null, 2)));
  return item;
}

function renderTaskTimeline() {
  const list = $("tasks-timeline"); clear(list);
  const task = selectedTaskData?.task;
  if (!task) return;
  const rows = [];
  for (const event of selectedTaskData.events || []) {
    rows.push({ when: event.created_at, label: "Task", title: event.event_type, detail: event, status: event.status });
  }
  for (const attempt of selectedTaskData.attempts || []) {
    rows.push({ when: attempt.created_at, label: "Attempt", title: `Attempt ${attempt.attempt_number} · ${attempt.state}`, detail: attempt });
  }
  for (const record of taskLogRecords) {
    const taskFields = [record.a2a_task_id, record.task_id, record.task_uid];
    const label = taskFields.includes(task.task_id) || taskFields.includes(task.uid) ? "Task log" : "Session log";
    rows.push({ when: record.timestamp, label, title: record.event || record.message, detail: record, status: record.outcome || record.level });
  }
  rows.sort((a, b) => String(a.when || "").localeCompare(String(b.when || "")));
  for (const row of rows) list.append(taskEventItem(row.label, row.when, row.title, row.detail, row.status));
  if (!rows.length) list.append(node("p", "No events for this Task yet.", "help"));
  $("tasks-older").classList.toggle("is-hidden", !taskHasMore);
}

async function loadTaskLogs(reset = false) {
  if (!selectedTaskId) return;
  if (reset) { taskLogOffset = 0; taskLogRecords = []; }
  const result = await json(`/api/board/tasks/${encodeURIComponent(selectedTaskId)}/logs?offset=${taskLogOffset}`, { headers: { "X-Tau-Board-Profile": $("tasks-profile").value } });
  taskLogRecords.push(...(result.records || []));
  taskLogOffset += (result.records || []).length;
  taskHasMore = Boolean(result.hasMore);
  renderTaskTimeline();
}

function renderTaskConversation() {
  const list = $("tasks-conversation"); clear(list);
  for (const record of selectedTaskData?.messages || []) {
    const message = record.message || {};
    const card = node("article", "", "task-card");
    const head = node("div", "", "task-card-head");
    const role = message.role === "ROLE_REQUESTER" ? "Requester" : (message.role === "ROLE_RESPONDER" ? "Responder" : (message.role || "Message"));
    head.append(node("strong", role), node("span", `#${record.sequence}`, "tag is-light"), node("span", displayTime(record.created_at), "mono"));
    card.append(head);
    for (const part of message.parts || []) {
      if (typeof part.text === "string") card.append(node("pre", part.text));
      else if (Object.hasOwn(part, "data")) card.append(node("pre", JSON.stringify(part.data, null, 2)));
      else card.append(node("pre", JSON.stringify(part, null, 2)));
    }
    list.append(card);
  }
  if (!(selectedTaskData?.messages || []).length) list.append(node("p", "No durable Task Messages.", "help"));
}

function renderTaskResults() {
  const list = $("tasks-result"); clear(list);
  for (const artifact of selectedTaskData?.outputs || []) {
    const card = node("article", "", "task-card");
    const head = node("div", "", "task-card-head");
    head.append(node("strong", artifact.name || artifact.artifact_id), node("span", `revision ${artifact.revision}`, "tag is-light"), node("span", artifact.finalized ? "Final" : "Open", artifact.finalized ? "tag is-success is-light" : "tag is-warning is-light"), node("span", `${artifact.byte_size || 0} bytes`, "tag is-light"));
    card.append(head);
    if (artifact.text) card.append(node("pre", artifact.text));
    for (const part of artifact.parts || []) {
      if (!Object.hasOwn(part, "data")) continue;
      card.append(node("pre", JSON.stringify(part.data, null, 2)));
    }
    list.append(card);
  }
  if (!(selectedTaskData?.outputs || []).length) list.append(node("p", "No Task Artifacts.", "help"));
}

function executionLabel(entry) {
  const raw = JSON.stringify(entry.entry || {}).toLowerCase();
  if (raw.includes("tool_result") || raw.includes("toolresult")) return "Tool result";
  if (raw.includes("tool_call") || raw.includes("toolcall")) return "Tool call";
  if (raw.includes("reasoning") || raw.includes("thinking")) return "Recorded reasoning";
  if (entry.entry_type === "message") return "Model message";
  return entry.entry_type || "Tau entry";
}

function executionOutcome(entry) {
  const value = entry.entry || {};
  const raw = JSON.stringify(value).toLowerCase();
  if (raw.includes('"is_error":true') || raw.includes('"error":true') || raw.includes('"status":"failed"')) return "Failed";
  if (raw.includes('"status":"completed"') || raw.includes('"status":"success"') || raw.includes('"success":true')) return "Succeeded";
  return "";
}

function renderTaskExecution() {
  const list = $("tasks-execution"); clear(list);
  const entries = selectedTaskData?.executionEntries || [];
  for (const attempt of selectedTaskData?.attempts || []) {
    const card = node("article", "", "task-card");
    const head = node("div", "", "task-card-head");
    head.append(node("strong", `Attempt ${attempt.attempt_number}`), node("span", attempt.state, "tag is-info is-light"), node("span", attempt.turn_resolution || "unresolved", attempt.turn_resolution === "committed" ? "tag is-success is-light" : "tag is-warning is-light"));
    card.append(head);
    detailLine(card, "Turn", attempt.turn_uid || "—");
    detailLine(card, "Entry interval", attempt.entry_start_sequence == null ? "—" : `[${attempt.entry_start_sequence}, ${attempt.entry_end_sequence ?? "?"})`);
    const attemptEntries = entries.filter((entry) => entry.turn_uid && entry.turn_uid === attempt.turn_uid && (attempt.entry_start_sequence == null || entry.sequence >= attempt.entry_start_sequence) && (attempt.entry_end_sequence == null || entry.sequence < attempt.entry_end_sequence));
    for (const entry of attemptEntries) card.append(taskEventItem("Tau", `#${entry.sequence}`, executionLabel(entry), entry, executionOutcome(entry)));
    if (!attemptEntries.length) card.append(node("p", "No retained entries in this turn interval.", "help"));
    list.append(card);
  }
  if (!(selectedTaskData?.attempts || []).length) list.append(node("p", "No execution attempts.", "help"));
}

async function inspectTask(taskId) {
  selectedTaskId = taskId;
  selectedTaskData = await json(`/api/board/tasks/${encodeURIComponent(taskId)}`, { headers: { "X-Tau-Board-Profile": $("tasks-profile").value } });
  const task = selectedTaskData.task;
  const detail = $("tasks-detail"); clear(detail);
  const failure = task.failure || {};
  const runtime = task.runtime || {};
  const duration = task.created_at && (task.terminal_at || task.status_timestamp) ? Math.max(0, new Date(task.terminal_at || task.status_timestamp).valueOf() - new Date(task.created_at).valueOf()) : null;
  for (const [label, value] of [["Task ID", task.task_id], ["Session", task.context_id], ["Status", task.status], ["Created", displayTime(task.created_at)], ["Status time", displayTime(task.status_timestamp)], ["Terminal time", displayTime(task.terminal_at)], ["Duration", duration == null || Number.isNaN(duration) ? "—" : `${duration} ms`], ["Provider", runtime.provider || "—"], ["Model", runtime.model || "—"], ["Thinking", runtime.thinking || "—"], ["Failure", failure.message || "—"], ["Failure code", failure.code || "—"], ["Failure category", failure.category || "—"], ["Retry safe", failure.retryable == null ? "—" : String(failure.retryable)], ["Recovery owner", task.recovery_owner || "—"], ["Recovery attempts", task.recovery_count || 0], ["Correlation ID", failure.correlationId || "—"], ["Attempts", selectedTaskData.attempts.length], ["Messages", selectedTaskData.messages.length], ["Artifacts", selectedTaskData.outputs.length]]) detailLine(detail, label, value);
  renderTaskConversation();
  renderTaskResults();
  renderTaskExecution();
  $("tasks-open-a2a").disabled = false;
  $("tasks-open-session").disabled = false;
  for (const button of $("tasks-list").querySelectorAll("button")) button.classList.toggle("is-selected", button.textContent.startsWith(`${taskId} ·`));
  await loadTaskLogs(true);
}

function openTaskInA2A() {
  const task = selectedTaskData?.task;
  if (!task) return;
  $("a2a-profile").value = $("tasks-profile").value;
  updateSelection("a2a");
  showView("a2a");
  $("a2a-task-id").value = task.task_id;
  $("a2a-context").value = task.context_id;
  getTask(task.task_id).catch(report);
}

function openTaskSessionLogs() {
  const task = selectedTaskData?.task;
  if (!task) return;
  requestedLogSession = task.context_id;
  $("log-profile").value = $("tasks-profile").value;
  showView("logs");
}
async function refreshLogChoices() {
  const currentSession = requestedLogSession || $("log-session").value;
  const headers = { "X-Tau-Board-Profile": $("log-profile").value };
  const sessions = await json("/api/board/sessions", { headers });
  const select = $("log-session"); clear(select);
  for (const item of sessions.sessions || []) select.append(option(item.uid, `${item.uid} · ${item.model}`));
  select.value = currentSession;
  if (!select.value && select.options.length) select.value = select.options[0].value;
  requestedLogSession = "";
  const currentTask = $("log-task").value;
  const tasks = await json(`/api/board/tasks?sessionUid=${encodeURIComponent(select.value)}`, { headers });
  const taskSelect = $("log-task"); clear(taskSelect);
  taskSelect.append(option("", "All in session"));
  for (const item of tasks.tasks || []) taskSelect.append(option(item.task_id));
  taskSelect.value = currentTask;
}
async function refreshLogs(reset = true) {
  if (reset) { logOffset = 0; logRecords = []; }
  const session = $("log-session").value;
  if (!session) { clear($("log-list")); $("log-list").append(node("p", "Select a session to view its events.", "help")); return; }
  const query = new URLSearchParams({ limit: "100", offset: String(logOffset), level: $("log-level").value, session, task: $("log-task").value, event: $("log-event").value.trim() });
  const result = await json(`/api/board/logs?${query}`, { headers: { "X-Tau-Board-Profile": $("log-profile").value } });
  $("logs-path").textContent = `${result.directory}/logs/tau.jsonl`;
  if (result.warning) notice(result.warning, "is-warning");
  logRecords.push(...(result.records || []));
  logOffset += (result.records || []).length;
  logHasMore = Boolean(result.hasMore);
  const list = $("log-list"); clear(list);
  for (const record of logRecords) {
    const item = node("details", "", "log-item");
    const summary = node("summary");
    summary.append(node("span", record.timestamp || "", "mono"), node("span", record.level || "", `tag is-light ${record.level === "error" ? "is-danger" : ""}`), node("span", record.event || record.message || "event", "event"));
    if (record.outcome) summary.append(node("span", record.outcome, "tag is-info is-light"));
    if (record.duration_ms != null) summary.append(node("span", `${record.duration_ms} ms`, "mono"));
    item.append(summary, node("pre", JSON.stringify(record, null, 2))); list.append(item);
  }
  if (!logRecords.length) list.append(node("p", "No matching events for this session.", "help"));
  $("logs-older").classList.toggle("is-hidden", !logHasMore);
}
async function refreshSettings() {
  const result = await json("/api/board/settings");
  const profile = result.selectedProfile;
  const effective = $("settings-effective"); clear(effective);
  detailLine(effective, "Selected profile", profile.name);
  detailLine(effective, "Tau endpoint", profile.url);
  detailLine(effective, "Effective state directory", result.effectiveStateDir || "Connect Tau to derive it");
  detailLine(effective, "Board URL", `http://127.0.0.1:${result.boardPort}`);
  const backend = result.environment.find((item) => item.name === "MAINSEQUENCE_ENDPOINT");
  detailLine(effective, "Backend hint from board environment", backend?.value || "Not set in board environment");
  $("settings-board-url").value = profile.url;
  $("settings-board-dir").value = profile.stateDir || "";
  $("settings-env-path").textContent = `${result.envFile} · ${result.envFileExists ? "existing file" : "will be created when saved"}`;
  const body = $("settings-env-rows"); clear(body);
  settingsBaseline = new Map();
  let group = "";
  for (const item of result.environment) {
    if (item.group !== group) {
      group = item.group;
      const heading = node("tr", "", "settings-group");
      const cell = node("th", group); cell.colSpan = 3; heading.append(cell); body.append(heading);
    }
    const saved = result.envFileValues[item.name];
    const original = saved?.redacted ? null : (saved?.value || "");
    settingsBaseline.set(item.name, original);
    const row = node("tr");
    const label = node("td");
    label.append(node("strong", item.name, "mono"), node("span", item.usedBy, "settings-scope"), node("p", item.meaning, "help"));
    const running = node("td", item.value || "Not set", "mono settings-value");
    const cell = node("td");
    const input = node("input", "", "input is-small mono settings-input");
    input.type = "text"; input.maxLength = 512; input.dataset.envName = item.name;
    input.value = original || "";
    input.placeholder = saved?.redacted ? "Hidden; enter a replacement" : "Not set";
    cell.append(input);
    row.append(label, running, cell); body.append(row);
  }
  const credentials = $("settings-credentials"); clear(credentials);
  for (const item of result.credentials) {
    const processStatus = item.present ? "Present" : "Missing";
    const fileStatus = result.envFileCredentials[item.name] ? "Present" : "Missing";
    detailLine(credentials, item.name, `Board: ${processStatus} · .env: ${fileStatus}`);
  }
}
async function saveEnvironment() {
  const changes = {};
  for (const input of document.querySelectorAll(".settings-input")) {
    const name = input.dataset.envName;
    const before = settingsBaseline.get(name);
    const after = input.value.trim();
    if ((before === null && after) || (before !== null && after !== before)) changes[name] = after;
  }
  if (!Object.keys(changes).length) { notice("No .env changes to save.", "is-info"); return; }
  await json("/api/board/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ changes }) });
  await refreshSettings();
  notice("Saved .env. Restart Tau for Tau settings; restart Tau Board for its startup settings.", "is-success");
}
async function applyBoardSettings() {
  const current = profileByName(config.selectedProfile);
  const changed = { ...current, url: $("settings-board-url").value.trim(), stateDir: $("settings-board-dir").value.trim() || null };
  const profiles = config.profiles.map((profile) => profile.name === current.name ? changed : profile);
  await saveConfig(profiles, current.name);
  await refreshSettings();
  notice("Board session override applied to the selected profile.", "is-success");
}
function bind(id, event, action) { $(id).addEventListener(event, (...args) => Promise.resolve(action(...args)).catch(report)); }
async function start() {
  config = await json("/api/board/config");
  renderProfiles();
  for (const item of document.querySelectorAll("[data-nav]")) item.addEventListener("click", (event) => { event.preventDefault(); const view = item.dataset.nav; history.replaceState(null, "", `#${view}`); showView(view); });
  bind("connect-select", "change", selectProfile);
  bind("save-profile", "click", saveProfile);
  bind("delete-profile", "click", deleteProfile);
  bind("check-connection", "click", checkConnection);
  bind("agent-profile", "change", refreshAgentSessions);
  bind("agent-session", "change", inspectAgent);
  bind("agent-refresh", "click", inspectAgent);
  bind("tool-source", "click", viewToolSource);
  bind("tool-validate", "click", validateTool);
  bind("tool-run", "click", runTool);
  bind("tool-cancel", "click", cancelTool);
  bind("tool-confirm", "change", () => { $("tool-run").disabled = !toolConfirmation || !$("tool-confirm").checked; });
  $("tool-arguments").addEventListener("input", invalidateToolConfirmation);
  for (const kind of ["chat", "a2a"]) {
    bind(`${kind}-profile`, "change", () => loadCatalog(kind));
    for (const field of ["provider", "model", "thinking"]) bind(`${kind}-${field}`, "change", () => selectionChanged(kind, field));
  }
  await Promise.allSettled([loadCatalog("chat").catch(report), loadCatalog("a2a").catch(report)]);
  bind("chat-form", "submit", sendChat);
  bind("chat-cancel", "click", cancelChat);
  bind("a2a-form", "submit", sendA2A);
  bind("a2a-new-task", "click", () => { $("a2a-task-id").value = ""; $("a2a-kind").value = "task"; $("a2a-input").focus(); });
  bind("refresh-tasks", "click", refreshTasks);
  bind("tasks-refresh", "click", refreshTaskExplorer);
  bind("tasks-profile", "change", () => { selectedTaskId = ""; selectedTaskData = null; for (const id of ["tasks-detail", "tasks-conversation", "tasks-result", "tasks-execution", "tasks-timeline"]) clear($(id)); return refreshTaskExplorer(); });
  bind("tasks-session", "change", refreshTaskExplorer);
  bind("tasks-open-id", "click", () => inspectTask($("tasks-id").value.trim()));
  bind("tasks-older", "click", () => loadTaskLogs());
  bind("tasks-open-a2a", "click", openTaskInA2A);
  bind("tasks-open-session", "click", openTaskSessionLogs);
  bind("refresh-state", "click", refreshState);
  bind("state-table", "change", () => { stateOffset = 0; return loadStateRows(); });
  bind("state-prev", "click", () => { stateOffset = Math.max(0, stateOffset - 50); return loadStateRows(); });
  bind("state-next", "click", () => { if (stateOffset + 50 < stateTotal) stateOffset += 50; return loadStateRows(); });
  bind("state-use-session", "click", useStateSession);
  bind("state-use-task", "click", useStateTask);
  bind("refresh-logs", "click", () => refreshLogs());
  bind("log-profile", "change", async () => { await refreshLogChoices(); await refreshLogs(); });
  bind("logs-older", "click", () => refreshLogs(false));
  bind("settings-refresh", "click", refreshSettings);
  bind("settings-save-env", "click", saveEnvironment);
  bind("settings-apply-board", "click", applyBoardSettings);
  bind("log-session", "change", async () => { await refreshLogChoices(); await refreshLogs(); });
  for (const id of ["log-level", "log-task", "log-event"]) bind(id, "change", () => refreshLogs());
  showView(location.hash.slice(1) || "connect");
  checkConnection().catch((error) => { $("global-status").textContent = "Tau unavailable"; $("global-status").className = "tag is-danger is-light"; report(error); });
  setInterval(() => { if (activeView === "logs" && logOffset <= 100) refreshLogs().catch(report); }, 5000);
}
start().catch(report);
