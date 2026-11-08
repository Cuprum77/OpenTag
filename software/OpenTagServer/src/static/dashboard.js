// ── Theme / Dark Mode ──
function initDarkMode() {
  const toggle = document.getElementById('theme-toggle');
  let isDark = false;
  try { isDark = localStorage.getItem('opentag_dark') === 'true'; } catch (e) { /* storage blocked */ }
  if (isDark) document.body.classList.add('dark');

  function updateIcon(dark) {
    if (toggle) toggle.textContent = dark ? '☀️' : '🌙';
  }
  updateIcon(isDark);

  if (toggle) {
    toggle.addEventListener('click', () => {
      const nowDark = !document.body.classList.contains('dark');
      document.body.classList.toggle('dark', nowDark);
      try { localStorage.setItem('opentag_dark', nowDark); } catch (e) { /* storage blocked */ }
      updateIcon(nowDark);
    });
  }
}

// Apply the theme before first paint.
initDarkMode();

// ── State ──
let map = null;
let markerLayer = null;
let polylineLayer = null;
let inventory = { devices: [], errors: [], warnings: [], max_retention_days: null };
const selectedDeviceTags = new Set(); // tag ids picked in the Devices tab
let editingDevice = null; // device being edited in the device modal (null = creating)

const AUTO_DEVICE_ICON = 'tag';
const DEFAULT_DEVICE_ICON = 'cube';
// Font Awesome 5 solid icons offered for devices (each needs a rule in static/fontawesome/fontawesome.css).
const DEVICE_ICONS = [
  'cube', 'key', 'suitcase-rolling', 'suitcase', 'briefcase', 'shopping-bag', 'wallet', 'glasses',
  'car', 'motorcycle', 'bicycle', 'truck', 'plane', 'paw', 'dog', 'cat', 'baby-carriage',
  'graduation-cap', 'home', 'laptop', 'mobile-alt', 'headphones', 'camera', 'umbrella',
  'gamepad', 'music', 'gift', 'box'
];

function faIcon(name, className, props) {
  return el('i', Object.assign({ className: 'fas fa-' + name + (className ? ' ' + className : ''), 'aria-hidden': 'true' }, props || {}));
}

function customDevices() {
  return (inventory.devices || []).filter(d => !d.auto_created);
}

function deviceIcon(device) {
  const auto = device.auto_created;
  const icon = auto ? AUTO_DEVICE_ICON : (DEVICE_ICONS.includes(device.icon) ? device.icon : DEFAULT_DEVICE_ICON);
  return faIcon(icon, 'device-icon' + (auto ? ' auto' : ''), {
    style: 'color:' + device.color,
    title: auto ? 'Single tag (not part of a custom device)' : 'Custom device'
  });
}

function allTags() {
  const tags = [];
  for (const device of inventory.devices || []) {
    for (const tag of device.tags || []) tags.push(Object.assign({ device }, tag));
  }
  return tags;
}

// ── DOM helpers ──
function el(tag, props, children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null) continue;
    if (key === 'className') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'style') node.style.cssText = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children || [])) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
  }
  return node;
}

function replaceChildren(host, children) {
  if (!host) return;
  host.textContent = '';
  for (const child of [].concat(children || [])) {
    if (child === null || child === undefined || child === false) continue;
    host.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
  }
}

function hint(text, isError) {
  return el('p', { className: 'hint', text, style: isError ? 'color: var(--text-error)' : null });
}

function plural(n, word) {
  return n + ' ' + word + (n === 1 ? '' : 's');
}

function formatTime(unix) {
  return unix ? new Date(unix * 1000).toLocaleString() : 'never';
}

function setFeedback(id, message, isError = false) {
  const node = document.getElementById(id);
  if (!node) return;
  node.textContent = message || '';
  node.classList.remove('feedback-success', 'feedback-error');
  if (message) node.classList.add(isError ? 'feedback-error' : 'feedback-success');
}

// ── Loading throbber ──
function withLoading(button, asyncFn) {
  return async () => {
    if (button.classList.contains('loading')) return;
    button.classList.add('loading');
    button.disabled = true;
    const originalText = button.textContent;
    replaceChildren(button, [el('span', { className: 'spinner' }), originalText]);
    try {
      await asyncFn();
    } finally {
      button.classList.remove('loading');
      button.disabled = false;
      button.textContent = originalText;
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
      headers: { 'Content-Type': 'application/json' },
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

async function postFile(path, file) {
  const body = new FormData();
  body.append('file', file);
  const res = await fetch(path, { method: 'POST', body, credentials: 'same-origin' });
  return readJson(res);
}

// ── Map ──
function initMap() {
  const mapHost = document.getElementById('map');
  if (!mapHost) return;
  if (!window.L) {
    replaceChildren(mapHost, hint('Map unavailable. Leaflet failed to load.', true));
    return;
  }
  map = L.map('map').setView([37.7749, -122.4194], 3);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap contributors'
  }).addTo(map);
  markerLayer = L.layerGroup().addTo(map);
  polylineLayer = L.layerGroup().addTo(map);
}

// ── Inventory (devices + tags) ──
async function refreshInventory() {
  try {
    inventory = await getJson('/api/inventory');
  } catch (err) {
    replaceChildren(document.getElementById('tags-container'), hint(String(err), true));
    return;
  }
  renderOverviewDevices();
  renderFetchSelects();
  renderAppleTagList();
  renderGoogleTagList();
  renderDevicesTagList();
  renderDevices();
  const problems = (inventory.errors || []).map(e => e.file + ': ' + e.error).concat(inventory.warnings || []);
  setFeedback('apple-feedback', problems.join(' | '), problems.length > 0);
}

function tagChip(tag) {
  return el('span', { className: 'device-tag-chip ' + tag.provider, text: tag.name + (tag.missing ? ' (missing keys)' : '') });
}

function renderOverviewDevices() {
  const host = document.getElementById('tags-container');
  const devices = inventory.devices || [];
  if (devices.length === 0) {
    replaceChildren(host, hint('No devices yet. Upload key files in the Keys tab.'));
    return;
  }
  replaceChildren(host, devices.map(device => {
    const seen = Math.max(0, ...device.tags.map(t => t.last_seen_unix || 0));
    return el('div', { className: 'tag-card', style: 'border-left: 4px solid ' + device.color }, [
      el('div', { className: 'tag-card-header' }, [deviceIcon(device), el('span', { className: 'tag-name', text: device.name })]),
      el('div', { className: 'device-tags' }, device.tags.map(tagChip)),
      el('div', { className: 'tag-meta', text: 'Last seen: ' + formatTime(seen) })
    ]);
  }));
}

function fillSelect(id, tags, labelFn) {
  const select = document.getElementById(id);
  if (!select) return;
  const previous = new Set(Array.from(select.selectedOptions || []).map(o => o.value));
  replaceChildren(select, tags.map(tag => {
    const option = el('option', { value: tag.id, text: labelFn(tag) });
    option.selected = previous.has(tag.id);
    return option;
  }));
}

function renderFetchSelects() {
  const tags = allTags().filter(t => !t.missing);
  fillSelect('apple-accessory-select', tags.filter(t => t.provider === 'apple'),
    t => t.name + ' (' + plural(t.key_count || 0, 'key') + ')');
  fillSelect('google-tag-select', tags.filter(t => t.provider === 'google'),
    t => t.name + (t.kind === 'compound' ? ' (' + plural((t.subtags || []).length, 'subtag') + ')' : ''));
}

function getMultiSelectedValues(selectId) {
  const select = document.getElementById(selectId);
  if (!select) return [];
  return Array.from(select.selectedOptions || []).map(o => o.value).filter(Boolean);
}

// ── Keys tab ──
function keyRow(provider, name, meta, details) {
  const header = el('div', { className: 'key-row-header' }, [
    el('div', { className: 'key-row-info' }, [
      el('span', { className: 'key-name', text: name }),
      el('span', { className: 'key-meta', text: meta })
    ])
  ]);
  const row = el('div', { className: 'key-row ' + provider }, [header]);
  if (details && details.length) {
    const panel = el('div', { className: 'key-row-details' }, details);
    const button = el('button', { type: 'button', className: 'key-row-expand-btn', text: '▼' });
    button.addEventListener('click', () => {
      button.textContent = panel.classList.toggle('open') ? '▲' : '▼';
    });
    header.appendChild(button);
    row.appendChild(panel);
  }
  return row;
}

function renderAppleTagList() {
  const tags = allTags().filter(t => t.provider === 'apple');
  replaceChildren(document.getElementById('apple-accessories-list'), tags.length === 0
    ? hint('No Apple tags uploaded yet.')
    : tags.map(t => keyRow('apple', t.name,
      plural(t.key_count || 0, 'key') + ' | ' + ((t.files || []).join(', ') || 'missing key file'))));
}

function renderGoogleTagList() {
  const tags = allTags().filter(t => t.provider === 'google');
  replaceChildren(document.getElementById('google-targets-list'), tags.length === 0
    ? hint('No Google tags found. Upload secrets.json, then sync with Google.')
    : tags.map(t => {
      const subtags = t.subtags || [];
      const meta = t.kind === 'compound'
        ? 'compound | ' + (t.key_count ? plural(t.key_count, 'key') + ' | ' : '') + plural(subtags.length, 'subtag')
        : 'tracker';
      return keyRow('google', t.name, meta, subtags.map(s => el('div', { className: 'key-subtag' }, [
        el('span', { className: 'key-subtag-name', text: s.name }),
        el('span', { className: 'key-subtag-count', text: plural(s.key_count || 0, 'key') })
      ])));
    }));
}

function fileRow(file) {
  return el('div', { className: 'file-row' }, [
    el('div', { className: 'file-info' }, [
      el('a', {
        className: 'file-name-link', text: file.filename, title: 'Download',
        href: '/api/keyfiles/' + encodeURIComponent(file.filename) + '/download'
      }),
      el('span', { text: file.size + ' bytes | ' + formatTime(file.updated_unix) })
    ]),
    el('button', { type: 'button', className: 'delete-btn', text: 'Delete', dataset: { filename: file.filename } })
  ]);
}

async function refreshFiles() {
  let files = [];
  try {
    files = (await getJson('/api/keyfiles')).files || [];
  } catch (err) { /* lists stay empty */ }
  const apple = files.filter(f => f.category === 'accessories');
  const google = files.filter(f => f.category === 'secrets');
  replaceChildren(document.getElementById('apple-files-list'), apple.map(fileRow));
  replaceChildren(document.getElementById('google-files-list'),
    google.length ? google.map(fileRow) : hint('No secrets.json uploaded.'));
  const googleHint = document.getElementById('google-upload-hint');
  if (googleHint) {
    googleHint.textContent = google.length
      ? 'Uploading a new secrets.json replaces the current one; tags that still exist keep their history.'
      : '';
  }
}

async function deleteKeyfile(filename) {
  const res = await fetch('/api/keyfiles/' + encodeURIComponent(filename), { method: 'DELETE', credentials: 'same-origin' });
  return readJson(res);
}

// ── Devices tab ──
function renderDevicesTagList() {
  const host = document.getElementById('devices-tag-list');
  const tags = allTags();
  for (const id of Array.from(selectedDeviceTags)) {
    if (!tags.some(t => t.id === id)) selectedDeviceTags.delete(id);
  }
  replaceChildren(host, tags.length === 0 ? hint('No tags available. Upload key files first.') : tags.map(tag => {
    const checkbox = el('input', { type: 'checkbox', value: tag.id });
    checkbox.checked = selectedDeviceTags.has(tag.id);
    checkbox.addEventListener('change', () => {
      if (checkbox.checked) selectedDeviceTags.add(tag.id); else selectedDeviceTags.delete(tag.id);
    });
    return el('label', { className: 'devices-tag-item ' + tag.provider }, [
      checkbox,
      deviceIcon(tag.device),
      el('span', { className: 'tag-item-name' }, [
        tag.name,
        tag.device.auto_created ? null : el('span', { className: 'tag-item-device', text: ' — in ' + tag.device.name })
      ]),
      el('span', { className: 'tag-item-provider ' + tag.provider, text: tag.provider })
    ]);
  }));
}

function renderDevices() {
  const host = document.getElementById('devices-container');
  const devices = customDevices();
  if (devices.length === 0) {
    replaceChildren(host, hint('No devices yet. Tick tags on the left and click "+ Create Device". ' +
      'A device can also hold a single tag if you want to give it its own name, icon or history retention.'));
    return;
  }
  const max = inventory.max_retention_days;
  replaceChildren(host, devices.map(device => {
    const retention = el('input', {
      type: 'number', min: '1', max: max || '', step: '1', style: 'width: 6em',
      placeholder: max ? String(max) : 'forever', value: device.retention_days || ''
    });
    const save = el('button', { type: 'button', text: 'Save' });
    save.addEventListener('click', withLoading(save, async () => {
      const raw = retention.value.trim();
      try {
        await sendJson('PUT', '/api/devices/' + encodeURIComponent(device.id), { retention_days: raw ? parseInt(raw, 10) : null });
        await refreshInventory();
      } catch (err) {
        alert('Could not save retention: ' + err.message);
      }
    }));
    const effective = device.effective_retention_days ? device.effective_retention_days + ' days' : 'forever';
    return el('div', { className: 'device-card', dataset: { deviceId: device.id } }, [
      el('div', { className: 'device-accent', style: 'background:' + device.color }),
      el('div', { className: 'device-body' }, [
        el('div', { className: 'device-header' }, [
          deviceIcon(device),
          el('div', { className: 'device-name', text: device.name }),
          el('div', { className: 'device-actions' }, [
            el('button', { type: 'button', text: 'Edit', onclick: () => openDeviceModal(device) }),
            el('button', {
              type: 'button', className: 'delete-btn', text: 'Ungroup',
              title: 'Dissolve this device; its tags are tracked on their own again',
              onclick: () => ungroupDevice(device)
            })
          ])
        ]),
        el('div', { className: 'device-tags' }, device.tags.map(tagChip)),
        el('label', { className: 'device-retention' }, [
          'Keep history for', retention, 'days',
          el('small', { text: '(effective: ' + effective + ')' }),
          save
        ])
      ])
    ]);
  }));
}

async function ungroupDevice(device) {
  const ok = await confirmDialog({
    title: 'Ungroup "' + device.name + '"?',
    body: [el('p', { text: 'Its ' + plural(device.tags.length, 'tag') + ' will be tracked on their own again. ' +
      'Location history is kept; the device name, icon and retention setting are removed.' })],
    confirmLabel: 'Ungroup'
  });
  if (!ok) return;
  try {
    await sendJson('DELETE', '/api/devices/' + encodeURIComponent(device.id));
    await refreshInventory();
  } catch (err) {
    alert('Failed to ungroup device: ' + err.message);
  }
}

// ── Device Modal (create / edit) ──
function setModalColor(color) {
  const swatches = document.querySelectorAll('.color-swatch');
  const custom = document.getElementById('device-custom-color');
  let matched = false;
  swatches.forEach(s => {
    const hit = !matched && s.dataset.color.toLowerCase() === String(color).toLowerCase();
    s.classList.toggle('active', hit);
    matched = matched || hit;
  });
  if (custom) {
    custom.value = color;
    custom.classList.toggle('active', !matched);
  }
}

function setModalIcon(icon) {
  document.querySelectorAll('#device-icon-picker .icon-option').forEach(b => {
    b.classList.toggle('active', b.dataset.icon === icon);
  });
}

function selectedIcon() {
  const active = document.querySelector('#device-icon-picker .icon-option.active');
  return active ? active.dataset.icon : DEFAULT_DEVICE_ICON;
}

function openDeviceModal(device) {
  editingDevice = device || null;
  if (!editingDevice && selectedDeviceTags.size < 1) {
    alert('Select the tags that belong to this device first.');
    return;
  }
  document.getElementById('device-modal-title').textContent = editingDevice ? 'Edit Device' : 'Create Device';
  document.getElementById('modal-create-btn').textContent = editingDevice ? 'Save' : 'Create Device';
  document.getElementById('device-name-input').value = editingDevice ? editingDevice.name : '';
  const firstSwatch = document.querySelector('.color-swatch');
  setModalColor(editingDevice ? editingDevice.color : (firstSwatch ? firstSwatch.dataset.color : '#ff961f'));
  setModalIcon(editingDevice && DEVICE_ICONS.includes(editingDevice.icon) ? editingDevice.icon : DEFAULT_DEVICE_ICON);

  const tags = editingDevice ? editingDevice.tags : allTags().filter(t => selectedDeviceTags.has(t.id));
  replaceChildren(document.getElementById('device-tag-checklist'), tags.map(tag =>
    el('div', { className: 'tag-checklist-item ' + tag.provider }, [
      el('span', { className: 'tag-label', text: tag.name }),
      el('span', { className: 'tag-provider ' + tag.provider, text: tag.provider })
    ])));
  updateDeviceModalState();
  document.getElementById('build-device-modal').style.display = 'flex';
  document.getElementById('device-name-input').focus();
}

function closeBuildDeviceModal() {
  document.getElementById('build-device-modal').style.display = 'none';
  editingDevice = null;
}

function updateDeviceModalState() {
  const count = editingDevice ? editingDevice.tags.length : selectedDeviceTags.size;
  const label = document.getElementById('device-tags-label');
  if (label) label.textContent = (editingDevice ? 'Tags: ' : 'Selected tags: ') + count;
  const name = document.getElementById('device-name-input').value.trim();
  document.getElementById('modal-create-btn').disabled = !(count >= 1 && name);
}

function selectedColor() {
  const custom = document.getElementById('device-custom-color');
  if (custom && custom.classList.contains('active')) return custom.value;
  const active = document.querySelector('.color-swatch.active');
  return active ? active.dataset.color : '#ff961f';
}

async function saveDeviceModal() {
  const name = document.getElementById('device-name-input').value.trim();
  if (!name) return;
  const fields = { name, color: selectedColor(), icon: selectedIcon() };
  try {
    if (editingDevice) {
      await sendJson('PUT', '/api/devices/' + encodeURIComponent(editingDevice.id), fields);
    } else {
      if (selectedDeviceTags.size < 1) return;
      await postJson('/api/devices', Object.assign(fields, { tag_ids: Array.from(selectedDeviceTags) }));
      selectedDeviceTags.clear();
    }
    closeBuildDeviceModal();
    await refreshInventory();
  } catch (err) {
    alert('Failed to save device: ' + err.message);
  }
}

function initBuildDeviceModal() {
  const modal = document.getElementById('build-device-modal');
  if (!modal) return;
  document.getElementById('build-device-btn').addEventListener('click', () => openDeviceModal(null));
  document.getElementById('modal-close-btn').addEventListener('click', closeBuildDeviceModal);
  document.getElementById('modal-cancel-btn').addEventListener('click', closeBuildDeviceModal);
  document.getElementById('modal-create-btn').addEventListener('click', saveDeviceModal);
  document.getElementById('device-name-input').addEventListener('input', updateDeviceModalState);

  const swatches = document.querySelectorAll('.color-swatch');
  const custom = document.getElementById('device-custom-color');
  swatches.forEach(swatch => swatch.addEventListener('click', () => setModalColor(swatch.dataset.color)));
  if (custom) {
    custom.addEventListener('input', () => {
      swatches.forEach(s => s.classList.remove('active'));
      custom.classList.add('active');
    });
  }
  replaceChildren(document.getElementById('device-icon-picker'), DEVICE_ICONS.map(icon =>
    el('button', {
      type: 'button', className: 'icon-option', title: icon.replace(/-/g, ' '),
      dataset: { icon }, onclick: () => setModalIcon(icon)
    }, faIcon(icon))));
  modal.addEventListener('click', e => { if (e.target === modal) closeBuildDeviceModal(); });
}

// ── Confirm dialog ──
function confirmDialog({ title, body, confirmLabel = 'Delete' }) {
  const modal = document.getElementById('confirm-modal');
  document.getElementById('confirm-modal-title').textContent = title;
  replaceChildren(document.getElementById('confirm-modal-body'), body);
  const ok = document.getElementById('confirm-ok-btn');
  ok.textContent = confirmLabel;
  modal.style.display = 'flex';
  ok.focus();
  return new Promise(resolve => {
    const controller = new AbortController();
    const finish = result => {
      controller.abort();
      modal.style.display = 'none';
      resolve(result);
    };
    const opts = { signal: controller.signal };
    ok.addEventListener('click', () => finish(true), opts);
    document.getElementById('confirm-cancel-btn').addEventListener('click', () => finish(false), opts);
    document.getElementById('confirm-close-btn').addEventListener('click', () => finish(false), opts);
    modal.addEventListener('click', e => { if (e.target === modal) finish(false); }, opts);
    document.addEventListener('keydown', e => { if (e.key === 'Escape') finish(false); }, opts);
  });
}

function deletePreviewBody(preview) {
  const nodes = [];
  if (preview.devices.length === 0) {
    nodes.push(el('p', { text: 'No tags are removed: every tag in this file is also in another key file.' }));
    return nodes;
  }
  nodes.push(el('p', { className: 'confirm-summary', text: 'This permanently deletes ' +
    plural(preview.tags_removed, 'tag') + ' and ' + plural(preview.events_removed, 'stored location') + ':' }));
  for (const device of preview.devices) {
    const tagNames = device.tags.map(t => t.name + ' (' + t.provider + ')').join(', ');
    let what;
    if (!device.custom) what = 'Tag is removed';
    else if (device.deleted) what = 'Device is deleted (all ' + plural(device.tag_count, 'tag') + ')';
    else what = 'Device keeps ' + plural(device.tag_count - device.tags.length, 'tag') + ' but loses ' + tagNames;
    nodes.push(el('div', { className: 'confirm-device' }, [
      deviceIcon({ auto_created: !device.custom, icon: device.icon, color: device.color }),
      el('div', {}, [
        el('strong', { text: device.name }),
        el('p', { text: what }),
        device.custom && !device.deleted ? null : el('p', { text: tagNames }),
        el('p', { text: plural(device.events, 'location') + ' of history lost' })
      ])
    ]));
  }
  return nodes;
}

// ── Status ──
const STATUS_ROWS = [
  { key: 'apple', label: 'Apple fetch' },
  { key: 'google', label: 'Google fetch' },
  { key: 'google_keys', label: 'Google key announcement' }
];

function statusBadge(dot, label, text, extra) {
  return el('div', { className: 'status-badge' }, [
    el('span', { className: 'status-dot ' + dot }),
    el('span', { className: 'status-label', text: label }),
    el('span', { text }),
    extra ? el('span', { className: 'status-time', text: extra }) : null
  ]);
}

function gapRecoveryBadge(gap, last) {
  if (!gap || !gap.enabled) return statusBadge('none', 'Apple gap recovery', 'Off (apple_gap_recovery)');
  let text;
  if (gap.running) {
    const left = Math.max(0, gap.total - gap.done);
    const minutes = Math.ceil(left * (gap.request_interval_seconds || 60) / 60);
    text = 'Pass ' + gap.done + '/' + gap.total + ' keys, ~' + minutes + ' min left';
  } else if (gap.next_pass_unix) {
    text = 'Last pass covered ' + plural(gap.total, 'key') + '; next ' + formatTime(gap.next_pass_unix);
  } else {
    text = 'Starts when the Apple queue is idle';
  }
  const dot = last && last.ok === false ? 'error' : (gap.total ? 'ok' : 'none');
  return statusBadge(dot, 'Apple gap recovery', last && last.ok === false ? text + ' | ' + last.error : text);
}

async function refreshStatus() {
  const host = document.getElementById('status-container');
  let data;
  try {
    data = await getJson('/api/status');
  } catch (err) {
    replaceChildren(host, hint(String(err), true));
    return;
  }
  const rows = STATUS_ROWS.map(({ key, label }) => {
    const status = data[key];
    if (!status) return statusBadge('none', label, 'No data yet');
    let text = status.ok ? (status.kind || 'OK').replace(/_/g, ' ') : (status.error || 'Failed');
    let extra = formatTime(status.timestamp_unix);
    if (key === 'google_keys' && data.google_keys_schedule && data.google_keys_schedule.next_due_unix) {
      extra += ' | next ' + formatTime(data.google_keys_schedule.next_due_unix);
    }
    return statusBadge(status.ok ? 'ok' : 'error', label, text, extra);
  });
  rows.splice(1, 0, gapRecoveryBadge(data.gap_recovery, data.apple_singleton));
  replaceChildren(host, rows);
}

// ── Alerts (sticky until dismissed) ──
async function refreshAlerts() {
  const host = document.getElementById('errors-container');
  let alerts;
  try {
    alerts = (await getJson('/api/alerts')).alerts || [];
  } catch (err) {
    replaceChildren(host, hint(String(err), true));
    return;
  }
  if (alerts.length === 0) {
    replaceChildren(host, hint('No alerts'));
    return;
  }
  replaceChildren(host, alerts.map(alert => {
    const dismiss = el('button', { type: 'button', className: 'delete-btn', text: '×', title: 'Dismiss', style: 'float: right; padding: 0 8px' });
    dismiss.addEventListener('click', async () => {
      try {
        await postJson('/api/alerts/' + alert.id + '/dismiss');
      } finally {
        refreshAlerts();
      }
    });
    const when = alert.count > 1
      ? plural(alert.count, 'time') + ', last ' + formatTime(alert.last_unix)
      : formatTime(alert.last_unix);
    return el('div', { className: 'error-item' }, [
      dismiss,
      el('strong', { text: alert.provider }), ': ', alert.message,
      alert.type ? el('span', { className: 'alert-type', text: ' ' + alert.type.replace(/_/g, ' ') }) : null,
      alert.target ? el('span', { className: 'alert-target', text: ' [' + alert.target + ']' }) : null,
      el('br'), el('small', { text: when })
    ]);
  }));
}

// ── History ──
function renderHistoryMap(events, options = {}) {
  if (!map || !markerLayer || !window.L) return;
  markerLayer.clearLayers();
  if (polylineLayer) polylineLayer.clearLayers();

  const sorted = [...(events || [])].sort((a, b) => (a.timestamp_unix || 0) - (b.timestamp_unix || 0));
  if (!sorted.length) return;

  const highlightLatest = options.highlightLatest !== false;
  const latestByProvider = {};
  for (const event of sorted) {
    const ts = event.timestamp_unix || 0;
    if (!latestByProvider[event.provider] || ts > latestByProvider[event.provider]) latestByProvider[event.provider] = ts;
  }

  const points = [];
  for (const event of sorted) {
    if (typeof event.latitude !== 'number' || typeof event.longitude !== 'number') continue;
    points.push([event.latitude, event.longitude]);
    const isLatest = (event.timestamp_unix || 0) === latestByProvider[event.provider];
    let color = event.provider === 'google' ? '#1f78ff' : '#18a96b';
    let fillOpacity = 0.8;
    let radius = 6;
    let weight = 1;
    if (highlightLatest && !isLatest) {
      fillOpacity = 0.3;
    } else if (highlightLatest) {
      color = event.provider === 'google' ? '#1fcbff' : '#29ffa2';
      fillOpacity = 1.0;
      radius = 8;
      weight = 2;
    }
    const popup = el('div', {}, [
      el('strong', { text: event.tag || 'tag' }), ' (' + event.provider + ')', el('br'),
      formatTime(event.timestamp_unix),
      event.part ? el('div', { text: event.part }) : null,
      isLatest && highlightLatest ? el('div', { text: 'Latest' }) : null
    ]);
    L.circleMarker([event.latitude, event.longitude], { radius, color, fillColor: color, fillOpacity, weight })
      .bindPopup(popup).addTo(markerLayer);
  }

  if (options.connectDots && points.length > 1) {
    L.polyline(points, { color: '#ff8c00', weight: 2, opacity: 0.5, dashArray: '5, 8' }).addTo(polylineLayer);
  }
  if (points.length > 0) map.fitBounds(points, { padding: [20, 20], maxZoom: 14 });
}

async function refreshHistory() {
  const slider = document.getElementById('history-days-slider');
  const days = slider ? parseInt(slider.value, 10) : 7;
  try {
    const data = await getJson('/api/history?days=' + encodeURIComponent(days));
    renderHistoryMap(data.events || [], {
      connectDots: document.getElementById('connect-dots-toggle')?.checked || false,
      highlightLatest: document.getElementById('highlight-latest-toggle')?.checked !== false
    });
  } catch (err) {
    console.error('refreshHistory', err);
  }
}

// ── Jobs (server-side work with progress) ──
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const knownActiveJobs = new Set();
let jobsTimer = null;

function describeJob(job) {
  const progress = job.total > 1 && job.active ? ' (' + job.progress + '/' + job.total + ' done)' : '';
  const who = job.trigger === 'auto' ? ' (automatic)' : job.trigger === 'upload' ? ' (after upload)' : '';
  let text = job.label + who + ': ' + (job.state === 'queued' ? 'waiting in queue' : job.stage) + progress;
  if (!job.active && job.error) text += ' | ' + job.error;
  return text;
}

function jobRow(job) {
  const pct = job.total > 0 ? Math.round(100 * job.progress / job.total) : (job.active ? 0 : 100);
  return el('div', { className: 'job-row job-' + job.state }, [
    el('div', { className: 'job-text', text: describeJob(job) }),
    el('div', { className: 'job-bar' }, el('div', { className: 'job-bar-fill', style: 'width:' + pct + '%' }))
  ]);
}

async function refreshJobs() {
  clearTimeout(jobsTimer);
  let list = [];
  try {
    list = (await getJson('/api/jobs')).jobs || [];
  } catch (err) { /* try again later */ }
  const active = list.filter(j => j.active);
  replaceChildren(document.getElementById('jobs-container'), active.map(jobRow));
  // A job finished since the last look (e.g. an automatic one): show its results.
  const finished = [...knownActiveJobs].filter(id => !active.some(j => j.id === id));
  knownActiveJobs.clear();
  active.forEach(j => knownActiveJobs.add(j.id));
  if (finished.length) refreshAfterJobs();
  jobsTimer = setTimeout(refreshJobs, active.length ? 2000 : 20000);
}

function refreshAfterJobs() {
  return Promise.all([refreshStatus(), refreshHistory(), refreshAlerts(), refreshInventory()]);
}

// Start a job and wait for it, reporting progress through `onUpdate(job)`.
async function runJob(path, payload, onUpdate) {
  let job = (await postJson(path, payload || {})).job;
  knownActiveJobs.add(job.id);
  refreshJobs();
  while (job.active) {
    if (onUpdate) onUpdate(job);
    await sleep(1500);
    try {
      job = (await getJson('/api/jobs/' + job.id)).job;
    } catch (err) {
      if (onUpdate) onUpdate(Object.assign({}, job, { stage: 'Lost contact with the server, retrying…' }));
    }
  }
  if (onUpdate) onUpdate(job);
  return job;
}

// ── Actions ──
async function fetchAllSelected() {
  const appleSelected = getMultiSelectedValues('apple-accessory-select');
  const googleSelected = getMultiSelectedValues('google-tag-select');
  const feedback = (msg, isError) => setFeedback('google-targets-feedback', msg, isError);

  if (appleSelected.length === 0 && googleSelected.length === 0) {
    feedback('Nothing selected. Pick Apple or Google tags and run fetch.');
    return;
  }

  feedback('');  // live progress is shown in the job list above
  const run = (key, path, ids) => runJob(path, { tag_ids: ids })
    .catch(err => ({ state: 'failed', error: err.message, label: key }));
  const started = [];
  if (appleSelected.length > 0) started.push(run('Apple', '/api/apple/fetch', appleSelected));
  if (googleSelected.length > 0) started.push(run('Google', '/api/google/fetch', googleSelected));
  const results = await Promise.all(started);

  let stored = 0;
  const problems = [];
  for (const job of results) {
    stored += (job.result && job.result.stored) || 0;
    if (job.state !== 'done') problems.push((job.label || 'Job') + ': ' + (job.error || job.state));
  }
  if (problems.length) {
    feedback('Finished with errors (' + plural(stored, 'new location') + '). ' + problems.join(' | '), true);
  } else {
    feedback('Done: ' + plural(stored, 'new location') + '.');
  }
  await refreshAfterJobs();
}

async function syncWithGoogle(feedbackId) {
  try {
    const job = await runJob('/api/google/sync', {}, j => setFeedback(feedbackId, describeJob(j)));
    if (job.state !== 'done') throw new Error(job.error || 'Sync failed');
    const d = job.result || {};
    const parts = ['Synced with Google at ' + new Date().toLocaleTimeString()];
    if (d.devices_uploaded !== undefined) parts.push(plural(d.devices_uploaded, 'tracker') + ' announced');
    if (d.tags_added) parts.push(plural(d.tags_added, 'tag') + ' added');
    if (d.tags_removed) parts.push(plural(d.tags_removed, 'tag') + ' removed (no longer on Google)');
    setFeedback(feedbackId, parts.join(' | '));
  } catch (err) {
    setFeedback(feedbackId, err.message, true);
  }
  await Promise.all([refreshInventory(), refreshStatus(), refreshAlerts()]);
}

// ── Wiring ──
function bindClick(id, fn) {
  const button = document.getElementById(id);
  if (button) button.addEventListener('click', withLoading(button, fn));
}

function bindFileDelete(listId, feedbackId) {
  const list = document.getElementById(listId);
  if (!list) return;
  list.addEventListener('click', async evt => {
    const target = evt.target;
    if (!(target instanceof HTMLElement) || !target.classList.contains('delete-btn')) return;
    const filename = target.dataset.filename;
    if (!filename) return;
    let preview;
    try {
      preview = await getJson('/api/keyfiles/' + encodeURIComponent(filename) + '/delete-preview');
    } catch (err) {
      setFeedback(feedbackId, err.message, true);
      return;
    }
    const ok = await confirmDialog({ title: 'Delete ' + filename + '?', body: deletePreviewBody(preview) });
    if (!ok) return;
    try {
      const result = await deleteKeyfile(filename);
      setFeedback(feedbackId, 'Deleted ' + filename + (result.tags_removed ? ' (' + plural(result.tags_removed, 'tag') + ' removed)' : ''));
    } catch (err) {
      setFeedback(feedbackId, err.message, true);
    }
    await Promise.all([refreshFiles(), refreshInventory(), refreshHistory()]);
  });
}

function bindUpload(formId, inputId, path, feedbackId, describe, confirmFn) {
  const form = document.getElementById(formId);
  if (!form) return;
  form.addEventListener('submit', async evt => {
    evt.preventDefault();
    const input = document.getElementById(inputId);
    const file = input && input.files ? input.files[0] : null;
    if (!file) return;
    if (confirmFn && !confirmFn()) return;
    try {
      const result = await postFile(path, file);
      setFeedback(feedbackId, describe(result));
      input.value = '';
    } catch (err) {
      setFeedback(feedbackId, err.message, true);
    }
    await Promise.all([refreshFiles(), refreshInventory()]);
    refreshJobs();
  });
}

function initDashboard() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
      btn.classList.add('active');
      const tab = document.getElementById('tab-' + btn.dataset.tab);
      if (tab) tab.classList.add('active');
      if (btn.dataset.tab === 'overview' && map) setTimeout(() => map.invalidateSize(), 50);
    });
  });

  const slider = document.getElementById('history-days-slider');
  if (slider) {
    slider.max = String(inventory.max_retention_days || 30);
    slider.addEventListener('input', () => {
      document.getElementById('history-days-value').textContent = slider.value;
    });
    slider.addEventListener('change', refreshHistory);
  }
  document.getElementById('connect-dots-toggle')?.addEventListener('change', refreshHistory);
  document.getElementById('highlight-latest-toggle')?.addEventListener('change', refreshHistory);

  bindClick('reload-history', refreshHistory);
  bindClick('clear-alerts-btn', async () => {
    try { await postJson('/api/alerts/dismiss-all', {}); } finally { await refreshAlerts(); }
  });
  bindClick('fetch-selected-btn', fetchAllSelected);
  bindClick('refresh-keys-selected-btn', () => syncWithGoogle('google-targets-feedback'));
  bindClick('refresh-google-keys-btn', () => syncWithGoogle('google-feedback'));

  bindFileDelete('apple-files-list', 'apple-feedback');
  bindFileDelete('google-files-list', 'google-feedback');
  bindUpload('upload-accessories-form', 'accessories-file', '/api/upload/accessories', 'apple-feedback',
    r => 'Uploaded ' + plural(r.saved.items, 'accessory') + ' (' + plural(r.tags_added, 'new tag') + ')' +
      (r.job ? ' | fetching their locations now (see Actions on the Overview)' : '') +
      ((r.warnings || []).length ? ' | ' + r.warnings.join(' | ') : ''));
  bindUpload('upload-secrets-form', 'secrets-file', '/api/upload/secrets', 'google-feedback',
    r => (r.saved.replaced ? 'Replaced' : 'Uploaded') + ' secrets.json (' + plural(r.tags_added, 'tag') + ' added, ' +
      plural(r.tags_removed, 'tag') + ' removed)',
    () => {
      const hasSecrets = document.querySelector('#google-files-list .file-row');
      return !hasSecrets || window.confirm('Replace the current secrets.json? Google tags that are not in the new file are removed with their history.');
    });

  initMap();
  setTimeout(() => { if (map) map.invalidateSize(); }, 100);
  initBuildDeviceModal();

  refreshFiles();
  refreshInventory().then(() => {
    if (slider && inventory.max_retention_days) slider.max = String(inventory.max_retention_days);
  });
  refreshStatus();
  refreshAlerts();
  refreshHistory();
  refreshJobs();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initDashboard);
} else {
  initDashboard();
}
