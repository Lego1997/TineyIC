/*
 * TinyIC Persona Studio — zero-build ES2020, no framework.
 * Persona identity constants are hand-synced from app.js / persona_style.py.
 * Markdown enters the DOM only through window.TinyICMarkdown (escape-first).
 */
(function () {
  "use strict";

  // Hand-synced from app.js PERSONA_IDENTITIES (test-pinned via persona_style.py).
  const PERSONA_IDENTITIES = Object.freeze({
    "Warren Buffett": { monogram: "WB", className: "persona-warren-buffett" },
    "Charlie Munger": { monogram: "CM", className: "persona-charlie-munger" },
    "Benjamin Graham": { monogram: "BG", className: "persona-benjamin-graham" },
    "Peter Lynch": { monogram: "PL", className: "persona-peter-lynch" },
    "Howard Marks": { monogram: "HM", className: "persona-howard-marks" },
    "Li Lu": { monogram: "LL", className: "persona-li-lu" },
  });

  const $ = (selector) => document.querySelector(selector);

  function hasOwn(record, key) {
    return Object.prototype.hasOwnProperty.call(record, key);
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  async function api(method, path, body) {
    const options = { method, headers: { Accept: "application/json" }, credentials: "same-origin" };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    const text = await response.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_err) { data = null; }
    if (!response.ok) {
      // Prefer the server's human-readable message; keep the reason code on
      // error.code so callers can still branch on it.
      const error = new Error(
        (data && (data.message || data.error)) || "http_" + response.status,
      );
      error.code = (data && data.error) || null;
      error.status = response.status;
      error.data = data || {};
      throw error;
    }
    return data;
  }

  let toastTimer = null;
  function toast(message) {
    // The live region stays in the accessibility tree (visibility via class,
    // not [hidden]) so screen readers announce the content change.
    const node = $("#toast");
    node.classList.add("toast--visible");
    node.textContent = message;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      node.classList.remove("toast--visible");
      node.textContent = "";
    }, 4000);
  }

  function confirmDialog(message) {
    return new Promise((resolve) => {
      const dialog = $("#confirm-dialog");
      $("#confirm-message").textContent = message;
      const settle = (value) => () => { dialog.close(); resolve(value); };
      $("#confirm-accept").onclick = settle(true);
      $("#confirm-cancel").onclick = settle(false);
      dialog.oncancel = settle(false);
      dialog.showModal();
    });
  }

  function duplicateDialog(slug) {
    return new Promise((resolve) => {
      const dialog = $("#duplicate-dialog");
      const input = $("#duplicate-slug");
      $("#duplicate-message").textContent = "Duplicate " + slug + " as\u2026";
      input.value = slug + "_v2";
      const accept = () => {
        const value = input.value.trim();
        if (!value) { input.focus(); return; }
        dialog.close();
        resolve(value);
      };
      const cancel = () => { dialog.close(); resolve(null); };
      $("#duplicate-accept").onclick = accept;
      $("#duplicate-cancel").onclick = cancel;
      input.onkeydown = (event) => {
        if (event.key === "Enter") { event.preventDefault(); accept(); }
      };
      dialog.oncancel = () => { resolve(null); };
      dialog.showModal();
      input.focus();
      input.select();
    });
  }

  function fallbackHash(name) {
    let hash = 2166136261;
    for (const character of name) {
      hash ^= character.codePointAt(0);
      hash = Math.imul(hash, 16777619);
    }
    return hash >>> 0;
  }

  function personaIdentity(name) {
    if (hasOwn(PERSONA_IDENTITIES, name)) {
      return PERSONA_IDENTITIES[name];
    }
    const words = String(name || "").trim().split(/\s+/).filter(Boolean);
    let monogram = "??";
    if (words.length === 1) {
      monogram = words[0].slice(0, 2).toUpperCase();
    } else if (words.length > 1) {
      monogram = (words[0][0] + words[words.length - 1][0]).toUpperCase();
    }
    return {
      monogram,
      className: "persona-fallback-" + (fallbackHash(String(name || "")) % 6),
    };
  }

  function monogramNode(name) {
    const identity = personaIdentity(name);
    return el("span", "monogram " + identity.className, identity.monogram);
  }

  // -- router --------------------------------------------------------------

  // Keys must match the "#/<key>" prefixes used across studio.js/studio.html
  // and the body[data-view] selectors in studio.css (contract-tested).
  const VIEWS = { library: renderLibrary, research: renderResearch, edit: renderEditor, committee: renderCommittee };

  let restoringHash = false;

  function route() {
    if (restoringHash) { restoringHash = false; return; }
    const rawHash = window.location.hash;
    // In-page anchors (e.g. the skip link's #studio-main) are not routes:
    // let the browser perform the jump without resetting the view.
    if (rawHash && !rawHash.startsWith("#/")) return;
    const hash = rawHash || "#/library";
    const parts = hash.replace(/^#\//, "").split("/");
    const view = VIEWS[parts[0]] ? parts[0] : "library";
    if (
      document.body.dataset.view === "edit"
      && editorState.dirty
      && (view !== "edit" || parts[1] !== editorState.slug)
    ) {
      // Put the editor hash back (without re-rendering) and ask first.
      restoringHash = true;
      window.location.hash = "#/edit/" + editorState.slug;
      confirmDialog("Discard unsaved changes to " + editorState.slug + "?").then((yes) => {
        if (yes) {
          editorState.dirty = false;
          window.location.hash = hash;
        }
      });
      return;
    }
    document.body.dataset.view = view;
    for (const link of document.querySelectorAll(".studio-nav a")) {
      if (link.dataset.nav === view) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
    VIEWS[view](parts[1]);
  }

  // -- library -------------------------------------------------------------

  async function renderLibrary() {
    const grid = $("#library-grid");
    grid.replaceChildren(el("li", "empty-note", "Loading\u2026"));
    let data;
    try {
      data = await api("GET", "/api/personas");
    } catch (error) {
      grid.replaceChildren(el("li", "empty-note", "Could not load personas: " + error.message));
      return;
    }
    const cards = data.personas.map(personaCard);
    grid.replaceChildren(...cards);
    $("#library-empty").hidden = cards.length > 0;
  }

  function personaCard(persona) {
    const card = el("li", "persona-card");
    const head = el("div", "persona-card__head");
    const title = el("div");
    title.append(el("div", "persona-card__name", persona.name));
    if (persona.temperament) title.append(el("div", "view-sub", persona.temperament));
    head.append(monogramNode(persona.name), title);

    const chips = el("div", "persona-card__chips");
    chips.append(el("span", "chip chip--origin-" + persona.origin.replace("_", "-"), persona.origin === "built_in" ? "Built-in" : "Custom"));
    if (persona.quality === "thin") chips.append(el("span", "chip chip--warn", "Thin sources"));
    if (persona.source_count) chips.append(el("span", "chip", persona.source_count + " sources"));

    const actions = el("div", "persona-card__actions");
    const open = el("a", "button", "Open");
    open.href = "#/edit/" + persona.slug;
    actions.append(open);
    const duplicate = el("button", "button", "Duplicate");
    duplicate.type = "button";
    duplicate.addEventListener("click", () => onDuplicate(persona.slug));
    actions.append(duplicate);
    if (persona.origin !== "built_in") {
      const remove = el("button", "button button--danger", "Delete");
      remove.type = "button";
      remove.addEventListener("click", () => onDelete(persona.slug, persona.name));
      actions.append(remove);
    }

    card.append(head);
    if (persona.epithet) card.append(el("p", "persona-card__epithet", persona.epithet));
    card.append(chips, actions);
    return card;
  }

  async function onDuplicate(slug) {
    const newSlug = await duplicateDialog(slug);
    if (!newSlug) return;
    try {
      const result = await api("POST", "/api/personas/" + slug + "/duplicate", { new_slug: newSlug });
      toast("Duplicated as " + result.slug);
      window.location.hash = "#/edit/" + result.slug;
    } catch (error) {
      toast("Duplicate failed: " + error.message);
    }
  }

  function deleteToast(result) {
    if (result.committee_updated) return "Deleted " + result.deleted + " (also removed from your default committee)";
    if (result.committee_cleared) return "Deleted " + result.deleted + " (default committee reset to the built-in six)";
    return "Deleted " + result.deleted;
  }

  async function onDelete(slug, name) {
    const yes = await confirmDialog("Delete " + name + " (" + slug + ")? Both artifact files are removed.");
    if (!yes) return;
    try {
      const result = await api("DELETE", "/api/personas/" + slug);
      toast(deleteToast(result));
      renderLibrary();
    } catch (error) {
      toast("Delete failed: " + error.message);
    }
  }

  // -- research wizard ----------------------------------------------------

  function detailRow(term, value) {
    const wrapper = document.createDocumentFragment();
    wrapper.append(el("dt", null, term), el("dd", null, value));
    return wrapper;
  }

  function money(value) {
    return "$" + Number(value).toFixed(4);
  }

  function researchErrorMessage(error) {
    if (error.code === "no_search_capable_lane") {
      return "No verified API-key search lane. Run 'tinyic onboard' to add an OpenAI, Grok, Google, or Kimi credential.";
    }
    if (error.code === "persona_exists") {
      return "That slug already exists — tick \u201cReplace the existing persona\u201d or choose another slug.";
    }
    if (error.code === "research_job_active") {
      return "A research job is already running — wait for it to finish before starting another.";
    }
    return error.message;
  }

  function researchSpec() {
    const spec = { investor_name: $("#research-name").value.trim() };
    const slug = $("#research-slug").value.trim();
    if (slug) spec.slug = slug;
    const model = $("#research-model").value.trim();
    if (model) spec.model = model;
    const max = $("#research-max-searches").value;
    if (max) spec.max_searches = Number(max);
    if ($("#research-force").checked) spec.force = true;
    return spec;
  }

  function showResearchError(message) {
    const node = $("#research-error");
    node.textContent = message;
    node.hidden = !message;
  }

  let estimatedSpec = null;

  async function onEstimate(event) {
    event.preventDefault();
    showResearchError("");
    $("#research-estimate-card").hidden = true;
    $("#research-progress").hidden = true;
    $("#research-result").hidden = true;
    const spec = researchSpec();
    const button = $("#research-estimate");
    button.disabled = true;
    let estimate;
    try {
      estimate = await api("POST", "/api/research/estimate", spec);
    } catch (error) {
      showResearchError(researchErrorMessage(error));
      return;
    } finally {
      button.disabled = false;
    }
    estimatedSpec = spec;
    $("#research-estimate-details").replaceChildren(
      detailRow("Slug", estimate.slug),
      detailRow("Lane", estimate.model_ref + " (" + estimate.provider + ")"),
      detailRow("Planned searches", String(estimate.estimate.planned_searches)),
      detailRow("Planned model calls", String(estimate.estimate.planned_calls)),
      detailRow("Search fees", money(estimate.estimate.search_fee_usd)),
      detailRow("Token cost", estimate.estimate.token_cost_usd === null ? "unavailable" : money(estimate.estimate.token_cost_usd)),
      detailRow("Estimated total", estimate.estimate.total_cost_usd === null ? "fee floor only" : money(estimate.estimate.total_cost_usd)),
    );
    $("#research-estimate-note").textContent =
      "Charged to your configured " + estimate.provider + " credential. Actual usage is reported when the run finishes.";
    $("#research-estimate-card").hidden = false;
    $("#research-confirm").onclick = () => onConfirmResearch();
  }

  async function onConfirmResearch() {
    if (!estimatedSpec) return;
    $("#research-confirm").disabled = true;
    try {
      // Confirm submits the SPEC THE ESTIMATE PRICED, not a re-read of the
      // form — editing a field hides the card and requires a new estimate.
      const spec = Object.assign({}, estimatedSpec);
      spec.confirmed = true;
      const started = await api("POST", "/api/research", spec);
      $("#research-estimate-card").hidden = true;
      followResearch(started.job_id);
    } catch (error) {
      showResearchError(researchErrorMessage(error));
    } finally {
      $("#research-confirm").disabled = false;
    }
  }

  let jobSource = null;

  function followResearch(jobId) {
    if (jobSource) { jobSource.close(); jobSource = null; }
    const stages = $("#research-stages");
    stages.replaceChildren();
    $("#research-progress").hidden = false;
    $("#research-result").hidden = true;
    const source = new EventSource("/api/research/" + jobId + "/events");
    jobSource = source;
    source.onmessage = (event) => {
      const envelope = JSON.parse(event.data);
      if (envelope.type === "stage") {
        const label = envelope.payload.angle
          ? "search · " + String(envelope.payload.angle).replace(/_/g, " ")
          : String(envelope.payload.stage).replace(/_/g, " ");
        stages.append(el("li", "stage", label));
      } else if (envelope.type === "job_completed") {
        source.close();
        jobSource = null;
        showResearchResult(envelope.payload);
      } else if (envelope.type === "job_error") {
        source.close();
        jobSource = null;
        $("#research-progress").hidden = true;
        showResearchError(envelope.payload.reason + ": " + envelope.payload.message);
      }
    };
    source.onerror = () => {
      if (!jobSource) return;
      // Transient drops auto-reconnect (the server resumes from
      // Last-Event-ID); only a permanently closed source is fatal.
      if (source.readyState !== EventSource.CLOSED) return;
      jobSource.close();
      jobSource = null;
      showResearchError("Lost the progress stream; check the library for the result.");
    };
  }

  function showResearchResult(payload) {
    const box = $("#research-result");
    const card = el("div", "estimate-card");
    card.append(el("h2", null, "Persona created"));
    const list = el("dl", "detail-list");
    list.append(
      detailRow("Slug", payload.slug),
      detailRow("Evidence", payload.source_count + " sources across " + payload.domain_count + " domains (" + payload.quality + ")"),
      detailRow("Usage", payload.usage.calls + " calls · " + payload.usage.search_calls + " searches" + (payload.usage.cost_usd === null ? "" : " · " + money(payload.usage.cost_usd))),
    );
    card.append(list);
    const open = el("a", "button button--primary", "Open in editor");
    open.href = "#/edit/" + payload.slug;
    card.append(open);
    box.replaceChildren(card);
    box.hidden = false;
  }

  function renderResearch() {
    const form = $("#research-form");
    form.onsubmit = onEstimate;
    form.oninput = () => {
      // The estimate no longer matches the form; require a fresh one.
      estimatedSpec = null;
      $("#research-estimate-card").hidden = true;
    };
    if (!jobSource) attachActiveResearch();
  }

  async function attachActiveResearch() {
    // After a reload, re-attach to a still-running job instead of going blind.
    let status;
    try {
      status = await api("GET", "/api/research");
    } catch (_error) {
      return;
    }
    if (status.job_id && !status.done) followResearch(status.job_id);
  }

  // -- editor ---------------------------------------------------------------

  const SECTIONS = [
    { title: "Identity", fields: [
      { path: ["persona", "name"], label: "Display name", kind: "text" },
      { path: ["tinyic", "epithet"], label: "Epithet", kind: "text", max: 80 },
      { path: ["tinyic", "temperament"], label: "Temperament", kind: "select", options: ["conciliatory", "balanced", "contrarian"] },
      { path: ["persona", "occupation", "description"], label: "Occupation description", kind: "text" },
    ]},
    { title: "Philosophy", fields: [
      { path: ["tinyic", "philosophy_hook"], label: "Philosophy hook", kind: "textarea", max: 240 },
      { path: ["tinyic", "decision_checklist"], label: "Decision checklist", kind: "list" },
      { path: ["tinyic", "signal_rules"], label: "Signal rules", kind: "list" },
      { path: ["tinyic", "red_flags"], label: "Red flags", kind: "list" },
    ]},
    { title: "Voice", fields: [
      { path: ["persona", "style"], label: "Style", kind: "textarea" },
      { path: ["persona", "personality", "traits"], label: "Traits", kind: "list" },
      { path: ["tinyic", "famous_quotes"], label: "Famous quotes", kind: "quotes" },
    ]},
    { title: "Agent detail", fields: [
      { path: ["persona", "beliefs"], label: "Beliefs", kind: "list" },
      { path: ["persona", "skills"], label: "Skills", kind: "list" },
      { path: ["persona", "behaviors", "general"], label: "Behaviors", kind: "list" },
      { path: ["persona", "other_facts"], label: "Other facts", kind: "list" },
    ]},
    { title: "Sources", fields: [
      { path: ["tinyic", "sources"], label: "Sources", kind: "sources" },
    ]},
  ];

  const QUOTE_COLUMNS = [
    { key: "text", placeholder: "Verbatim quote" },
    { key: "source", placeholder: "Source #", number: true },
  ];
  const SOURCE_COLUMNS = [
    { key: "title", placeholder: "Title" },
    { key: "url", placeholder: "URL" },
    { key: "type", placeholder: "primary | secondary" },
    { key: "accessed", placeholder: "YYYY-MM-DD" },
  ];

  const editorState = { slug: null, origin: null, agent: null, dossierDirty: false, dirty: false, mode: "structured" };

  function isGeneratedAgent(agent) {
    const generation = getPath(agent, ["tinyic", "generation"]);
    return generation !== undefined && generation !== null;
  }

  function getPath(obj, path) {
    return path.reduce((node, key) => (node == null ? node : node[key]), obj);
  }

  function setPath(obj, path, value) {
    let node = obj;
    for (let index = 0; index < path.length - 1; index += 1) {
      if (typeof node[path[index]] !== "object" || node[path[index]] === null) node[path[index]] = {};
      node = node[path[index]];
    }
    node[path[path.length - 1]] = value;
  }

  function miniButton(label, onClick) {
    const button = el("button", "mini-button", label);
    button.type = "button";
    button.addEventListener("click", onClick);
    return button;
  }

  function listEditor(initial, onChange) {
    const items = Array.isArray(initial) ? initial.slice() : [];
    const wrap = el("div", "list-editor");
    const emit = () => onChange(items.slice());
    const rebuild = () => {
      wrap.replaceChildren();
      items.forEach((value, index) => {
        const row = el("div", "list-editor__row");
        const input = el("input");
        input.type = "text";
        input.value = value;
        input.addEventListener("input", () => { items[index] = input.value; emit(); });
        row.append(
          input,
          miniButton("\u2191", () => { if (index > 0) { items.splice(index - 1, 0, items.splice(index, 1)[0]); emit(); rebuild(); } }),
          miniButton("\u2193", () => { if (index < items.length - 1) { items.splice(index + 1, 0, items.splice(index, 1)[0]); emit(); rebuild(); } }),
          miniButton("\u2715", () => { items.splice(index, 1); emit(); rebuild(); }),
        );
        wrap.append(row);
      });
      const add = el("button", "button", "Add item");
      add.type = "button";
      add.addEventListener("click", () => { items.push(""); emit(); rebuild(); });
      wrap.append(add);
    };
    rebuild();
    return wrap;
  }

  function rowsEditor(initial, columns, onChange) {
    const items = Array.isArray(initial) ? initial.map((row) => Object.assign({}, row)) : [];
    const wrap = el("div", "list-editor");
    const emit = () => onChange(items.map((row) => Object.assign({}, row)));
    const rebuild = () => {
      wrap.replaceChildren();
      items.forEach((row, index) => {
        const line = el("div", "rows-editor__row");
        for (const column of columns) {
          const input = el("input");
          input.type = column.number ? "number" : "text";
          input.placeholder = column.placeholder;
          input.value = row[column.key] === undefined ? "" : String(row[column.key]);
          input.addEventListener("input", () => {
            row[column.key] = column.number ? Number(input.value) : input.value;
            emit();
          });
          line.append(input);
        }
        line.append(miniButton("\u2715", () => { items.splice(index, 1); emit(); rebuild(); }));
        wrap.append(line);
      });
      const add = el("button", "button", "Add row");
      add.type = "button";
      add.addEventListener("click", () => { items.push({}); emit(); rebuild(); });
      wrap.append(add);
    };
    rebuild();
    return wrap;
  }

  function fieldControl(field) {
    const value = getPath(editorState.agent, field.path);
    const epithetPath = field.path.join(".") === "tinyic.epithet";
    const commit = (next) => {
      setPath(editorState.agent, field.path, next);
      if (epithetPath && isGeneratedAgent(editorState.agent)) {
        // The strict (generated) contract pins occupation.description to the
        // epithet; keep them in lockstep so a save cannot fail on the pair.
        setPath(editorState.agent, ["persona", "occupation", "description"], next);
      }
      editorState.dirty = true;
    };
    if (field.kind === "select") {
      const select = el("select");
      for (const optionValue of field.options) {
        const option = el("option", null, optionValue);
        option.value = optionValue;
        if (optionValue === value) option.selected = true;
        select.append(option);
      }
      select.addEventListener("change", () => commit(select.value));
      return select;
    }
    if (field.kind === "textarea") {
      const area = el("textarea");
      area.rows = 3;
      area.value = value || "";
      if (field.max) area.maxLength = field.max;
      area.addEventListener("input", () => commit(area.value));
      return area;
    }
    if (field.kind === "list") return listEditor(value, commit);
    if (field.kind === "quotes") return rowsEditor(value, QUOTE_COLUMNS, commit);
    if (field.kind === "sources") return rowsEditor(value, SOURCE_COLUMNS, commit);
    const input = el("input");
    input.type = "text";
    input.value = value || "";
    if (field.max) input.maxLength = field.max;
    input.addEventListener("input", () => commit(input.value));
    return input;
  }

  function buildStructured() {
    const host = $("#editor-structured");
    host.replaceChildren();
    const generated = isGeneratedAgent(editorState.agent);
    for (const section of SECTIONS) {
      const box = el("section", "editor-section");
      box.append(el("h2", null, section.title));
      for (const field of section.fields) {
        // Generated personas mirror the epithet into occupation.description;
        // hide the mirror so the invariant cannot be broken by hand.
        if (generated && field.path.join(".") === "persona.occupation.description") continue;
        const label = el("label", "field");
        label.append(el("span", null, field.label), fieldControl(field));
        box.append(label);
      }
      host.append(box);
    }
    const generation = getPath(editorState.agent, ["tinyic", "generation"]);
    if (generation) {
      const box = el("section", "editor-section");
      box.append(el("h2", null, "Generation (read-only)"));
      const list = el("dl", "detail-list");
      for (const key of ["generated_by", "model_ref", "date", "search_calls", "quality", "disclaimer"]) {
        if (generation[key] !== undefined) {
          list.append(detailRow(key.replace(/_/g, " "), String(generation[key])));
        }
      }
      box.append(list);
      host.append(box);
    }
    if (editorState.origin === "built_in") {
      for (const control of host.querySelectorAll("input, textarea, select, button")) {
        control.disabled = true;
      }
    }
  }

  function setEditorMode(mode) {
    if (editorState.mode === "raw" && mode !== "raw" && editorState.origin !== "built_in") {
      // Leaving the raw tab adopts its edits (or blocks on a parse error) so
      // they are never silently discarded.
      const rawText = $("#editor-raw-text").value;
      if (rawText !== JSON.stringify(editorState.agent, null, 2)) {
        try {
          editorState.agent = JSON.parse(rawText);
          editorState.dirty = true;
        } catch (_err) {
          const issues = $("#editor-issues");
          issues.textContent = "Raw JSON does not parse — fix it (or reopen the persona) before leaving this tab.";
          issues.hidden = false;
          return;
        }
      }
    }
    $("#editor-issues").hidden = true;
    editorState.mode = mode;
    for (const name of ["structured", "dossier", "raw"]) {
      $("#editor-" + name).hidden = name !== mode;
      $("#tab-" + name).setAttribute("aria-selected", String(name === mode));
    }
    if (mode === "raw") {
      $("#editor-raw-text").value = JSON.stringify(editorState.agent, null, 2);
    }
    if (mode === "structured") {
      buildStructured();
    }
  }

  async function renderEditor(slug) {
    if (!slug) {
      window.location.hash = "#/library";
      return;
    }
    let payload;
    try {
      payload = await api("GET", "/api/personas/" + slug);
    } catch (error) {
      toast("Could not open " + slug + ": " + error.message);
      window.location.hash = "#/library";
      return;
    }
    editorState.slug = slug;
    editorState.origin = payload.origin;
    editorState.agent = payload.agent;
    editorState.dossierDirty = false;
    editorState.dirty = false;
    const displayName = getPath(payload.agent, ["persona", "name"]) || slug;
    $("#editor-name").textContent = displayName;
    $("#editor-meta").textContent = slug + " · " + (payload.origin === "built_in" ? "built-in" : "custom");
    const badge = $("#editor-monogram");
    const identity = personaIdentity(displayName);
    badge.className = "monogram " + identity.className;
    badge.textContent = identity.monogram;
    const readonly = payload.origin === "built_in";
    $("#editor-readonly").hidden = !readonly;
    $("#editor-save").hidden = readonly;
    $("#editor-delete").hidden = readonly;
    $("#editor-issues").hidden = true;
    $("#editor-dossier-text").value = payload.dossier || "";
    $("#editor-dossier-text").readOnly = readonly;
    $("#editor-raw-text").readOnly = readonly;
    TinyICMarkdown.setInto($("#editor-dossier-preview"), payload.dossier || "*No dossier.*");
    setEditorMode("structured");
    $("#editor-save").onclick = onSave;
    $("#editor-duplicate").onclick = () => onDuplicate(slug);
    $("#editor-delete").onclick = onEditorDelete;
    $("#tab-structured").onclick = () => setEditorMode("structured");
    $("#tab-dossier").onclick = () => setEditorMode("dossier");
    $("#tab-raw").onclick = () => setEditorMode("raw");
    $("#editor-dossier-text").oninput = () => {
      editorState.dossierDirty = true;
      editorState.dirty = true;
      TinyICMarkdown.setInto($("#editor-dossier-preview"), $("#editor-dossier-text").value);
    };
    $("#editor-raw-text").oninput = () => {
      editorState.dirty = true;
    };
  }

  async function onSave() {
    const issues = $("#editor-issues");
    issues.hidden = true;
    let agent = editorState.agent;
    if (editorState.mode === "raw") {
      try {
        agent = JSON.parse($("#editor-raw-text").value);
      } catch (_err) {
        issues.textContent = "Raw JSON does not parse.";
        issues.hidden = false;
        return;
      }
    }
    const body = { agent };
    if (editorState.dossierDirty) body.dossier = $("#editor-dossier-text").value;
    try {
      await api("PUT", "/api/personas/" + editorState.slug, body);
      editorState.agent = agent;
      editorState.dossierDirty = false;
      editorState.dirty = false;
      toast("Saved " + editorState.slug);
    } catch (error) {
      const detail = error.data && error.data.issues ? error.data.issues.join("; ") : error.message;
      issues.textContent = detail;
      issues.hidden = false;
    }
  }

  async function onEditorDelete() {
    const yes = await confirmDialog("Delete " + editorState.slug + "? Both artifact files are removed.");
    if (!yes) return;
    try {
      const result = await api("DELETE", "/api/personas/" + editorState.slug);
      editorState.dirty = false;
      toast(deleteToast(result));
      window.location.hash = "#/library";
    } catch (error) {
      toast("Delete failed: " + error.message);
    }
  }

  // -- committee ------------------------------------------------------------

  const committeeState = { selected: [], personas: [] };

  async function renderCommittee() {
    const errorBox = $("#committee-error");
    errorBox.hidden = true;
    try {
      const [committee, personas] = await Promise.all([
        api("GET", "/api/committee"),
        api("GET", "/api/personas"),
      ]);
      committeeState.selected = committee.committee.slice();
      committeeState.personas = personas.personas;
      $("#committee-source").textContent =
        (committee.source === "overlay" ? "Custom overlay" : "Built-in default") +
        " · " + committee.committee.length + " members";
      buildCommitteeLists();
      $("#committee-save").onclick = onCommitteeSave;
      $("#committee-reset").onclick = onCommitteeReset;
    } catch (error) {
      errorBox.textContent = "Could not load the committee: " + error.message;
      errorBox.hidden = false;
    }
  }

  function committeeRow(persona, actions, missing) {
    const row = el("li", "committee-row");
    row.append(monogramNode(persona.name));
    row.append(el("span", "committee-row__name", persona.name));
    row.append(el("span", "committee-row__slug", persona.slug));
    if (missing) row.append(el("span", "chip chip--warn", "Missing"));
    for (const action of actions) row.append(action);
    return row;
  }

  function buildCommitteeLists() {
    const selectedHost = $("#committee-selected");
    const availableHost = $("#committee-available");
    selectedHost.replaceChildren();
    availableHost.replaceChildren();
    const bySlug = new Map(committeeState.personas.map((p) => [p.slug, p]));
    committeeState.selected.forEach((slug, index) => {
      const persona = bySlug.get(slug) || { slug, name: slug };
      selectedHost.append(committeeRow(persona, [
        miniButton("\u2191", () => moveCommittee(index, -1)),
        miniButton("\u2193", () => moveCommittee(index, 1)),
        miniButton("\u2715", () => {
          committeeState.selected.splice(index, 1);
          buildCommitteeLists();
        }),
      ], !bySlug.has(slug)));
    });
    let available = 0;
    for (const persona of committeeState.personas) {
      if (committeeState.selected.includes(persona.slug)) continue;
      const add = el("button", "mini-button", "+");
      add.type = "button";
      add.addEventListener("click", () => {
        committeeState.selected.push(persona.slug);
        buildCommitteeLists();
      });
      availableHost.append(committeeRow(persona, [add]));
      available += 1;
    }
    if (available === 0) {
      availableHost.append(el("li", "empty-note", "Every persona is on the committee."));
    }
    const count = committeeState.selected.length;
    const save = $("#committee-save");
    save.disabled = count < 2 || count > 6;
    $("#committee-count").textContent =
      count + " selected" + (save.disabled ? " \u2014 a committee needs 2 to 6 members" : "");
  }

  function moveCommittee(index, delta) {
    const target = index + delta;
    if (target < 0 || target >= committeeState.selected.length) return;
    const [item] = committeeState.selected.splice(index, 1);
    committeeState.selected.splice(target, 0, item);
    buildCommitteeLists();
  }

  async function onCommitteeSave() {
    const errorBox = $("#committee-error");
    errorBox.hidden = true;
    try {
      const result = await api("PUT", "/api/committee", { committee: committeeState.selected });
      $("#committee-source").textContent =
        (result.source === "overlay" ? "Custom overlay" : "Built-in default") +
        " · " + result.committee.length + " members";
      toast("Default committee saved");
    } catch (error) {
      const unknown = error.data && error.data.unknown ? " (" + error.data.unknown.join(", ") + ")" : "";
      errorBox.textContent = error.message + unknown;
      errorBox.hidden = false;
    }
  }

  async function onCommitteeReset() {
    const yes = await confirmDialog("Reset the default committee to the built-in six?");
    if (!yes) return;
    const errorBox = $("#committee-error");
    errorBox.hidden = true;
    try {
      await api("PUT", "/api/committee", { committee: null });
      toast("Reset to the built-in six");
      renderCommittee();
    } catch (error) {
      errorBox.textContent = "Reset failed: " + error.message;
      errorBox.hidden = false;
    }
  }

  window.addEventListener("beforeunload", (event) => {
    if (document.body.dataset.view === "edit" && editorState.dirty) {
      event.preventDefault();
    }
  });

  window.addEventListener("hashchange", route);
  route();
})();
