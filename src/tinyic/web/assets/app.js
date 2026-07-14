(() => {
  "use strict";

  const SCHEMA_VERSION = 1;
  const AUTHORITATIVE_EVENT_TYPES = Object.freeze([
    "debate_started",
    "data_ready",
    "phase_started",
    "phase_completed",
    "debate_completed",
    "debate_error",
    "turn_started",
    "think_delta",
    "think_completed",
    "talk_delta",
    "talk_completed",
    "cognitive_state",
    "turn_completed",
    "turn_interrupted",
    "steering_submitted",
    "steering_delivered",
    "steering_dropped",
    "thesis_recorded",
    "vote_recorded",
    "scorecard",
    "memo_section",
    "disagreement",
    "collapse_metric",
    "usage",
    "usage_window",
  ]);

  const PHASE_LABELS = Object.freeze({
    opening: "Opening",
    cross_exam: "Cross-examination",
    rebuttal: "Rebuttal",
    verdict: "Verdict",
  });

  const ROLE_LABELS = Object.freeze({
    statement: "Statement",
    challenge: "Challenge",
    response: "Response",
    rebuttal: "Rebuttal",
    verdict: "Verdict",
  });

  const MEMO_LABELS = Object.freeze({
    executive_summary: "Executive summary",
    investment_thesis: "Investment thesis",
    key_risks: "Key risks",
    valuation_discussion: "Valuation discussion",
    final_verdict: "Final verdict",
  });

  const MEMO_ORDER = Object.freeze(Object.keys(MEMO_LABELS));

  // Hand-synced with src/tinyic/persona_style.py. Colors live in style.css.
  const PERSONA_IDENTITIES = Object.freeze({
    "Warren Buffett": { monogram: "WB", className: "persona-warren-buffett" },
    "Charlie Munger": { monogram: "CM", className: "persona-charlie-munger" },
    "Benjamin Graham": { monogram: "BG", className: "persona-benjamin-graham" },
    "Peter Lynch": { monogram: "PL", className: "persona-peter-lynch" },
    "Howard Marks": { monogram: "HM", className: "persona-howard-marks" },
    "Li Lu": { monogram: "LL", className: "persona-li-lu" },
  });

  const dom = {
    body: document.body,
    ticker: document.querySelector("#ticker"),
    companyName: document.querySelector("#company-name"),
    phaseChip: document.querySelector("#phase-chip"),
    elapsed: document.querySelector("#elapsed"),
    sequence: document.querySelector("#sequence"),
    replayChip: document.querySelector("#replay-chip"),
    runState: document.querySelector("#run-state"),
    connectionState: document.querySelector("#connection-state"),
    connectionLabel: document.querySelector("#connection-label"),
    thinkingToggle: document.querySelector("#thinking-toggle"),
    stopButton: document.querySelector("#stop-button"),
    errorBanner: document.querySelector("#error-banner"),
    errorTitle: document.querySelector("#error-title"),
    errorMessage: document.querySelector("#error-message"),
    truncatedBanner: document.querySelector("#truncated-banner"),
    verdictSummary: document.querySelector("#verdict-summary"),
    verdictTitle: document.querySelector("#verdict-title"),
    verdictCounts: document.querySelector("#verdict-counts"),
    verdictCopy: document.querySelector("#verdict-copy"),
    dataPackage: document.querySelector("#data-package"),
    dataSourceCount: document.querySelector("#data-source-count"),
    financialsSummary: document.querySelector("#financials-summary"),
    dataSources: document.querySelector("#data-sources"),
    transcript: document.querySelector("#transcript"),
    transcriptEmpty: document.querySelector("#transcript-empty"),
    transcriptStatus: document.querySelector("#transcript-status"),
    committeeRoster: document.querySelector("#committee-roster"),
    committeeEmpty: document.querySelector("#committee-empty"),
    committeeCount: document.querySelector("#committee-count"),
    scorecardPanel: document.querySelector("#scorecard-panel"),
    scorecardBody: document.querySelector("#scorecard-body"),
    scorecardConsensus: document.querySelector("#scorecard-consensus"),
    scorecardSplit: document.querySelector("#scorecard-split"),
    memoPanel: document.querySelector("#memo-panel"),
    memoSections: document.querySelector("#memo-sections"),
    memoCount: document.querySelector("#memo-count"),
    disagreementPanel: document.querySelector("#disagreement-panel"),
    disagreements: document.querySelector("#disagreements"),
    disagreementCount: document.querySelector("#disagreement-count"),
    steeringPanel: document.querySelector("#steering-panel"),
    steeringHistory: document.querySelector("#steering-history"),
    steeringCount: document.querySelector("#steering-count"),
    composer: document.querySelector("#composer"),
    steeringForm: document.querySelector("#steering-form"),
    steeringTarget: document.querySelector("#steering-target"),
    steeringText: document.querySelector("#steering-text"),
    sendButton: document.querySelector("#send-button"),
    steerMode: document.querySelector("#steer-mode"),
    queueMode: document.querySelector("#queue-mode"),
    pauseButton: document.querySelector("#pause-button"),
    nextPhaseButton: document.querySelector("#next-phase-button"),
    interruptButton: document.querySelector("#interrupt-button"),
    jumpLive: document.querySelector("#jump-live"),
    confirmDialog: document.querySelector("#confirm-dialog"),
    confirmTitle: document.querySelector("#confirm-title"),
    confirmMessage: document.querySelector("#confirm-message"),
    confirmAccept: document.querySelector("#confirm-accept"),
    toast: document.querySelector("#toast"),
  };

  const state = {
    eventSource: null,
    meta: { replay: false, status: "connecting", seqHigh: 0 },
    lastSeq: 0,
    firstTimestamp: null,
    lastTimestamp: null,
    terminalSeen: false,
    currentPhase: "",
    currentTurnId: "",
    mode: "steer",
    paused: false,
    thinkingVisible: false,
    phases: new Map(),
    turns: new Map(),
    personas: new Map(),
    votes: new Map(),
    scorecard: null,
    memo: new Map(),
    disagreements: [],
    steering: new Map(),
    steeringOrder: [],
    toastTimer: null,
  };

  function isRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function hasOwn(record, key) {
    return Object.prototype.hasOwnProperty.call(record, key);
  }

  function asString(value, fallback = "") {
    return typeof value === "string" ? value : fallback;
  }

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function createElement(tagName, className = "", text = "") {
    const node = document.createElement(tagName);
    if (className) {
      node.className = className;
    }
    if (text !== "") {
      node.textContent = text;
    }
    return node;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function safeHttpUrl(escapedCandidate) {
    const candidate = escapedCandidate.replace(/&amp;/g, "&");
    try {
      const parsed = new URL(candidate);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        return null;
      }
      return escapeHtml(parsed.href);
    } catch (_error) {
      return null;
    }
  }

  function formatEmphasis(escapedText) {
    return escapedText
      .replace(/\*\*([^*\n]+?)\*\*/g, "<strong>$1</strong>")
      .replace(/__([^_\n]+?)__/g, "<strong>$1</strong>")
      .replace(/(^|[\s(])\*([^*\n]+?)\*(?=$|[\s).,!?:;])/g, "$1<em>$2</em>")
      .replace(/(^|[\s(])_([^_\n]+?)_(?=$|[\s).,!?:;])/g, "$1<em>$2</em>");
  }

  function renderInline(escapedText) {
    const tokens = [];
    const reserve = (html) => {
      const token = `TINYICTOKEN${tokens.length}END`;
      tokens.push(html);
      return token;
    };

    let rendered = escapedText.replace(/`([^`\n]+?)`/g, (_match, code) => {
      return reserve(`<code>${code}</code>`);
    });

    rendered = rendered.replace(/\[([^\]\n]+?)\]\(([^)\s]+?)\)/g, (_match, label, href) => {
      const safeHref = safeHttpUrl(href);
      if (safeHref === null) {
        return formatEmphasis(label);
      }
      const safeLabel = formatEmphasis(label);
      return reserve(
        `<a href="${safeHref}" rel="noopener noreferrer" target="_blank">${safeLabel}</a>`,
      );
    });

    rendered = formatEmphasis(rendered);
    return rendered.replace(/TINYICTOKEN(\d+)END/g, (_match, index) => {
      return tokens[Number(index)] ?? "";
    });
  }

  function renderMarkdown(markdown) {
    // Model output is escaped before a single Markdown token is interpreted.
    const lines = escapeHtml(String(markdown ?? "").replace(/\r\n?/g, "\n")).split("\n");
    const blocks = [];
    let index = 0;

    const startsBlock = (line) => (
      /^\s*```/.test(line)
      || /^#{1,6}\s+/.test(line)
      || /^&gt;\s?/.test(line)
      || /^\s*[-+*]\s+/.test(line)
      || /^\s*\d+[.)]\s+/.test(line)
    );

    while (index < lines.length) {
      const line = lines[index];
      if (line.trim() === "") {
        index += 1;
        continue;
      }

      const fence = line.match(/^\s*```([^`]*)$/);
      if (fence) {
        const codeLines = [];
        index += 1;
        while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) {
          codeLines.push(lines[index]);
          index += 1;
        }
        if (index < lines.length) {
          index += 1;
        }
        blocks.push(`<pre><code>${codeLines.join("\n")}</code></pre>`);
        continue;
      }

      const heading = line.match(/^(#{1,6})\s+(.+)$/);
      if (heading) {
        const level = heading[1].length;
        blocks.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
        index += 1;
        continue;
      }

      if (/^&gt;\s?/.test(line)) {
        const quoteLines = [];
        while (index < lines.length && /^&gt;\s?/.test(lines[index])) {
          quoteLines.push(renderInline(lines[index].replace(/^&gt;\s?/, "")));
          index += 1;
        }
        blocks.push(`<blockquote>${quoteLines.join("<br>")}</blockquote>`);
        continue;
      }

      const unordered = line.match(/^\s*[-+*]\s+(.+)$/);
      const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
      if (unordered || ordered) {
        const listTag = ordered ? "ol" : "ul";
        const matcher = ordered ? /^\s*\d+[.)]\s+(.+)$/ : /^\s*[-+*]\s+(.+)$/;
        const items = [];
        while (index < lines.length) {
          const item = lines[index].match(matcher);
          if (!item) {
            break;
          }
          items.push(`<li>${renderInline(item[1])}</li>`);
          index += 1;
        }
        blocks.push(`<${listTag}>${items.join("")}</${listTag}>`);
        continue;
      }

      const paragraph = [];
      while (
        index < lines.length
        && lines[index].trim() !== ""
        && (paragraph.length === 0 || !startsBlock(lines[index]))
      ) {
        paragraph.push(renderInline(lines[index]));
        index += 1;
      }
      if (paragraph.length > 0) {
        blocks.push(`<p>${paragraph.join("<br>")}</p>`);
      }
    }

    return blocks.join("");
  }

  function setMarkdown(node, markdown) {
    // Only the escape-first renderer output is parsed; raw model text never is.
    const fragment = document.createRange().createContextualFragment(renderMarkdown(markdown));
    node.replaceChildren(fragment);
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
    const words = name.trim().split(/\s+/).filter(Boolean);
    let monogram = "??";
    if (words.length >= 2) {
      monogram = `${words[0][0]}${words[1][0]}`.toUpperCase();
    } else if (words.length === 1) {
      monogram = (words[0].slice(0, 2) || "??").padEnd(2, words[0][0] || "?").toUpperCase();
    }
    return {
      monogram,
      className: `persona-fallback-${fallbackHash(name) % 6}`,
    };
  }

  function monogramNode(name) {
    const identity = personaIdentity(name);
    return createElement("span", `monogram ${identity.className}`, identity.monogram);
  }

  function phaseLabel(phase) {
    return PHASE_LABELS[phase] ?? (phase.replace(/_/g, " ") || "Pending");
  }

  function setConnection(connection, label) {
    dom.connectionState.dataset.connection = connection;
    dom.connectionLabel.textContent = label;
  }

  function controlsShouldBeEnabled(runState) {
    return runState === "live";
  }

  function setRunState(runState, label = "") {
    const labels = {
      connecting: "Connecting",
      live: "Live",
      replay: "Replay",
      completed: "Completed",
      error: "Error",
    };
    state.meta.status = runState;
    dom.body.dataset.runState = runState;
    dom.runState.textContent = label || labels[runState] || runState;
    dom.replayChip.hidden = !state.meta.replay;

    const enabled = controlsShouldBeEnabled(runState);
    for (const control of [
      dom.steeringTarget,
      dom.steeringText,
      dom.sendButton,
      dom.steerMode,
      dom.queueMode,
      dom.pauseButton,
      dom.nextPhaseButton,
      dom.interruptButton,
      dom.stopButton,
    ]) {
      control.disabled = !enabled;
    }
    dom.nextPhaseButton.disabled = !enabled || !state.paused;
    if (enabled && state.mode === "queue") {
      dom.steeringTarget.disabled = true;
    }
  }

  function formatElapsed(milliseconds) {
    const seconds = Math.max(0, Math.floor(milliseconds / 1000));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remainder = seconds % 60;
    if (hours > 0) {
      return [hours, minutes, remainder].map((part) => String(part).padStart(2, "0")).join(":");
    }
    return [minutes, remainder].map((part) => String(part).padStart(2, "0")).join(":");
  }

  function trackEventTime(timestamp) {
    const parsed = Date.parse(timestamp);
    if (!Number.isFinite(parsed)) {
      return;
    }
    if (state.firstTimestamp === null) {
      state.firstTimestamp = parsed;
    }
    state.lastTimestamp = state.lastTimestamp === null ? parsed : Math.max(parsed, state.lastTimestamp);
    dom.elapsed.textContent = formatElapsed(state.lastTimestamp - state.firstTimestamp);
  }

  function removeTranscriptEmptyState() {
    if (dom.transcriptEmpty?.isConnected) {
      dom.transcriptEmpty.remove();
    }
  }

  function ensurePhase(phase, index = null, daPersona = "") {
    const key = phase || "unassigned";
    if (state.phases.has(key)) {
      return state.phases.get(key);
    }
    removeTranscriptEmptyState();
    const section = createElement("section", "phase-section");
    section.dataset.phase = key;
    section.dataset.completed = "false";

    const heading = createElement("div", "phase-heading");
    const shownIndex = Number.isInteger(index) ? index + 1 : state.phases.size + 1;
    heading.append(createElement("span", "phase-heading__index", String(shownIndex).padStart(2, "0")));
    heading.append(createElement("h3", "", phaseLabel(key)));
    const meta = createElement("span", "phase-heading__meta");
    if (daPersona) {
      meta.textContent = `Devil’s advocate: ${daPersona}`;
    }
    heading.append(meta);
    section.append(heading);
    dom.transcript.append(section);

    const record = { section, headingMeta: meta, phase: key };
    state.phases.set(key, record);
    return record;
  }

  function setRosterActivity(name, activity, kind = "idle") {
    const member = state.personas.get(name);
    if (!member) {
      return;
    }
    member.node.dataset.speaking = kind === "speaking" ? "true" : "false";
    member.node.dataset.thinking = kind === "thinking" ? "true" : "false";
    member.activity.textContent = activity;
  }

  function clearActiveSpeaker(except = "") {
    for (const [name] of state.personas) {
      if (name !== except) {
        setRosterActivity(name, "Ready");
      }
    }
    for (const turn of state.turns.values()) {
      if (turn.id !== state.currentTurnId) {
        turn.node.dataset.active = "false";
      }
    }
  }

  function renderRoster(personas) {
    state.personas.clear();
    dom.committeeRoster.replaceChildren();
    while (dom.steeringTarget.options.length > 1) {
      dom.steeringTarget.remove(1);
    }

    for (const rawPersona of personas) {
      if (!isRecord(rawPersona)) {
        continue;
      }
      const name = asString(rawPersona.name).trim();
      if (!name) {
        continue;
      }
      const identity = personaIdentity(name);
      const item = createElement("li", `committee-member ${identity.className}`);
      item.dataset.speaking = "false";
      item.dataset.thinking = "false";
      item.append(monogramNode(name));

      const identityBlock = createElement("div", "committee-member__identity");
      identityBlock.append(createElement("span", "committee-member__name", name));
      identityBlock.append(
        createElement("span", "committee-member__model", asString(rawPersona.model_ref, "model pending")),
      );
      item.append(identityBlock);
      const activity = createElement("span", "committee-member__activity", "Ready");
      item.append(activity);
      dom.committeeRoster.append(item);

      state.personas.set(name, { node: item, activity, data: rawPersona });
      const option = createElement("option", "", name);
      option.value = name;
      dom.steeringTarget.append(option);
    }

    dom.committeeCount.textContent = String(state.personas.size);
    if (state.personas.size === 0) {
      dom.committeeRoster.append(createElement("li", "rail-empty", "Roster unavailable"));
    }
  }

  function onDebateStarted(payload) {
    dom.ticker.textContent = asString(payload.ticker, "—");
    dom.companyName.textContent = asString(payload.company_name, "Company name unavailable");
    renderRoster(asArray(payload.personas));
    dom.transcriptStatus.textContent = "Debate record opened";
    if (!state.meta.replay && state.meta.status !== "completed") {
      setRunState("live");
    }
  }

  function onDataReady(payload) {
    dom.dataPackage.hidden = false;
    const summaryParts = [asString(payload.description), asString(payload.financials_summary)].filter(Boolean);
    setMarkdown(dom.financialsSummary, summaryParts.join("\n\n"));
    dom.dataSources.replaceChildren();
    const sources = asArray(payload.sources).filter(isRecord);
    const okCount = sources.filter((source) => source.status === "ok").length;
    dom.dataSourceCount.textContent = `${okCount}/${sources.length} ready`;

    for (const source of sources) {
      const item = createElement("li", "source-item");
      item.dataset.sourceStatus = asString(source.status, "unknown");
      item.append(createElement("span", "source-item__name", asString(source.name, "Unknown source")));
      const status = createElement("span", "status-chip", asString(source.status, "unknown"));
      status.dataset.status = asString(source.status, "unknown");
      item.append(status);
      if (source.warning) {
        item.append(createElement("p", "source-item__warning", asString(source.warning)));
      }
      dom.dataSources.append(item);
    }
  }

  function onPhaseStarted(payload) {
    const phase = asString(payload.phase);
    if (!phase) {
      return;
    }
    state.currentPhase = phase;
    const index = Number.isInteger(payload.index) ? payload.index : null;
    ensurePhase(phase, index, asString(payload.da_persona));
    dom.phaseChip.textContent = phaseLabel(phase);
    dom.transcriptStatus.textContent = `${phaseLabel(phase)} in progress`;
  }

  function onPhaseCompleted(payload) {
    const phase = asString(payload.phase);
    const record = state.phases.get(phase);
    if (record) {
      record.section.dataset.completed = "true";
      if (Number.isInteger(payload.turn_count)) {
        record.headingMeta.textContent = `${payload.turn_count} turns recorded`;
      }
    }
  }

  function onTurnStarted(payload) {
    const turnId = asString(payload.turn_id);
    if (!turnId || state.turns.has(turnId)) {
      return;
    }
    const persona = asString(payload.persona, "Unknown member");
    const phase = asString(payload.phase, state.currentPhase || "unassigned");
    const phaseRecord = ensurePhase(phase);
    const identity = personaIdentity(persona);
    const article = createElement("article", `turn ${identity.className}`);
    article.dataset.turnId = turnId;
    article.dataset.active = "true";
    article.dataset.interrupted = "false";
    article.append(monogramNode(persona));

    const body = createElement("div", "turn__body");
    const header = createElement("header", "turn__header");
    header.append(createElement("h4", "turn__speaker", persona));
    header.append(createElement("span", "turn__role", ROLE_LABELS[payload.role] ?? asString(payload.role, "Turn")));
    if (payload.target_persona) {
      header.append(createElement("span", "turn__target", `to ${asString(payload.target_persona)}`));
    }
    const status = createElement("span", "turn__status", "Speaking");
    header.append(status);

    const speech = createElement("div", "turn__speech markdown");
    const thinkingDetails = createElement("details", "thinking-details");
    thinkingDetails.hidden = true;
    thinkingDetails.open = state.thinkingVisible;
    thinkingDetails.append(createElement("summary", "", "Reasoning…"));
    const thinking = createElement("div", "thinking-details__copy markdown");
    thinkingDetails.append(thinking);
    body.append(header, speech, thinkingDetails);
    article.append(body);
    phaseRecord.section.append(article);

    for (const turn of state.turns.values()) {
      turn.node.dataset.active = "false";
    }
    state.currentTurnId = turnId;
    clearActiveSpeaker(persona);
    setRosterActivity(persona, "Speaking", "speaking");
    state.turns.set(turnId, {
      id: turnId,
      persona,
      node: article,
      speech,
      speechText: "",
      thinking,
      thinkingText: "",
      thinkingDetails,
      status,
    });
  }

  function turnFor(payload) {
    return state.turns.get(asString(payload.turn_id));
  }

  function onThinkDelta(payload) {
    const turn = turnFor(payload);
    if (!turn) {
      return;
    }
    turn.thinkingText += asString(payload.text);
    turn.thinkingDetails.hidden = turn.thinkingText.length === 0;
    turn.thinkingDetails.open = state.thinkingVisible;
    setMarkdown(turn.thinking, turn.thinkingText);
    setRosterActivity(turn.persona, "Reasoning", "thinking");
  }

  function onThinkCompleted(payload) {
    const turn = turnFor(payload);
    if (!turn) {
      return;
    }
    turn.thinkingText = asString(payload.full_text);
    turn.thinkingDetails.hidden = turn.thinkingText.length === 0;
    turn.thinkingDetails.open = state.thinkingVisible;
    setMarkdown(turn.thinking, turn.thinkingText);
  }

  function onTalkDelta(payload) {
    const turn = turnFor(payload);
    if (!turn) {
      return;
    }
    turn.speechText += asString(payload.text);
    setMarkdown(turn.speech, turn.speechText);
    setRosterActivity(turn.persona, "Speaking", "speaking");
  }

  function onTalkCompleted(payload) {
    const turn = turnFor(payload);
    if (!turn) {
      return;
    }
    turn.speechText = asString(payload.full_text);
    setMarkdown(turn.speech, turn.speechText);
    turn.status.textContent = "Statement recorded";
  }

  function onCognitiveState(payload) {
    const persona = asString(payload.persona);
    const member = state.personas.get(persona);
    if (member && payload.attention) {
      member.node.title = `Attention: ${asString(payload.attention)}`;
    }
  }

  function onTurnCompleted(payload) {
    const turn = turnFor(payload);
    if (turn) {
      turn.node.dataset.active = "false";
      if (payload.interrupted) {
        turn.node.dataset.interrupted = "true";
        turn.status.textContent = "Interrupted";
      } else {
        turn.status.textContent = "Complete";
      }
      setRosterActivity(turn.persona, "Ready");
    }
  }

  function onTurnInterrupted(payload) {
    const turn = turnFor(payload);
    if (!turn) {
      return;
    }
    turn.node.dataset.active = "false";
    turn.node.dataset.interrupted = "true";
    const disposition = asString(payload.disposition).replace(/_/g, " ");
    turn.status.textContent = disposition ? `Interrupted · ${disposition}` : "Interrupted";
    setRosterActivity(turn.persona, "Interrupted");
  }

  function scorecardVoteOrder() {
    const ordered = [];
    const seen = new Set();
    for (const name of state.personas.keys()) {
      if (state.votes.has(name)) {
        ordered.push(state.votes.get(name));
        seen.add(name);
      }
    }
    for (const [name, vote] of state.votes) {
      if (!seen.has(name)) {
        ordered.push(vote);
      }
    }
    return ordered;
  }

  function renderScorecard() {
    const votes = scorecardVoteOrder();
    if (votes.length === 0 && !state.scorecard) {
      return;
    }
    dom.scorecardPanel.hidden = false;
    dom.scorecardBody.replaceChildren();

    for (const vote of votes) {
      const persona = asString(vote.persona, "Unknown member");
      const row = createElement("tr", personaIdentity(persona).className);
      row.append(createElement("td", "", persona));
      const voteCell = createElement("td");
      const voteChip = createElement("span", "vote-chip", asString(vote.vote, "—"));
      voteChip.dataset.vote = asString(vote.vote);
      voteCell.append(voteChip);
      row.append(voteCell);
      const confidence = asString(vote.confidence, "—");
      row.append(createElement("td", "", vote.changed_mind ? `${confidence} · changed` : confidence));
      dom.scorecardBody.append(row);
    }

    const derived = { BUY: 0, HOLD: 0, SELL: 0 };
    for (const vote of votes) {
      if (hasOwn(derived, vote.vote)) {
        derived[vote.vote] += 1;
      }
    }
    const card = state.scorecard ?? {};
    const buys = Number.isInteger(card.bull_count) ? card.bull_count : derived.BUY;
    const holds = Number.isInteger(card.hold_count) ? card.hold_count : derived.HOLD;
    const sells = Number.isInteger(card.bear_count) ? card.bear_count : derived.SELL;
    dom.scorecardSplit.textContent = `BUY ${buys} · HOLD ${holds} · SELL ${sells}`;
    dom.scorecardConsensus.textContent = asString(card.consensus, votes.length ? "Live" : "Pending") || "Split";
  }

  function onThesisRecorded(payload) {
    const persona = asString(payload.persona);
    if (persona && payload.stance) {
      setRosterActivity(persona, asString(payload.stance));
    }
  }

  function onVoteRecorded(payload) {
    const persona = asString(payload.persona);
    if (!persona) {
      return;
    }
    state.votes.set(persona, payload);
    setRosterActivity(persona, asString(payload.vote, "Voted"));
    renderScorecard();
  }

  function onScorecard(payload) {
    state.scorecard = payload;
    for (const vote of asArray(payload.votes)) {
      if (isRecord(vote) && vote.persona) {
        const prior = state.votes.get(vote.persona) ?? {};
        state.votes.set(vote.persona, { ...prior, ...vote });
      }
    }
    renderScorecard();
  }

  function renderMemo() {
    dom.memoPanel.hidden = state.memo.size === 0;
    dom.memoCount.textContent = `${state.memo.size}/5`;
    dom.memoSections.replaceChildren();

    for (const sectionName of MEMO_ORDER) {
      const payload = state.memo.get(sectionName);
      if (!payload) {
        continue;
      }
      const card = createElement("article", "memo-card");
      card.append(createElement("h3", "", MEMO_LABELS[sectionName]));
      const content = createElement("div", "markdown");
      setMarkdown(content, asString(payload.content));
      card.append(content);
      const contributors = asArray(payload.contributing_personas).filter((name) => typeof name === "string");
      if (contributors.length > 0) {
        card.append(
          createElement("p", "memo-card__contributors", `Contributors: ${contributors.join(", ")}`),
        );
      }
      dom.memoSections.append(card);
    }
  }

  function onMemoSection(payload) {
    const section = asString(payload.section);
    if (!hasOwn(MEMO_LABELS, section)) {
      return;
    }
    state.memo.set(section, payload);
    renderMemo();
  }

  function renderDisagreements() {
    dom.disagreementPanel.hidden = state.disagreements.length === 0;
    dom.disagreementCount.textContent = String(state.disagreements.length);
    dom.disagreements.replaceChildren();

    for (const entry of state.disagreements) {
      const card = createElement("article", "disagreement-card");
      if (entry.kind === "collapse") {
        card.dataset.caved = entry.payload.caved ? "true" : "false";
        const persona = asString(entry.payload.persona, "Unknown member");
        card.append(
          createElement("h3", "", `${persona} · ${entry.payload.caved ? "caved" : "held"}`),
        );
        const movement = [entry.payload.stance_before, entry.payload.stance_after]
          .filter((value) => typeof value === "string" && value)
          .join(" → ");
        const copy = createElement("div", "markdown");
        setMarkdown(copy, [movement, asString(entry.payload.note)].filter(Boolean).join("\n\n"));
        card.append(copy);
      } else {
        card.append(createElement("h3", "", asString(entry.payload.dimension, "Disagreement")));
        const copy = createElement("div", "markdown");
        setMarkdown(copy, asString(entry.payload.description));
        card.append(copy);
        const sides = asArray(entry.payload.sides).filter(isRecord);
        if (sides.length > 0) {
          const list = createElement("ul", "disagreement-card__sides");
          for (const side of sides) {
            const item = createElement("li");
            const who = asString(side.persona, "Committee member");
            const position = asString(side.position);
            item.append(createElement("strong", "", `${who}: `));
            item.append(document.createTextNode(position));
            if (side.evidence_quote) {
              item.append(document.createTextNode(` — “${asString(side.evidence_quote)}”`));
            }
            list.append(item);
          }
          card.append(list);
        }
        if (entry.payload.resolution) {
          card.append(
            createElement(
              "p",
              "disagreement-card__resolution",
              `Resolution: ${asString(entry.payload.resolution)}`,
            ),
          );
        }
      }
      dom.disagreements.append(card);
    }
  }

  function onDisagreement(payload) {
    state.disagreements.push({ kind: "disagreement", payload });
    renderDisagreements();
  }

  function onCollapseMetric(payload) {
    state.disagreements.push({ kind: "collapse", payload });
    renderDisagreements();
  }

  function renderSteering() {
    dom.steeringPanel.hidden = state.steeringOrder.length === 0;
    dom.steeringCount.textContent = String(state.steeringOrder.length);
    dom.steeringHistory.replaceChildren();

    for (const msgId of state.steeringOrder) {
      const message = state.steering.get(msgId);
      if (!message) {
        continue;
      }
      const item = createElement("li", "steering-item");
      const meta = createElement("div", "steering-item__meta");
      const target = message.target ? ` · ${message.target}` : " · Committee";
      meta.append(createElement("p", "", `${message.mode || "steer"}${target}`));
      const chip = createElement("span", "status-chip", message.status);
      chip.dataset.status = message.status;
      meta.append(chip);
      item.append(meta);
      item.append(createElement("p", "steering-item__text", message.text || "Message unavailable"));
      if (message.reason) {
        item.append(createElement("p", "memo-card__contributors", message.reason));
      }
      dom.steeringHistory.append(item);
    }
  }

  function steeringRecord(msgId) {
    if (!state.steering.has(msgId)) {
      state.steering.set(msgId, {
        id: msgId,
        mode: "steer",
        target: "",
        text: "Message details unavailable",
        status: "submitted",
        reason: "",
      });
      state.steeringOrder.push(msgId);
    }
    return state.steering.get(msgId);
  }

  function onSteeringSubmitted(payload) {
    const msgId = asString(payload.msg_id, `sequence-${state.lastSeq}`);
    const record = steeringRecord(msgId);
    record.mode = asString(payload.mode, "steer");
    record.target = asString(payload.target_persona);
    record.text = asString(payload.text, "Message unavailable");
    record.status = "submitted";
    record.reason = "";
    renderSteering();
  }

  function onSteeringDelivered(payload) {
    const record = steeringRecord(asString(payload.msg_id, `sequence-${state.lastSeq}`));
    record.status = "delivered";
    if (payload.delivered_before_turn_id) {
      record.reason = `Delivered before ${asString(payload.delivered_before_turn_id)}`;
    }
    renderSteering();
  }

  function onSteeringDropped(payload) {
    const record = steeringRecord(asString(payload.msg_id, `sequence-${state.lastSeq}`));
    record.status = "dropped";
    record.reason = asString(payload.reason, "Not delivered");
    renderSteering();
  }

  function showVerdictSummary() {
    dom.verdictSummary.hidden = false;
    const card = state.scorecard ?? {};
    const consensus = asString(card.consensus) || "Split decision";
    dom.verdictTitle.textContent = consensus;
    const derived = scorecardVoteOrder().reduce(
      (counts, vote) => {
        if (hasOwn(counts, vote.vote)) {
          counts[vote.vote] += 1;
        }
        return counts;
      },
      { BUY: 0, HOLD: 0, SELL: 0 },
    );
    const buys = Number.isInteger(card.bull_count) ? card.bull_count : derived.BUY;
    const holds = Number.isInteger(card.hold_count) ? card.hold_count : derived.HOLD;
    const sells = Number.isInteger(card.bear_count) ? card.bear_count : derived.SELL;
    dom.verdictCounts.textContent = `BUY ${buys} · HOLD ${holds} · SELL ${sells}`;
    const finalMemo = state.memo.get("final_verdict") ?? state.memo.get("executive_summary");
    if (finalMemo) {
      setMarkdown(dom.verdictCopy, asString(finalMemo.content));
    } else {
      dom.verdictCopy.replaceChildren();
    }
  }

  function onDebateCompleted() {
    state.terminalSeen = true;
    showVerdictSummary();
    dom.transcriptStatus.textContent = "Debate complete";
    setRunState(state.meta.replay ? "replay" : "completed");
    setConnection("closed", state.meta.replay ? "Replay loaded" : "Complete");
    state.eventSource?.close();
  }

  function onDebateError(payload) {
    state.terminalSeen = true;
    dom.errorBanner.hidden = false;
    const stage = asString(payload.stage, "unknown stage");
    dom.errorTitle.textContent = `Stopped during ${stage}`;
    const recoverable = payload.recoverable ? "The run may be resumable." : "The recorded result may be incomplete.";
    dom.errorMessage.textContent = `${asString(payload.message, "No error detail was recorded")} ${recoverable}`;
    dom.transcriptStatus.textContent = "Debate ended with an error";
    setRunState("error");
    setConnection("error", "Run error");
    state.eventSource?.close();
  }

  function dispatchEnvelope(envelope) {
    const payload = envelope.payload;
    switch (envelope.type) {
      case "debate_started": onDebateStarted(payload); break;
      case "data_ready": onDataReady(payload); break;
      case "phase_started": onPhaseStarted(payload); break;
      case "phase_completed": onPhaseCompleted(payload); break;
      case "debate_completed": onDebateCompleted(payload); break;
      case "debate_error": onDebateError(payload); break;
      case "turn_started": onTurnStarted(payload); break;
      case "think_delta": onThinkDelta(payload); break;
      case "think_completed": onThinkCompleted(payload); break;
      case "talk_delta": onTalkDelta(payload); break;
      case "talk_completed": onTalkCompleted(payload); break;
      case "cognitive_state": onCognitiveState(payload); break;
      case "turn_completed": onTurnCompleted(payload); break;
      case "turn_interrupted": onTurnInterrupted(payload); break;
      case "steering_submitted": onSteeringSubmitted(payload); break;
      case "steering_delivered": onSteeringDelivered(payload); break;
      case "steering_dropped": onSteeringDropped(payload); break;
      case "thesis_recorded": onThesisRecorded(payload); break;
      case "vote_recorded": onVoteRecorded(payload); break;
      case "scorecard": onScorecard(payload); break;
      case "memo_section": onMemoSection(payload); break;
      case "disagreement": onDisagreement(payload); break;
      case "collapse_metric": onCollapseMetric(payload); break;
      case "usage":
      case "usage_window":
        // Usage is preserved in the event log and exports; the Town Hall has no cost rail.
        break;
      default:
        // Schema v1 is additive: unknown event types are deliberately ignored.
        return;
    }
  }

  function isNearBottom() {
    const remaining = document.documentElement.scrollHeight - window.scrollY - window.innerHeight;
    return remaining < 180;
  }

  function updateJumpToLive() {
    dom.jumpLive.hidden = isNearBottom() || state.lastSeq === 0;
  }

  function maybeMarkTruncatedReplay() {
    if (
      state.meta.replay
      && !state.terminalSeen
      && state.lastSeq >= state.meta.seqHigh
    ) {
      window.setTimeout(() => {
        if (state.terminalSeen || state.lastSeq < state.meta.seqHigh) {
          return;
        }
        dom.truncatedBanner.hidden = false;
        dom.transcriptStatus.textContent = "Replay log ends mid-run";
        setConnection("closed", "Incomplete replay");
        state.eventSource?.close();
      }, 250);
    }
  }

  function handleSseMessage(message) {
    let envelope;
    try {
      envelope = JSON.parse(message.data);
    } catch (_error) {
      return;
    }
    if (
      !isRecord(envelope)
      || envelope.v !== SCHEMA_VERSION
      || !Number.isInteger(envelope.seq)
      || typeof envelope.type !== "string"
      || !isRecord(envelope.payload)
      || envelope.seq <= state.lastSeq
    ) {
      return;
    }

    const follow = isNearBottom() && !state.meta.replay;
    state.lastSeq = envelope.seq;
    dom.sequence.textContent = String(state.lastSeq);
    trackEventTime(asString(envelope.ts));
    dispatchEnvelope(envelope);
    maybeMarkTruncatedReplay();

    if (follow) {
      window.requestAnimationFrame(() => window.scrollTo({ top: document.documentElement.scrollHeight }));
    } else {
      updateJumpToLive();
    }
  }

  function openEventStream() {
    const eventSource = new EventSource("/events?from_seq=0");
    state.eventSource = eventSource;
    // Servers may set `event:<type>` or omit it; support both wire forms.
    for (const eventType of AUTHORITATIVE_EVENT_TYPES) {
      eventSource.addEventListener(eventType, handleSseMessage);
    }
    eventSource.onmessage = handleSseMessage;
    eventSource.onopen = () => {
      setConnection("live", state.meta.replay ? "Reading replay" : "Live stream");
    };
    eventSource.onerror = () => {
      if (state.terminalSeen) {
        return;
      }
      if (eventSource.readyState === EventSource.CLOSED) {
        setConnection("closed", "Stream closed");
      } else {
        setConnection("connecting", "Reconnecting");
      }
    };
  }

  async function loadMeta() {
    const response = await fetch("/api/meta", {
      method: "GET",
      headers: { Accept: "application/json" },
      credentials: "same-origin",
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error(`Metadata request failed (${response.status})`);
    }
    const meta = await response.json();
    if (!isRecord(meta)) {
      throw new Error("Metadata response was not an object");
    }
    state.meta.replay = Boolean(meta.replay) || meta.status === "replay";
    state.meta.seqHigh = Number.isInteger(meta.seq_high) ? meta.seq_high : 0;
    state.paused = Boolean(meta.paused);
    dom.pauseButton.textContent = state.paused ? "Resume" : "Pause";
    if (meta.ticker) {
      dom.ticker.textContent = asString(meta.ticker, "—");
    }
    if (state.meta.replay) {
      setRunState("replay");
    } else if (["live", "completed", "error"].includes(meta.status)) {
      setRunState(meta.status);
    }
  }

  function showToast(message) {
    window.clearTimeout(state.toastTimer);
    dom.toast.textContent = message;
    dom.toast.hidden = false;
    state.toastTimer = window.setTimeout(() => {
      dom.toast.hidden = true;
    }, 4200);
  }

  async function postJson(path, payload) {
    const response = await fetch(path, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      credentials: "same-origin",
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      let detail = "";
      try {
        const responseBody = await response.json();
        detail = isRecord(responseBody) ? asString(responseBody.error || responseBody.message) : "";
      } catch (_error) {
        detail = "";
      }
      if (response.status === 409 && detail === "replay_read_only") {
        state.meta.replay = true;
        setRunState("replay");
        throw new Error("Controls are unavailable in replay mode");
      }
      throw new Error(detail || `Request rejected (${response.status})`);
    }
    return response;
  }

  function setMode(mode) {
    state.mode = mode === "queue" ? "queue" : "steer";
    const steer = state.mode === "steer";
    dom.steerMode.setAttribute("aria-pressed", String(steer));
    dom.queueMode.setAttribute("aria-pressed", String(!steer));
    dom.steeringTarget.disabled = !steer || !controlsShouldBeEnabled(state.meta.status);
    if (!steer) {
      dom.steeringTarget.value = "";
    }
  }

  async function submitSteering() {
    const text = dom.steeringText.value.trim();
    if (!text) {
      dom.steeringText.focus();
      return;
    }
    const payload = { type: state.mode, text };
    if (state.mode === "steer" && dom.steeringTarget.value) {
      payload.target = dom.steeringTarget.value;
    }
    dom.sendButton.disabled = true;
    try {
      await postJson("/api/steering", payload);
      dom.steeringText.value = "";
      showToast(state.mode === "queue" ? "Instruction queued for the next phase." : "Steer submitted for the next turn.");
    } catch (error) {
      showToast(error.message);
    } finally {
      dom.sendButton.disabled = !controlsShouldBeEnabled(state.meta.status);
    }
  }

  function confirmAction(title, message, confirmationLabel) {
    if (typeof dom.confirmDialog.showModal !== "function") {
      return Promise.resolve(window.confirm(`${title}\n\n${message}`));
    }
    dom.confirmTitle.textContent = title;
    dom.confirmMessage.textContent = message;
    dom.confirmAccept.textContent = confirmationLabel;
    dom.confirmDialog.returnValue = "";
    dom.confirmDialog.showModal();
    return new Promise((resolve) => {
      dom.confirmDialog.addEventListener(
        "close",
        () => resolve(dom.confirmDialog.returnValue === "confirm"),
        { once: true },
      );
    });
  }

  async function sendControl(type, successMessage) {
    try {
      await postJson("/api/control", { type });
      showToast(successMessage);
      return true;
    } catch (error) {
      showToast(error.message);
      return false;
    }
  }

  function bindControls() {
    dom.steeringForm.addEventListener("submit", (event) => {
      event.preventDefault();
      void submitSteering();
    });
    dom.steeringText.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        void submitSteering();
      }
    });
    dom.steerMode.addEventListener("click", () => setMode("steer"));
    dom.queueMode.addEventListener("click", () => setMode("queue"));

    dom.thinkingToggle.addEventListener("click", () => {
      state.thinkingVisible = !state.thinkingVisible;
      dom.body.dataset.thinking = state.thinkingVisible ? "shown" : "hidden";
      dom.thinkingToggle.setAttribute("aria-pressed", String(state.thinkingVisible));
      dom.thinkingToggle.textContent = state.thinkingVisible ? "Hide reasoning" : "Show reasoning";
      for (const turn of state.turns.values()) {
        turn.thinkingDetails.open = state.thinkingVisible;
      }
    });

    dom.pauseButton.addEventListener("click", async () => {
      const next = state.paused ? "resume" : "pause";
      dom.pauseButton.disabled = true;
      if (await sendControl(next, next === "pause" ? "Pause requested." : "Resume requested.")) {
        state.paused = next === "pause";
        dom.pauseButton.textContent = state.paused ? "Resume" : "Pause";
      }
      dom.pauseButton.disabled = !controlsShouldBeEnabled(state.meta.status);
      dom.nextPhaseButton.disabled =
        !controlsShouldBeEnabled(state.meta.status) || !state.paused;
    });

    dom.nextPhaseButton.addEventListener("click", () => {
      void sendControl("next_phase", "Advance to the next phase requested.");
    });

    dom.interruptButton.addEventListener("click", async () => {
      const confirmed = await confirmAction(
        "Interrupt the current turn?",
        "The in-flight answer will be cancelled or discarded, and the speaker will retake the turn.",
        "Interrupt turn",
      );
      if (!confirmed) {
        return;
      }
      try {
        await postJson("/api/steering", {
          type: "interrupt",
          text: "Interrupt the current speaker and retake the turn.",
        });
        showToast("Interrupt requested.");
      } catch (error) {
        showToast(error.message);
      }
    });

    dom.stopButton.addEventListener("click", async () => {
      const confirmed = await confirmAction(
        "Stop this debate?",
        "TinyIC will request a graceful stop. The event log remains available for replay and export.",
        "Stop debate",
      );
      if (confirmed) {
        void sendControl("stop", "Graceful stop requested.");
      }
    });

    dom.jumpLive.addEventListener("click", () => {
      window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "smooth" });
      dom.jumpLive.hidden = true;
    });
    window.addEventListener("scroll", updateJumpToLive, { passive: true });
  }

  async function start() {
    setRunState("connecting");
    setConnection("connecting", "Connecting");
    bindControls();
    try {
      await loadMeta();
    } catch (error) {
      showToast(error.message);
      setConnection("connecting", "Metadata unavailable");
    }
    openEventStream();
    maybeMarkTruncatedReplay();
  }

  void start();
})();
