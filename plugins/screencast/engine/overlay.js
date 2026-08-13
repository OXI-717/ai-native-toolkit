// Injected via addInitScript({ content, arg: runId }). NOT an ES module.
// Defines idempotent overlay (chrome bar, cursor, ripple, caption) keyed by runId on <html>.
(function (runId) {
  const P = '__sc_' + runId + '_';
  const BAR = window[P + 'bar'] = (window[P + 'bar'] || 52);
  function ensure() {
    const root = document.documentElement;
    if (!root) return;
    if (!document.getElementById(P + 'style')) {
      const st = document.createElement('style'); st.id = P + 'style';
      st.textContent =
        '@keyframes ' + P + 'rip{to{transform:scale(7);opacity:0}}' +
        '@keyframes ' + P + 'shim{0%{background-position:-300px 0}100%{background-position:300px 0}}';
      (document.head || root).append(st);
    }
    if (window[P + 'chrome'] !== false && !document.getElementById(P + 'chrome')) {
      if (!document.getElementById(P + 'pad')) {
        const pad = document.createElement('style'); pad.id = P + 'pad';
        pad.textContent = 'html{padding-top:' + BAR + 'px!important;box-sizing:border-box}';
        (document.head || root).append(pad);
      }
      const bar = document.createElement('div'); bar.id = P + 'chrome';
      bar.style.cssText = 'position:fixed;z-index:2147483646;top:0;left:0;right:0;height:' + BAR + 'px;pointer-events:none;' +
        'display:flex;align-items:center;gap:12px;padding:0 16px;box-sizing:border-box;background:#e8e4dd;border-bottom:1px solid #cfc9bf;' +
        'font:500 14px/1 -apple-system,Segoe UI,Roboto,sans-serif';
      const dots = document.createElement('div'); dots.style.cssText = 'display:flex;gap:8px;flex:0 0 auto';
      dots.innerHTML = ['#ff5f57', '#febc2e', '#28c840'].map((c) => '<span style="width:13px;height:13px;border-radius:50%;background:' + c + '"></span>').join('');
      const pill = document.createElement('div'); pill.id = P + 'pill';
      pill.style.cssText = 'flex:1 1 auto;display:flex;align-items:center;gap:8px;height:32px;padding:0 14px;background:#fff;border-radius:999px;' +
        'color:#2b2b2b;box-shadow:inset 0 0 0 1px #d8d2c8;overflow:hidden;white-space:nowrap;background-size:300px 100%';
      pill.innerHTML = '<span style="opacity:.5">🔒</span><span id="' + P + 'url"></span>';
      bar.append(dots, pill); root.append(bar);
    }
    let cur = document.getElementById(P + 'cursor');
    if (!cur) {
      cur = document.createElement('div'); cur.id = P + 'cursor';
      cur.style.cssText = 'position:fixed;z-index:2147483647;width:22px;height:22px;left:-50px;top:-50px;pointer-events:none;' +
        'transition:left .05s linear,top .05s linear;background:rgba(0,0,0,.18);border:2px solid #fff;border-radius:50%;box-shadow:0 2px 6px rgba(0,0,0,.4)';
      root.append(cur);
    }
    let cap = document.getElementById(P + 'caption');
    if (!cap) {
      cap = document.createElement('div'); cap.id = P + 'caption';
      cap.style.cssText = 'position:fixed;z-index:2147483647;left:50%;top:' + (BAR + 14) + 'px;transform:translateX(-50%);max-width:80%;' +
        'padding:10px 20px;border-radius:999px;pointer-events:none;font:600 18px/1.3 -apple-system,Segoe UI,Roboto,sans-serif;color:#fff8ee;' +
        'background:rgba(27,37,38,.94);box-shadow:0 8px 24px rgba(0,0,0,.35);opacity:0;transition:opacity .25s ease;white-space:nowrap';
      root.append(cap);
    }
  }
  window[P + 'setChrome'] = (on) => { window[P + 'chrome'] = on; };
  window[P + 'moveCursor'] = (x, y) => { ensure(); const c = document.getElementById(P + 'cursor'); c.style.left = (x - 11) + 'px'; c.style.top = (y - 11) + 'px'; };
  window[P + 'ripple'] = (x, y) => {
    ensure(); const r = document.createElement('div');
    r.style.cssText = 'position:fixed;z-index:2147483646;left:' + (x - 6) + 'px;top:' + (y - 6) + 'px;width:12px;height:12px;border-radius:50%;' +
      'background:rgba(232,122,46,.55);pointer-events:none;animation:' + P + 'rip .6s ease-out forwards';
    document.documentElement.append(r); setTimeout(() => r.remove(), 650);
  };
  window[P + 'caption'] = (t) => { ensure(); const c = document.getElementById(P + 'caption'); c.textContent = t; c.style.opacity = t ? '1' : '0'; };
  window[P + 'seturl'] = (u, animate) => {
    ensure(); const t = document.getElementById(P + 'url'); if (!t) return;
    let s = u; try { const x = new URL(u); s = x.host + x.pathname; } catch {}
    if (s.length > 64) s = s.slice(0, 63) + '…';
    t.textContent = s;
    if (animate) { const pill = document.getElementById(P + 'pill'); if (pill) { pill.style.background = 'linear-gradient(90deg,#fff,#e9e9e9,#fff)'; pill.style.animation = P + 'shim .55s linear 1'; setTimeout(() => { pill.style.animation = ''; pill.style.background = '#fff'; }, 600); } }
  };
  if (document.documentElement) ensure(); else addEventListener('DOMContentLoaded', ensure);
})(arg);
