// Shared by every page: theme, DOM helpers, menus and dialogs.
// Loaded in <head> (not deferred) so the theme is applied before the first paint.

// ── Theme ──
// Stored choice ('light' / 'dark'); without one, the system setting decides.
const THEME_KEY = 'opentag_theme';

function storedTheme() {
  try {
    const value = localStorage.getItem(THEME_KEY);
    if (value === 'light' || value === 'dark') return value;
    const legacy = localStorage.getItem('opentag_dark');  // before the orange theme
    if (legacy === 'true') return 'dark';
    if (legacy === 'false') return 'light';
  } catch (e) { /* storage blocked */ }
  return null;
}

function currentTheme() {
  return document.documentElement.dataset.theme ||
    (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  document.querySelectorAll('.theme-toggle-btn').forEach(button => {
    const dark = theme === 'dark';
    button.title = dark ? 'Switch to light mode' : 'Switch to dark mode';
    button.setAttribute('aria-label', button.title);
    replaceChildren(button, faIcon(dark ? 'sun' : 'moon'));
  });
}

document.documentElement.dataset.theme = storedTheme() || currentTheme();

document.addEventListener('DOMContentLoaded', () => {
  applyTheme(currentTheme());
  document.querySelectorAll('.theme-toggle-btn').forEach(button => button.addEventListener('click', () => {
    const next = currentTheme() === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* storage blocked */ }
    applyTheme(next);
    document.dispatchEvent(new CustomEvent('themechange', { detail: next }));
  }));
  document.addEventListener('click', evt => {
    if (!evt.target.closest('.popover-wrap')) closeMenus();
  });
  document.addEventListener('keydown', evt => { if (evt.key === 'Escape') closeMenus(); });
});

// ── DOM helpers ──
function el(tag, props, children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'className') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'style') node.style.cssText = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of [].concat(children ?? [])) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
  }
  return node;
}

function replaceChildren(host, children) {
  if (!host) return;
  host.textContent = '';
  for (const child of [].concat(children ?? [])) {
    if (child === null || child === undefined || child === false) continue;
    host.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
  }
}

function faIcon(name, className, props) {
  return el('i', Object.assign({ className: 'fas fa-' + name + (className ? ' ' + className : ''), 'aria-hidden': 'true' }, props || {}));
}

function hint(text, isError) {
  return el('p', { className: 'hint' + (isError ? ' is-error' : ''), text });
}

function plural(n, word, many) {
  return n + ' ' + (n === 1 ? word : (many || word + 's'));
}

function formatTime(unix) {
  return unix ? new Date(unix * 1000).toLocaleString() : 'never';
}

function formatDate(unix) {
  return unix ? new Date(unix * 1000).toLocaleDateString() : '—';
}

function relativeTime(unix) {
  if (!unix) return 'never';
  const s = Math.max(0, Date.now() / 1000 - unix);
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + ' min ago';
  if (s < 86400) return Math.floor(s / 3600) + ' h ago';
  return Math.floor(s / 86400) + ' d ago';
}

function relativeFuture(unix) {
  const s = unix - Date.now() / 1000;
  if (s <= 60) return 'due now';
  if (s < 3600) return 'in ' + Math.round(s / 60) + ' min';
  if (s < 86400) return 'in ' + Math.round(s / 3600) + ' h';
  return 'in ' + Math.round(s / 86400) + ' d';
}

function setFeedback(id, message, isError = false) {
  const node = typeof id === 'string' ? document.getElementById(id) : id;
  if (!node) return;
  node.textContent = message || '';
  node.classList.remove('feedback-success', 'feedback-error');
  if (message) node.classList.add(isError ? 'feedback-error' : 'feedback-success');
}

// Disable a button and show a spinner while `asyncFn` runs.
function withLoading(button, asyncFn) {
  return async (...args) => {
    if (button.classList.contains('loading')) return;
    button.classList.add('loading');
    button.disabled = true;
    const original = [...button.childNodes];
    replaceChildren(button, [el('span', { className: 'spinner' }), button.textContent.trim()]);
    try {
      await asyncFn(...args);
    } finally {
      button.classList.remove('loading');
      button.disabled = false;
      replaceChildren(button, original);
    }
  };
}

// ── HTTP ──
async function readJson(res) {
  let data = {};
  try { data = await res.json(); } catch (e) { /* non-JSON error page */ }
  if (res.status === 401) {
    window.location.href = '/auth/login';
    throw new Error('Session expired');
  }
  if (!res.ok) throw new Error(data.error || ('Request failed (' + res.status + ')'));
  return data;
}

async function getJson(path) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20000);
  try {
    const res = await fetch(path, { credentials: 'same-origin', signal: controller.signal });
    return await readJson(res);
  } catch (err) {
    if (err.name === 'AbortError') throw new Error('Loading server data timed out.');
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

async function sendJson(method, path, payload, timeoutMs = 30000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(path, {
      method,
      headers: payload === undefined ? {} : { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: payload === undefined ? undefined : JSON.stringify(payload),
      signal: controller.signal
    });
    return await readJson(res);
  } catch (err) {
    if (err.name === 'AbortError') throw new Error('The server did not answer in time.');
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

const postJson = (path, payload, timeoutMs) => sendJson('POST', path, payload || {}, timeoutMs);

// ── Popover menus (the ⋯ buttons) ──
function closeMenus(except) {
  document.querySelectorAll('.popover-menu').forEach(menu => {
    if (menu === except) return;
    menu.hidden = true;
    const button = menu.parentElement && menu.parentElement.querySelector('[aria-expanded]');
    if (button) button.setAttribute('aria-expanded', 'false');
  });
}

// items: [{ icon, label, onclick, danger }] (null entries are skipped)
function menuButton(items, { title = 'More actions', up = false } = {}) {
  const menu = el('div', { className: 'popover-menu' + (up ? ' up' : ''), role: 'menu', hidden: true },
    items.filter(Boolean).map(item => el('button', {
      type: 'button', role: 'menuitem', className: item.danger ? 'danger' : null,
      onclick: () => { closeMenus(); item.onclick(); }
    }, [item.icon ? faIcon(item.icon) : null, item.label])));
  const button = el('button', {
    type: 'button', className: 'icon-btn', title, 'aria-label': title, 'aria-haspopup': 'menu', 'aria-expanded': 'false',
    onclick: evt => {
      evt.stopPropagation();
      const open = menu.hidden;
      closeMenus(menu);
      menu.hidden = !open;
      button.setAttribute('aria-expanded', String(open));
      if (open) menu.querySelector('button')?.focus();
    }
  }, faIcon('ellipsis-h'));
  return el('div', { className: 'popover-wrap' }, [button, menu]);
}

// ── Dialogs ──
// openDialog({ title, body: [nodes], confirmLabel, danger, cancelLabel, onConfirm })
// onConfirm may be async; throwing shows the message in the dialog and keeps it open.
// Resolves to the onConfirm result (or true), or false when cancelled.
function openDialog({ title, body, confirmLabel = 'OK', danger = false, cancelLabel = 'Cancel', onConfirm, wide = false }) {
  return new Promise(resolve => {
    const error = el('p', { className: 'dialog-error', role: 'alert', hidden: true });
    const confirm = el('button', { type: 'submit', className: danger ? 'danger-btn' : 'primary-btn', text: confirmLabel });
    const cancel = el('button', { type: 'button', text: cancelLabel });
    const close = el('button', { type: 'button', className: 'icon-btn', title: 'Close', 'aria-label': 'Close' }, faIcon('times'));
    const form = el('form', { className: 'modal-dialog' + (wide ? ' wide' : ''), role: 'dialog', 'aria-modal': 'true', 'aria-label': title, novalidate: true }, [
      el('div', { className: 'modal-header' }, [el('h2', { text: title }), close]),
      el('div', { className: 'modal-body' }, [].concat(body || [], error)),
      el('div', { className: 'modal-footer' }, [cancel, confirm])
    ]);
    const overlay = el('div', { className: 'modal-overlay' }, form);
    const previous = document.activeElement;
    let done = false;
    const finish = result => {
      if (done) return;
      done = true;
      overlay.remove();
      document.removeEventListener('keydown', onKey, true);
      if (previous && previous.focus) previous.focus();
      resolve(result);
    };
    const onKey = evt => {
      if (evt.key === 'Escape') { evt.stopPropagation(); finish(false); }
    };
    form.addEventListener('submit', async evt => {
      evt.preventDefault();
      if (!form.checkValidity()) { form.reportValidity(); return; }
      error.hidden = true;
      if (!onConfirm) { finish(true); return; }
      confirm.disabled = true;
      try {
        const result = await onConfirm(form);
        finish(result === undefined ? true : result);
      } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
        confirm.disabled = false;
      }
    });
    cancel.addEventListener('click', () => finish(false));
    close.addEventListener('click', () => finish(false));
    overlay.addEventListener('mousedown', evt => { if (evt.target === overlay) finish(false); });
    document.addEventListener('keydown', onKey, true);
    document.body.appendChild(overlay);
    (form.querySelector('.modal-body input, .modal-body select') || confirm).focus();
  });
}

function confirmDialog({ title, body, confirmLabel = 'Delete', danger = true }) {
  return openDialog({ title, body, confirmLabel, danger });
}

function field(label, input, note) {
  return el('label', { className: 'field' }, [el('span', { className: 'field-label', text: label }), input, note ? el('small', { text: note }) : null]);
}
