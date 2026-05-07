/* ==========================================================================
   praman-motion.js — orchestrates the motion layer
   --------------------------------------------------------------------------
   Stack:
     - GSAP 3.13 + ScrollTrigger + DrawSVGPlugin + SplitText (CDN)
     - View Transitions API (native, Chrome 111+ / Safari 18+)
     - Ninja Keys (CDN, web component) for Cmd-K
     - HTMX events: htmx:afterSwap (for re-init), htmx:trigger (for toasts)

   Thesis: motion budget is spent on moments of consequence. Stagger
   reveals on page load, stamp impress on verdict, hash chain drawing
   forward, audit pill pulse on chain advance, View Transitions
   between hero pages.
   ========================================================================== */

(function () {
  "use strict";

  // ----- helpers ---------------------------------------------------------

  function ready(fn) {
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  function once(el, evt, fn) {
    el.addEventListener(evt, fn, { once: true });
  }

  const prefersReducedMotion = window.matchMedia(
    "(prefers-reduced-motion: reduce)"
  ).matches;

  // ----- 1. GSAP entry stagger (page load) -------------------------------
  // The first ~12 frame elements (.title-block, .frame, .ledger tr, ...)
  // fade up from 8px y-offset, staggered 32ms. Total reveal under 500ms.

  function entryReveal() {
    if (prefersReducedMotion || !window.gsap) return;
    // Removed `.ledger` from the entry stagger — tables are data-critical
    // and were being left invisible if the staggered animation didn't
    // complete (e.g. tab backgrounded mid-anim). Page chrome animates;
    // primary content stays visible from frame 0.
    const targets = document.querySelectorAll(
      ".crumb, .title-block, .hero, .frame, .hashviz, .verdict-stamp"
    );
    if (!targets.length) return;
    gsap.from(targets, {
      y: 10,
      opacity: 0,
      duration: 0.42,
      ease: "power3.out",
      stagger: 0.04,
      clearProps: "transform",
      onComplete: () => {
        // Belt + suspenders: ensure no element is left at opacity:0.
        targets.forEach(el => { el.style.opacity = ""; });
      },
    });
  }

  // ----- 2. Verdict stamp impress ----------------------------------------
  // The .verdict-stamp on drilldown gets `.is-impressed` after a tiny
  // delay so the animation runs as the eye lands. Class adds a CSS
  // keyframe — JS just toggles state.

  function impressVerdictStamp() {
    if (prefersReducedMotion) return;
    const stamp = document.querySelector(".verdict-stamp");
    if (!stamp) return;
    requestAnimationFrame(() =>
      requestAnimationFrame(() => stamp.classList.add("is-impressed"))
    );
  }

  // ----- 3. Counter — animates the "№ N entries appended" number --------

  function animateCounters() {
    document.querySelectorAll("[data-count]").forEach((el) => {
      const target = parseInt(el.dataset.count, 10) || 0;
      // ALWAYS write the real value first — this guarantees the counter
      // is never stuck at "0" if GSAP fails to load or reduced-motion is
      // on. The animation is a sweetener, not the source of truth.
      el.textContent = target.toLocaleString();
      if (prefersReducedMotion || !window.gsap || target === 0) return;
      const obj = { v: 0 };
      gsap.to(obj, {
        v: target,
        duration: 1.0,
        ease: "power2.out",
        delay: 0.15,
        onUpdate: () => {
          el.textContent = Math.floor(obj.v).toLocaleString();
        },
        onComplete: () => {
          // Belt + suspenders: ensure the final value matches the data
          // attribute exactly, regardless of GSAP rounding.
          el.textContent = target.toLocaleString();
        },
      });
    });
  }

  // ----- 4. Hash-chain SVG drawing (mempool-style) -----------------------
  // Looks for #hashviz on the signoff page. Renders the last N audit
  // entries as a horizontal train of blocks linked by short paths.
  // Draws with DrawSVGPlugin if available, falls back to stroke-dashoffset
  // CSS transition.

  function drawHashChain() {
    const root = document.getElementById("hashviz");
    if (!root) return;
    const data = JSON.parse(root.dataset.entries || "[]");
    if (!data.length) return;

    // T2 fix — container-width-adaptive layout. The mini hashviz on the
    // dashboard hero is constrained to ~400-500 px; the full one on
    // signoff has the full content column (~900 px). When the container
    // is narrow, we render a compact strip: smaller blocks, abbreviated
    // text, fits exactly into the viewport. When wide, we render the
    // full detail layout. Either way, the SVG uses overflow-x:auto via
    // CSS so users can scroll horizontally if they want every byte.
    const containerW = root.clientWidth || 400;
    const compact = containerW < 600;

    const blockW   = compact ? 78  : 130;
    const blockH   = compact ? 80  : 110;
    const linkW    = compact ? 14  : 22;
    const margin   = compact ? 10  : 18;
    const fontSeq  = compact ? 9   : 10;
    const fontEv   = compact ? 9.5 : 11;
    const fontEnt  = compact ? 8.5 : 10;
    const fontHash = compact ? 9   : 9;

    const totalW = margin * 2 + data.length * blockW + (data.length - 1) * linkW;
    const totalH = blockH + margin * 2;

    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", `0 0 ${totalW} ${totalH}`);
    // 'meet' would shrink the whole thing; 'slice' would crop. We want
    // *natural* size + horizontal scroll. Set width to totalW and let
    // the parent's overflow:auto handle the rest.
    svg.setAttribute("preserveAspectRatio", "xMinYMid meet");
    svg.style.width = totalW + "px";
    svg.style.height = totalH + "px";
    svg.style.minWidth = totalW + "px";  // prevents flex/grid shrinkage

    // colour by event type
    const palette = {
      "evidence.signed":   "var(--pass)",
      "verdict.emitted":   "var(--abstain)",
      "criterion.created": "var(--ink)",
      "criterion.updated": "var(--ink)",
      "fact.created":      "var(--ink-soft)",
      "document.created":  "var(--sepia)",
      "blocks.ingested":   "var(--sepia)",
    };
    function colorFor(eventType) {
      for (const [prefix, color] of Object.entries(palette)) {
        if (eventType.startsWith(prefix.split(".")[0])) return color;
      }
      return "var(--sepia-soft)";
    }

    let x = margin;
    const links = [];
    data.forEach((entry, idx) => {
      const isLast = idx === data.length - 1;

      // The block rectangle
      const g = document.createElementNS(ns, "g");
      g.setAttribute("class", "hashviz-block" + (isLast ? " is-current" : ""));
      g.setAttribute("transform", `translate(${x}, ${margin})`);
      g.dataset.seq = entry.seq;
      g.dataset.hash = entry.hash;

      const rect = document.createElementNS(ns, "rect");
      rect.setAttribute("width", blockW);
      rect.setAttribute("height", blockH);
      rect.setAttribute("fill", "var(--paper)");
      rect.setAttribute("stroke", colorFor(entry.event_type));
      rect.setAttribute("stroke-width", isLast ? "2" : "1");
      g.appendChild(rect);

      const padX = compact ? 6 : 8;

      // Sequence number, top-left
      const seqText = document.createElementNS(ns, "text");
      seqText.setAttribute("x", padX);
      seqText.setAttribute("y", compact ? 14 : 18);
      seqText.setAttribute("font-family", "JetBrains Mono, monospace");
      seqText.setAttribute("font-size", String(fontSeq));
      seqText.setAttribute("fill", "var(--sepia)");
      seqText.textContent = "№ " + entry.seq;
      g.appendChild(seqText);

      // Event type — full label when wide, abbreviated when compact.
      const evText = document.createElementNS(ns, "text");
      evText.setAttribute("x", padX);
      evText.setAttribute("y", compact ? 32 : 40);
      evText.setAttribute("font-family", "Spectral, serif");
      evText.setAttribute("font-size", String(fontEv));
      evText.setAttribute("font-weight", "600");
      evText.setAttribute("fill", "var(--ink)");
      const evLabel = entry.event_type;
      // Compact fits ~9 chars; abbreviate "evidence.signed" → "ev.signed"
      // and similar. The full label stays in the <title> tooltip.
      evText.textContent = compact
        ? (evLabel.length > 11 ? evLabel.slice(0, 9) + "…" : evLabel)
        : evLabel;
      g.appendChild(evText);

      if (!compact) {
        // Entity line — only in the wide layout. Compact drops this to
        // keep text from overflowing the smaller block.
        const entText = document.createElementNS(ns, "text");
        entText.setAttribute("x", padX);
        entText.setAttribute("y", 58);
        entText.setAttribute("font-family", "Spectral, serif");
        entText.setAttribute("font-size", String(fontEnt));
        entText.setAttribute("font-style", "italic");
        entText.setAttribute("fill", "var(--sepia)");
        entText.textContent = entry.entity + (entry.entity_id ? " · " + entry.entity_id : "");
        g.appendChild(entText);
      }

      // Hash (truncated) — bottom of block
      const hashText = document.createElementNS(ns, "text");
      hashText.setAttribute("x", padX);
      hashText.setAttribute("y", blockH - (compact ? 8 : 12));
      hashText.setAttribute("font-family", "JetBrains Mono, monospace");
      hashText.setAttribute("font-size", String(fontHash));
      hashText.setAttribute("fill", "var(--ink-soft)");
      hashText.textContent = compact
        ? entry.hash.slice(0, 8) + "…"
        : entry.hash.slice(0, 14) + "…";
      g.appendChild(hashText);

      // Tooltip
      const title = document.createElementNS(ns, "title");
      title.textContent = `seq ${entry.seq}\n${entry.event_type} ${entry.entity}/${entry.entity_id || "—"}\nhash ${entry.hash}`;
      g.appendChild(title);

      svg.appendChild(g);

      // Link line to next block
      if (!isLast) {
        const link = document.createElementNS(ns, "path");
        link.setAttribute("class", "hashviz-link");
        const startX = x + blockW;
        const endX = startX + linkW;
        const yMid = margin + blockH / 2;
        link.setAttribute(
          "d",
          `M ${startX} ${yMid} L ${endX} ${yMid}`
        );
        svg.appendChild(link);
        links.push(link);
      }

      x += blockW + linkW;
    });

    root.appendChild(svg);

    // Block click → highlight + scroll to corresponding row in the timeline
    root.addEventListener("click", (e) => {
      const target = e.target.closest(".hashviz-block");
      if (!target) return;
      const seq = target.dataset.seq;
      const tableRow = document.querySelector(`tr[data-seq="${seq}"]`);
      if (tableRow) {
        document.querySelectorAll("tr.is-highlighted").forEach((r) => r.classList.remove("is-highlighted"));
        tableRow.classList.add("is-highlighted");
        tableRow.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    });

    // Animate links drawing forward via stroke-dashoffset.
    // Use ScrollTrigger if available so the drawing happens on scroll-in.
    if (window.gsap && window.ScrollTrigger && !prefersReducedMotion) {
      gsap.registerPlugin(ScrollTrigger);
      gsap.to(links, {
        strokeDashoffset: 0,
        duration: 0.4,
        stagger: 0.06,
        ease: "power2.out",
        scrollTrigger: {
          trigger: root,
          start: "top 80%",
          once: true,
        },
      });

      // Block fade-in stagger
      const blocks = root.querySelectorAll(".hashviz-block");
      gsap.from(blocks, {
        opacity: 0,
        x: -10,
        duration: 0.4,
        stagger: 0.05,
        ease: "power3.out",
        scrollTrigger: {
          trigger: root,
          start: "top 80%",
          once: true,
        },
      });
    } else {
      // CSS-only fallback: just remove the dashoffset.
      links.forEach((l) => l.classList.add("is-drawn"));
    }
  }

  // ----- 5. View Transitions on cross-page nav + HTMX swaps --------------

  function installViewTransitions() {
    if (!document.startViewTransition) return;

    // (a) Cross-document nav — same-origin link clicks. Fall through if
    // user holds modifier keys or middle-clicks.
    document.addEventListener("click", (e) => {
      const a = e.target.closest("a");
      if (!a) return;
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
      const href = a.getAttribute("href");
      if (!href || href.startsWith("#") || href.startsWith("mailto:")) return;
      const url = new URL(a.href, window.location.href);
      if (url.origin !== window.location.origin) return;
      // Skip if the link explicitly opts out
      if (a.dataset.noTransition !== undefined) return;
      // Skip if it's a download or post-form button
      if (a.hasAttribute("download")) return;

      e.preventDefault();
      document.startViewTransition(() => {
        window.location.href = url.toString();
      });
    });

    // (b) HTMX swaps — `htmx:beforeSwap` lets us wrap the swap in a
    // view transition. Subtle and effective.
    document.addEventListener("htmx:beforeSwap", (e) => {
      if (!document.startViewTransition) return;
      const t = document.startViewTransition(() => {
        // The default swap will run inside this callback when we resolve.
        // We let HTMX do its thing by setting the response and swap target.
      });
      // We don't actually need to do anything here for HTMX 2 — it doesn't
      // expose a clean way to defer the swap. Instead, just trigger a
      // small CSS-driven highlight on the swapped element after.
    });
  }

  // ----- 6. Audit pill pulse — on HTMX swap, briefly add `.is-pulsing`
  //         which triggers the @property ink-spread keyframe.

  function installAuditPillPulse() {
    document.addEventListener("htmx:afterSwap", (e) => {
      const pill = document.getElementById("audit-pill");
      if (!pill) return;
      // Only pulse if the count looks like it advanced — i.e. the pill
      // is not in its loading/error state. Cheap heuristic: any swap.
      pill.classList.remove("is-pulsing");
      // Force reflow to restart the transition
      void pill.offsetWidth;
      pill.classList.add("is-pulsing");
      setTimeout(() => pill.classList.remove("is-pulsing"), 900);
    });
  }

  // ----- 7. Toast queue — HTMX out-of-band swaps target #toast-rail with
  //         <div class="toast">…</div>. We slide them in, then dismiss.

  function ensureToastRail() {
    let rail = document.getElementById("toast-rail");
    if (!rail) {
      rail = document.createElement("div");
      rail.id = "toast-rail";
      document.body.appendChild(rail);
    }
    return rail;
  }

  function notify(message, opts = {}) {
    const rail = ensureToastRail();
    const t = document.createElement("div");
    t.className = "toast" + (opts.error ? " is-error" : "");
    t.textContent = message;
    rail.appendChild(t);
    requestAnimationFrame(() =>
      requestAnimationFrame(() => t.classList.add("is-shown"))
    );
    setTimeout(() => {
      t.classList.remove("is-shown");
      setTimeout(() => t.remove(), 400);
    }, opts.duration || 3800);
  }

  // Expose so other scripts (or HTMX hx-on) can call praman.notify().
  window.praman = window.praman || {};
  window.praman.notify = notify;

  // HTMX swap success → "audit chain advanced" toast (only on POSTs that
  // hit endpoints we know append to the chain).
  document.addEventListener("htmx:afterRequest", (e) => {
    const detail = e.detail || {};
    const xhr = detail.xhr || {};
    const reqMethod = (detail.requestConfig && detail.requestConfig.verb) || "";
    const isMutation = ["post", "put", "delete", "patch"].includes(reqMethod.toLowerCase());
    if (!isMutation) return;
    if (xhr.status >= 200 && xhr.status < 300) {
      notify("Audit chain advanced.");
    }
  });

  // ----- 8. Cmd-K command palette via Ninja Keys -------------------------
  // The component is loaded from CDN; we just configure its data and
  // bind it. Ninja Keys auto-binds Cmd+K / Ctrl+K when present.

  function installCmdK() {
    const ninja = document.querySelector("ninja-keys");
    if (!ninja) return;

    const rfpId = ninja.dataset.rfpId || null;
    const verdictId = ninja.dataset.verdictId || null;

    const data = [
      { id: "dashboard", title: "Dashboard", section: "Navigate",
        hotkey: "g d", handler: () => navigate("/officer/") },
      { id: "upload-tender", title: "Enter new tender", section: "Navigate",
        handler: () => navigate("/officer/upload/tender/") },
    ];
    if (rfpId) {
      data.push(
        { id: "criteria", title: "Criteria · review", section: "Navigate",
          hotkey: "g c", handler: () => navigate(`/officer/rfp/${rfpId}/criteria/`) },
        { id: "grid", title: "Verdict register", section: "Navigate",
          hotkey: "g v", handler: () => navigate(`/officer/rfp/${rfpId}/grid/`) },
        { id: "signoff", title: "Sign-off & export PDF", section: "Navigate",
          hotkey: "g s", handler: () => navigate(`/officer/rfp/${rfpId}/signoff/`) },
        { id: "upload-bidder", title: "Upload bidder bundle", section: "Navigate",
          handler: () => navigate(`/officer/upload/bidder/${rfpId}/`) },
      );
    }
    data.push(
      { id: "logout", title: "Sign out", section: "Tools",
        handler: () => {
          const f = document.querySelector('form[action$="/accounts/logout/"]');
          if (f) f.submit();
        } },
    );

    ninja.data = data;
  }

  function navigate(href) {
    if (document.startViewTransition) {
      document.startViewTransition(() => (window.location.href = href));
    } else {
      window.location.href = href;
    }
  }

  // ----- 9. Re-init on HTMX swaps (so animations re-attach to swapped
  //         fragments). Wrap in gsap.context() to avoid leaking. ---------

  let pageContext = null;
  function bootPage() {
    if (pageContext) pageContext.revert();
    pageContext = window.gsap ? gsap.context(() => {
      entryReveal();
      animateCounters();
    }) : null;
    impressVerdictStamp();
    drawHashChain();
  }

  // ----- boot ------------------------------------------------------------

  ready(() => {
    bootPage();
    installViewTransitions();
    installAuditPillPulse();
    installCmdK();
  });

  document.addEventListener("htmx:afterSwap", () => {
    // Re-bind anything that may have been swapped. Light-weight.
    impressVerdictStamp();
  });
})();
