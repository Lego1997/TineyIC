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
      const error = new Error((data && data.error) || "http_" + response.status);
      error.status = response.status;
      error.data = data || {};
      throw error;
    }
    return data;
  }

  let toastTimer = null;
  function toast(message) {
    const node = $("#toast");
    node.textContent = message;
    node.hidden = false;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { node.hidden = true; }, 4000);
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
      const settle = (value) => () => { dialog.close(); resolve(value); };
      $("#duplicate-accept").onclick = settle(() => input.value.trim());
      $("#duplicate-cancel").onclick = settle(null);
      dialog.oncancel = settle(null);
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

  const VIEWS = { library: renderLibrary, research: renderResearch, editor: renderEditor, committee: renderCommittee };

  function route() {
    const hash = window.location.hash || "#/library";
    const parts = hash.replace(/^#\//, "").split("/");
    const view = VIEWS[parts[0]] ? parts[0] : "library";
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

  async function onDelete(slug, name) {
    const yes = await confirmDialog("Delete " + name + " (" + slug + ")? Both artifact files are removed.");
    if (!yes) return;
    try {
      await api("DELETE", "/api/personas/" + slug);
      toast("Deleted " + slug);
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
    if (error.message === "no_search_capable_lane") {
      return "No verified API-key search lane. Run 'tinyic onboard' to add an OpenAI, Grok, Google, or Kimi credential.";
    }
    if (error.message === "persona_exists") {
      return "That slug already exists — tick \u201cReplace the existing persona\u201d or choose another slug.";
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

  async function onEstimate(event) {
    event.preventDefault();
    showResearchError("");
    $("#research-estimate-card").hidden = true;
    $("#research-progress").hidden = true;
    $("#research-result").hidden = true;
    let estimate;
    try {
      estimate = await api("POST", "/api/research/estimate", researchSpec());
    } catch (error) {
      showResearchError(researchErrorMessage(error));
      return;
    }
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
    $("#research-confirm").disabled = true;
    try {
      const spec = researchSpec();
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
    $("#research-form").onsubmit = onEstimate;
  }

  // Views added by later milestones; stubbed so the router boots today.
  function renderEditor() {}
  function renderCommittee() {}

  window.addEventListener("hashchange", route);
  route();
})();
