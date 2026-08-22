/* icons.js — inline SVG icon set for VirusGPT UI.
 * Line-style, stroke-based, theme-aware via `currentColor` so every icon
 * inherits the active theme color (neon/amber/ice). Replaces the old emoji
 * glyphs with crisp, scalable vector icons.
 *
 * Usage (HTML):  <span class="vg-ico" data-ico="gear"></span>
 *                (filled by initIcons() on load)
 * Usage (JS):    el.innerHTML = VG_ICON('mic');          // returns SVG string
 *                setIcon('#btn-mic', 'stop', true);       // set + optional spin
 */
(function () {
  const S = 'currentColor';
  // 24x24 viewBox, stroke icons. `f` = filled variant where useful.
  const P = {
    gear: `<path d="M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7z"/><path d="M19.4 13a7.6 7.6 0 0 0 .05-2l2-1.5-2-3.5-2.4 1a7.6 7.6 0 0 0-1.7-1l-.4-2.5h-4l-.4 2.5a7.6 7.6 0 0 0-1.7 1l-2.4-1-2 3.5 2 1.5a7.6 7.6 0 0 0 0 2l-2 1.5 2 3.5 2.4-1a7.6 7.6 0 0 0 1.7 1l.4 2.5h4l.4-2.5a7.6 7.6 0 0 0 1.7-1l2.4 1 2-3.5-2-1.5z" fill="none"/>`,
    speaker_on: `<path d="M4 9v6h4l5 4V5L8 9H4z"/><path d="M16 8.5a4 4 0 0 1 0 7" fill="none"/><path d="M18.5 6a7 7 0 0 1 0 12" fill="none"/>`,
    speaker_off: `<path d="M4 9v6h4l5 4V5L8 9H4z"/><path d="M16 9l5 6M21 9l-5 6" fill="none"/>`,
    mic: `<rect x="9" y="3" width="6" height="11" rx="3" fill="none"/><path d="M5 11a7 7 0 0 0 14 0" fill="none"/><path d="M12 18v3"/>`,
    mic_off: `<rect x="9" y="3" width="6" height="11" rx="3" fill="none"/><path d="M5 11a7 7 0 0 0 14 0" fill="none"/><path d="M12 18v3"/><path d="M3 3l18 18"/>`,
    stop: `<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none"/>`,
    spark: `<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" fill="currentColor" stroke="none"/><path d="M18.5 16.5l.8 2.3 2.3.8-2.3.8-.8 2.3-.8-2.3-2.3-.8 2.3-.8z" fill="currentColor" stroke="none"/>`,
    brush: `<path d="M14 3l7 7-3 3-7-7 3-3z" fill="none"/><path d="M11 6L4 13c-1.5 1.5-2 4-2 5 1 0 3.5-.5 5-2l7-7" fill="none"/>`,
    send: `<path d="M5 12l14-7-5 14-3-5-6-2z" fill="currentColor" stroke="none"/>`,
    play: `<path d="M7 5l12 7-12 7V5z" fill="currentColor" stroke="none"/>`,
    robot: `<rect x="5" y="8" width="14" height="11" rx="3" fill="none"/><path d="M12 4v4" fill="none"/><circle cx="12" cy="3" r="1.3" fill="currentColor" stroke="none"/><path d="M9.5 13h.01M14.5 13h.01" fill="none"/><path d="M9 16h6" fill="none"/>`,
    brain: `<path d="M9 5a3 3 0 0 0-3 3 3 3 0 0 0-1 5.5A3 3 0 0 0 8 18a3 3 0 0 0 4 1 3 3 0 0 0 4-1 3 3 0 0 0 3-4.5A3 3 0 0 0 18 8a3 3 0 0 0-3-3 3 3 0 0 0-6 0z" fill="none"/><path d="M12 5v14M9 11h1.5M13.5 11H15M9 14h1.5M13.5 14H15" fill="none"/>`,
    clipboard: `<rect x="5" y="4" width="14" height="17" rx="2" fill="none"/><path d="M9 4a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2" fill="none"/><path d="M8 11h8M8 15h8M8 19h5" fill="none"/>`,
    wrench: `<path d="M15.5 5.5a3.5 3.5 0 0 0-4.7 4.2L4 16.5 7.5 20l6.8-6.8a3.5 3.5 0 0 0 4.2-4.7l-2.3 2.3-2.2-2.2 2.5-2.3z" fill="none"/>`,
    rocket: `<path d="M12 3c3 1.5 5 5 5 9l-3 3h-4l-3-3c0-4 2-7.5 5-9z" fill="none"/><circle cx="12" cy="9.5" r="1.6" fill="currentColor" stroke="none"/><path d="M9 16l-3 4M15 16l3 4M10 19h4" fill="none"/>`,
    refresh: `<path d="M20 11a8 8 0 0 0-14-4.5L4 8" fill="none"/><path d="M4 4v4h4" fill="none"/><path d="M4 13a8 8 0 0 0 14 4.5L20 16" fill="none"/><path d="M20 20v-4h-4" fill="none"/>`,
    close: `<path d="M6 6l12 12M18 6L6 18" fill="none"/>`,
    chat: `<path d="M4 5h16v11H9l-5 4V5z" fill="none"/><path d="M8 10h8M8 13h5" fill="none"/>`,
    dna: `<path d="M7 3c0 5 10 5 10 10M17 3c0 5-10 5-10 10M7 21c0-5 10-5 10-10M17 21c0-5-10-5-10-10" fill="none"/><path d="M8 6h8M8 18h8M9 9h6M9 15h6" fill="none"/>`,
    graph: `<circle cx="6" cy="6" r="2.4" fill="none"/><circle cx="18" cy="7" r="2.4" fill="none"/><circle cx="12" cy="17" r="2.4" fill="none"/><path d="M7.8 7.4l3 7.6M16.4 8.7l-3 6.8M8.2 6.5l7.6.8" fill="none"/>`,
    window: `<rect x="3" y="4" width="18" height="16" rx="2" fill="none"/><path d="M3 8h18" fill="none"/><circle cx="6" cy="6" r=".7" fill="currentColor" stroke="none"/><circle cx="8.5" cy="6" r=".7" fill="currentColor" stroke="none"/><circle cx="11" cy="6" r=".7" fill="currentColor" stroke="none"/>`,
    kanban: `<rect x="4" y="4" width="5" height="14" rx="1" fill="none"/><rect x="10.5" y="4" width="5" height="9" rx="1" fill="none"/><rect x="17" y="4" width="3.5" height="11" rx="1" fill="none"/>`,
    tools: `<path d="M14.5 5.5a3.5 3.5 0 0 0-4.7 4.2L4 16.5 7.5 20l6.8-6.8a3.5 3.5 0 0 0 4.2-4.7l-2.3 2.3-2.2-2.2 2.5-2.3z" fill="none"/><circle cx="15" cy="9" r="6" fill="none"/>`,
    check: `<path d="M4 12l5 5 11-11" fill="none"/>`,
    warn: `<path d="M12 3l9 16H3z" fill="none"/><path d="M12 9v5M12 17h.01" fill="none"/>`,
    star: `<path d="M12 3l2.6 5.5L20 9.3l-4 4 1 6-5-2.8L7 19.3l1-6-4-4 5.4-.8z" fill="none"/>`,
    cog_small: `<path d="M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2" fill="none"/>`,
  };

  function svg(name, cls) {
    const body = P[name] || P.gear;
    const extra = cls ? ` ${cls}` : '';
    return `<svg class="vg-svg${extra}" viewBox="0 0 24 24" width="1em" height="1em" ` +
      `fill="none" stroke="${S}" stroke-width="1.8" stroke-linecap="round" ` +
      `stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
  }

  window.VG_ICON = svg;
  window.setIcon = function (sel, name, spin) {
    const el = typeof sel === 'string' ? document.querySelector(sel) : sel;
    if (!el) return;
    el.innerHTML = svg(name);
    el.classList.toggle('vg-spin', !!spin);
  };

  // Fill any <span class="vg-ico" data-ico="name"></span> placeholders on load.
  window.initIcons = function () {
    document.querySelectorAll('.vg-ico[data-ico]').forEach(el => {
      el.innerHTML = svg(el.getAttribute('data-ico'), el.getAttribute('data-ico-cls') || '');
    });
  };
})();
