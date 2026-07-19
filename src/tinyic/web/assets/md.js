/*
 * TinyIC shared Markdown subset renderer — escape-first, no raw-HTML passthrough.
 * Model/user text is escaped before a single Markdown token is interpreted;
 * only the renderer's own output is ever parsed back into the DOM.
 * Loaded before app.js/studio.js; exposes window.TinyICMarkdown.
 */
(function () {
  "use strict";

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

  window.TinyICMarkdown = {
    render: renderMarkdown,
    setInto: setMarkdown,
    escapeHtml: escapeHtml,
  };
})();
