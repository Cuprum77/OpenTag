// Dashboard: the Overview, Keys and Devices tabs. Needs common.js (helpers, theme, dialogs).

// ── State ──
let map = null;
let markerLayer = null;
let polylineLayer = null;
let inventory = { devices: [], errors: [], warnings: [], max_retention_days: null };
let keyFiles = [];

const AUTO_DEVICE_ICON = 'tag';
const DEFAULT_DEVICE_ICON = 'cube';
// Font Awesome 5 solid icons offered for devices (each needs a rule in static/fontawesome/fontawesome.css).
const DEVICE_ICONS = [
  'cube', 'key', 'suitcase-rolling', 'suitcase', 'briefcase', 'shopping-bag', 'wallet', 'glasses',
  'car', 'motorcycle', 'bicycle', 'truck', 'plane', 'paw', 'dog', 'cat', 'baby-carriage',
  'graduation-cap', 'home', 'laptop', 'mobile-alt', 'headphones', 'camera', 'umbrella',
  'gamepad', 'music', 'gift', 'box'
];
// Device colours: strong enough to carry a white icon on the map pins.
const DEVICE_COLORS = ['#1f6fe5', '#2e8b57', '#d63a2f', '#e07800', '#8e3fb5', '#00838f', '#795548', '#546e7a'];
const RETENTION_CHOICES = [1, 7, 14, 30, 90, 180, 365];
const STORE_DISMISSED = 'opentag_dismissed_suggestions';

function customDevices() {
  return (inventory.devices || []).filter(d => !d.auto_created);
}

// Any Font Awesome solid icon name is allowed (DEVICE_ICONS are just the quick picks).
const ICON_NAME_RE = /^[a-z0-9-]{1,40}$/;

function deviceIconName(device) {
  if (device.auto_created) return AUTO_DEVICE_ICON;
  return ICON_NAME_RE.test(device.icon || '') ? device.icon : DEFAULT_DEVICE_ICON;
}

// The full icon list (~50 KB) is only loaded when someone opens "All icons".
let iconIndexPromise = null;
function loadIconIndex() {
  if (!iconIndexPromise) {
    iconIndexPromise = new Promise((resolve, reject) => {
      if (typeof FA_SOLID_ICONS !== 'undefined') { resolve(FA_SOLID_ICONS); return; }
      const script = el('script', { src: '/static/fontawesome/icons.js' });
      script.onload = () => resolve(FA_SOLID_ICONS);
      script.onerror = () => { iconIndexPromise = null; reject(new Error('Could not load the icon list.')); };
      document.head.appendChild(script);
    });
  }
  return iconIndexPromise;
}

function deviceIcon(device) {
  return faIcon(deviceIconName(device), 'device-icon' + (device.auto_created ? ' auto' : ''), {
    style: 'color:' + device.color,
    title: device.auto_created ? 'Single tag (not part of a device)' : 'Device'
  });
}

function allTags() {
  const tags = [];
  for (const device of inventory.devices || []) {
    for (const tag of device.tags || []) tags.push(Object.assign({ device }, tag));
  }
  return tags;
}

function byName(a, b) {
  return a.name.localeCompare(b.name, undefined, { sensitivity: 'base', numeric: true });
}

function formatBytes(n) {
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1024 / 1024).toFixed(1) + ' MB';
}

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
  map = L.map('map', { zoomControl: false }).setView([37.7749, -122.4194], 3);
  L.control.zoom({ position: 'bottomright' }).addTo(map);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap contributors'
  }).addTo(map);
  polylineLayer = L.layerGroup().addTo(map);
  markerLayer = L.layerGroup().addTo(map);
  selectionLayer = L.layerGroup().addTo(map);
}

// ── Inventory (devices + tags) ──
async function refreshInventory() {
  try {
    inventory = await getJson('/api/inventory');
  } catch (err) {
    replaceChildren(document.getElementById('device-list'), hint(String(err), true));
    return;
  }
  renderOverview();
  renderKeysTab();
  renderDevicesTab();
}

// ═════════════════════════════ Keys tab ═════════════════════════════

function tagSummary(tag) {
  if (tag.missing) return 'key file missing';
  if (tag.provider === 'apple') return plural(tag.key_count || 0, 'key');
  if (tag.kind === 'compound') {
    return (tag.key_count ? plural(tag.key_count, 'key') + ' · ' : '') + plural((tag.subtags || []).length, 'subtag');
  }
  return 'single tracker';
}

// The device a tag belongs to, as a chip that leads to the Devices tab.
function deviceChip(device) {
  if (device.auto_created) return el('span', { className: 'muted', text: 'Not in a device' });
  return el('a', { className: 'device-chip', href: '#devices', title: 'Open the Devices tab' }, [
    faIcon(deviceIconName(device), null, { style: 'color:' + device.color }),
    el('span', { text: device.name })
  ]);
}

function keyTagRow(tag) {
  const subtags = tag.subtags || [];
  const expandable = tag.kind === 'compound' && subtags.length > 0;
  const panel = expandable ? el('div', { className: 'tag-subtags', hidden: true }, subtags.map(s =>
    el('div', { className: 'tag-subtag' }, [
      el('span', { text: s.name.replace('[OTB-C] ', '') }),
      el('span', { className: 'muted', text: plural(s.key_count || 0, 'key') })
    ]))) : null;
  const toggle = expandable ? el('button', {
    type: 'button', className: 'icon-btn tag-expand', title: 'Show subtags', 'aria-expanded': 'false',
    onclick: () => {
      panel.hidden = !panel.hidden;
      toggle.setAttribute('aria-expanded', String(!panel.hidden));
      toggle.title = panel.hidden ? 'Show subtags' : 'Hide subtags';
      replaceChildren(toggle, faIcon(panel.hidden ? 'chevron-down' : 'chevron-up'));
    }
  }, faIcon('chevron-down')) : null;
  return el('div', { className: 'tag-row-wrap' }, [
    el('div', { className: 'tag-row' }, [
      el('div', { className: 'tag-row-name' }, [
        el('strong', { text: tag.name }),
        tag.missing ? el('span', { className: 'badge warn', text: 'missing' }) : null,
        toggle
      ]),
      el('div', { className: 'tag-row-meta', text: tagSummary(tag) }),
      el('div', { className: 'tag-row-device' }, deviceChip(tag.device)),
      el('div', { className: 'tag-row-seen', text: relativeTime(tag.last_seen_unix), title: 'Last report: ' + formatTime(tag.last_seen_unix) })
    ]),
    panel
  ]);
}

function renderTagTable(hostId, provider, emptyText) {
  const tags = allTags().filter(t => t.provider === provider).sort(byName);
  replaceChildren(document.getElementById(hostId), tags.length === 0 ? hint(emptyText) : [
    el('div', { className: 'tag-row tag-row-head', 'aria-hidden': 'true' },
      ['Tag', 'Keys', 'Device', 'Last report'].map(h => el('div', { text: h }))),
    ...tags.map(keyTagRow)
  ]);
}

function fileRow(file, feedbackId) {
  const remove = el('button', { type: 'button', className: 'icon-btn danger', title: 'Delete ' + file.filename, 'aria-label': 'Delete ' + file.filename }, faIcon('trash-alt'));
  remove.addEventListener('click', () => deleteKeyfileFlow(file.filename, feedbackId));
  return el('div', { className: 'file-row' }, [
    faIcon('file-alt', 'muted'),
    el('a', {
      className: 'file-name', text: file.filename, title: 'Download',
      href: '/api/keyfiles/' + encodeURIComponent(file.filename) + '/download'
    }),
    el('span', { className: 'muted', text: formatBytes(file.size) + ' · ' + formatTime(file.updated_unix) }),
    remove
  ]);
}

function renderKeysTab() {
  renderTagTable('apple-accessories-list', 'apple', 'No Apple tags yet. Add an accessories.json exported from your tags.');
  renderTagTable('google-targets-list', 'google',
    keyFiles.some(f => f.category === 'secrets')
      ? 'No Google tags found yet. Try Sync with Google.'
      : 'No Google tags yet. Upload the secrets.json of your Google account.');

  const apple = keyFiles.filter(f => f.category === 'accessories');
  const google = keyFiles.filter(f => f.category === 'secrets');
  replaceChildren(document.getElementById('apple-files-list'), apple.length
    ? [el('div', { className: 'source-files-title', text: 'Key files' }), ...apple.map(f => fileRow(f, 'apple-feedback'))] : []);
  replaceChildren(document.getElementById('google-files-list'), google.length
    ? [el('div', { className: 'source-files-title', text: 'Auth file' }), ...google.map(f => fileRow(f, 'google-feedback'))] : []);

  const uploadLabel = document.getElementById('google-upload-label');
  if (uploadLabel) uploadLabel.textContent = google.length ? 'Replace secrets.json' : 'Upload secrets.json';
  const sync = document.getElementById('refresh-google-keys-btn');
  if (sync && !sync.classList.contains('loading')) sync.disabled = google.length === 0;

  const problems = (inventory.errors || []).map(e => e.file + ': ' + e.error).concat(inventory.warnings || []);
  const feedback = document.getElementById('apple-feedback');
  if (feedback && (problems.length || feedback.dataset.problems)) {
    setFeedback(feedback, problems.join(' | '), problems.length > 0);
    feedback.dataset.problems = problems.length ? '1' : '';
  }
}

// "Last fetch 12 min ago · next in 48 min" lines under the provider names.
function scheduleParts(lastStatus, schedule, what) {
  const parts = [];
  if (lastStatus) {
    parts.push(el('span', {
      className: lastStatus.ok ? null : 'text-error', title: formatTime(lastStatus.timestamp_unix),
      text: 'Last ' + what + ' ' + relativeTime(lastStatus.timestamp_unix) + (lastStatus.ok ? '' : ' (failed)')
    }));
  } else {
    parts.push(el('span', { text: 'No ' + what + ' yet' }));
  }
  if (schedule && schedule.interval_min === 0 && what === 'fetch') parts.push(el('span', { text: 'automatic fetch off' }));
  else if (schedule && schedule.next_due_unix) parts.push(el('span', { text: 'next ' + relativeFuture(schedule.next_due_unix), title: formatTime(schedule.next_due_unix) }));
  return parts;
}

function joinParts(parts) {
  const out = [];
  parts.forEach((part, i) => { if (i) out.push(el('span', { className: 'sep', text: '·', 'aria-hidden': 'true' })); out.push(part); });
  return out;
}

function renderProviderSchedules() {
  const data = statusCache || {};
  replaceChildren(document.getElementById('apple-schedule'), joinParts(scheduleParts(data.apple, data.apple_schedule, 'fetch')));
  const keys = data.google_keys;
  const keysSchedule = data.google_keys_schedule || {};
  const google = scheduleParts(data.google, data.google_schedule, 'fetch');
  google.push(el('span', {
    className: keys && !keys.ok ? 'text-error' : null,
    title: keys ? formatTime(keys.timestamp_unix) : null,
    text: keys ? 'synced ' + relativeTime(keys.timestamp_unix) + (keys.ok ? '' : ' (failed)') +
      (keysSchedule.next_due_unix ? ', next ' + relativeFuture(keysSchedule.next_due_unix) : '') : 'never synced'
  }));
  replaceChildren(document.getElementById('google-schedule'), joinParts(google));
}

async function refreshFiles() {
  try {
    keyFiles = (await getJson('/api/keyfiles')).files || [];
  } catch (err) { /* lists stay as they are */ }
  renderKeysTab();
}

async function deleteKeyfileFlow(filename, feedbackId) {
  let preview;
  try {
    preview = await getJson('/api/keyfiles/' + encodeURIComponent(filename) + '/delete-preview');
  } catch (err) {
    setFeedback(feedbackId, err.message, true);
    return;
  }
  const result = await openDialog({
    title: 'Delete ' + filename + '?', body: deletePreviewBody(preview), confirmLabel: 'Delete file', danger: true,
    onConfirm: () => sendJson('DELETE', '/api/keyfiles/' + encodeURIComponent(filename))
  });
  if (!result) return;
  setFeedback(feedbackId, 'Deleted ' + filename + (result.tags_removed ? ' (' + plural(result.tags_removed, 'tag') + ' removed)' : ''));
  await Promise.all([refreshFiles(), refreshInventory()]);
  refreshHistory();
}

function deletePreviewBody(preview) {
  const nodes = [];
  if (preview.devices.length === 0) {
    nodes.push(el('p', { text: 'No tags are removed: every tag in this file is also in another key file.' }));
    return nodes;
  }
  nodes.push(el('p', { text: 'This permanently deletes ' +
    plural(preview.tags_removed, 'tag') + ' and ' + plural(preview.events_removed, 'stored location') + ':' }));
  for (const device of preview.devices) {
    const tagNames = device.tags.map(t => t.name + ' (' + (PROVIDER_LABEL[t.provider] || t.provider) + ')').join(', ');
    let what;
    if (!device.custom) what = 'Tag is removed';
    else if (device.deleted) what = 'Device is deleted (all ' + plural(device.tag_count, 'tag') + ')';
    else what = 'Device keeps ' + plural(device.tag_count - device.tags.length, 'tag') + ' but loses ' + tagNames;
    nodes.push(el('div', { className: 'confirm-device' }, [
      deviceIcon({ auto_created: !device.custom, icon: device.icon, color: device.color }),
      el('div', {}, [
        el('strong', { text: device.name }),
        el('div', { text: what }),
        device.custom && !device.deleted ? null : el('div', { className: 'muted', text: tagNames }),
        el('div', { className: 'muted', text: plural(device.events, 'location') + ' of history lost' })
      ])
    ]));
  }
  return nodes;
}

// Upload buttons + drag and drop onto the provider cards.
function bindUpload({ cardId, buttonId, inputId, path, feedbackId, describe, confirm }) {
  const card = document.getElementById(cardId);
  const button = document.getElementById(buttonId);
  const input = document.getElementById(inputId);
  if (!card || !button || !input) return;

  const upload = withLoading(button, async file => {
    if (confirm && !(await confirm())) return;
    try {
      const result = await postFile(path, file);
      setFeedback(feedbackId, describe(result));
    } catch (err) {
      setFeedback(feedbackId, err.message, true);
    }
    await Promise.all([refreshFiles(), refreshInventory()]);
    refreshJobs();
  });

  // (re-render afterwards: the button label was swapped for a spinner during the upload)
  const run = file => upload(file).then(renderKeysTab);
  button.addEventListener('click', () => input.click());
  input.addEventListener('change', () => {
    const file = input.files && input.files[0];
    input.value = '';
    if (file) run(file);
  });

  let depth = 0;
  const hasFiles = evt => [...(evt.dataTransfer?.types || [])].includes('Files');
  card.addEventListener('dragenter', evt => {
    if (!hasFiles(evt)) return;
    evt.preventDefault();
    depth += 1;
    card.classList.add('dragging');
  });
  card.addEventListener('dragover', evt => { if (hasFiles(evt)) evt.preventDefault(); });
  card.addEventListener('dragleave', () => {
    depth = Math.max(0, depth - 1);
    if (!depth) card.classList.remove('dragging');
  });
  card.addEventListener('drop', evt => {
    if (!hasFiles(evt)) return;
    evt.preventDefault();
    depth = 0;
    card.classList.remove('dragging');
    const file = evt.dataTransfer.files[0];
    if (file) run(file);
  });
}

// ═════════════════════════════ Devices tab ═════════════════════════════

function loadDismissed() {
  return new Set(loadSetting(STORE_DISMISSED, []));
}

// Ungrouped Apple and Google tags with the same name (ignoring case) are probably one tag.
function groupingSuggestions() {
  const dismissed = loadDismissed();
  const groups = new Map();
  for (const tag of allTags()) {
    if (!tag.device.auto_created || tag.missing) continue;
    const key = tag.name.trim().toLowerCase();
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(tag);
  }
  return [...groups.values()]
    .filter(tags => new Set(tags.map(t => t.provider)).size > 1)
    .map(tags => ({ tags, key: tags.map(t => t.id).sort().join('+') }))
    .filter(s => !dismissed.has(s.key));
}

function renderSuggestions() {
  const host = document.getElementById('device-suggestions');
  if (!host) return;
  replaceChildren(host, groupingSuggestions().map(({ tags, key }) => {
    const names = tags.map(t => [PROVIDER_LABEL[t.provider] + ' ', el('em', { text: t.name })]);
    const text = [];
    names.forEach((parts, i) => {
      if (i) text.push(i === names.length - 1 ? ' and ' : ', ');
      text.push(...parts);
    });
    text.push(' look like the same tag.');
    return el('div', { className: 'suggestion', role: 'note' }, [
      faIcon('lightbulb', 'suggestion-icon'),
      el('div', { className: 'suggestion-text' }, text),
      el('button', {
        type: 'button', className: 'primary-btn small-btn',
        onclick: () => openDeviceDialog({ tagIds: tags.map(t => t.id), name: tags[0].name })
      }, 'Group them'),
      el('button', {
        type: 'button', className: 'icon-btn', title: 'Dismiss this suggestion', 'aria-label': 'Dismiss this suggestion',
        onclick: () => {
          const dismissed = loadDismissed();
          dismissed.add(key);
          saveSetting(STORE_DISMISSED, [...dismissed]);
          renderSuggestions();
        }
      }, faIcon('times'))
    ]);
  }));
}

function retentionSelect(device) {
  const max = inventory.max_retention_days;
  const select = el('select', { 'aria-label': 'Keep history of ' + device.name + ' for' });
  select.appendChild(el('option', { value: '', text: max ? 'Default (' + plural(max, 'day') + ')' : 'Default (forever)' }));
  const choices = RETENTION_CHOICES.filter(d => !max || d < max);
  if (device.retention_days && !choices.includes(device.retention_days)) choices.push(device.retention_days);
  choices.sort((a, b) => a - b);
  for (const days of choices) {
    const capped = max && days > max;
    select.appendChild(el('option', { value: String(days), text: plural(days, 'day') + (capped ? ' (capped at ' + max + ')' : '') }));
  }
  select.value = device.retention_days ? String(device.retention_days) : '';
  const saved = el('span', { className: 'saved-note', 'aria-live': 'polite' });
  select.addEventListener('change', async () => {
    select.disabled = true;
    try {
      await sendJson('PUT', '/api/devices/' + encodeURIComponent(device.id), { retention_days: select.value ? parseInt(select.value, 10) : null });
      device.retention_days = select.value ? parseInt(select.value, 10) : null;
      replaceChildren(saved, [faIcon('check'), ' Saved']);
      setTimeout(() => replaceChildren(saved, []), 1800);
    } catch (err) {
      select.value = device.retention_days ? String(device.retention_days) : '';
      setFeedback('devices-feedback', 'Could not save the history setting: ' + err.message, true);
    } finally {
      select.disabled = false;
    }
  });
  return [select, saved];
}

function deviceCard(device) {
  const tags = [...device.tags].sort((a, b) => a.provider.localeCompare(b.provider) || byName(a, b));
  const card = el('article', { className: 'device-card', dataset: { deviceId: device.id } }, [
    el('div', { className: 'device-card-head' }, [
      el('span', { className: 'device-badge', style: 'background:' + device.color }, faIcon(deviceIconName(device))),
      el('div', { className: 'device-card-title' }, [
        el('h3', { text: device.name }),
        el('small', { className: 'muted', text: plural(tags.length, 'tag') + ' · last seen ' + relativeTime(lastSeen(device)) })
      ]),
      menuButton([
        { icon: 'pen', label: 'Edit device', onclick: () => openDeviceDialog({ device }) },
        { icon: 'unlink', label: 'Delete device', danger: true, onclick: () => deleteDevice(device) }
      ], { title: 'Actions for ' + device.name })
    ]),
    el('ul', { className: 'device-card-tags' }, tags.map(tag => el('li', {}, [
      providerIcon(tag.provider),
      el('span', { className: 'grow', text: tag.name }),
      el('span', { className: 'muted', text: tagSummary(tag) }),
      el('span', { className: 'muted seen', text: relativeTime(tag.last_seen_unix), title: 'Last report: ' + formatTime(tag.last_seen_unix) })
    ]))),
    el('label', { className: 'device-card-retention' }, [
      faIcon('history', 'muted'), el('span', { text: 'Keep history' }), ...retentionSelect(device)
    ])
  ]);
  card.style.setProperty('--device', device.color);
  return card;
}

function renderDevicesTab() {
  renderSuggestions();
  const devices = customDevices().sort(byName);
  const ungrouped = allTags().filter(t => t.device.auto_created).sort((a, b) => byName(a, b) || a.provider.localeCompare(b.provider));
  const total = allTags().length;

  replaceChildren(document.getElementById('devices-container'), devices.length
    ? devices.map(deviceCard)
    : el('div', { className: 'empty-state' }, total
      ? [el('strong', { text: 'No devices yet' }),
        el('p', { text: 'Group the Apple and Google halves of a tag into one device, or give a single tag its own name, icon and history setting.' })]
      : [el('strong', { text: 'No tags yet' }),
        el('p', {}, ['Upload your key files in the ', el('a', { href: '#keys', text: 'Keys tab' }), ' first.'])]));

  const title = document.getElementById('ungrouped-title');
  const list = document.getElementById('ungrouped-list');
  if (title) title.hidden = total === 0;
  if (list) list.hidden = total === 0;
  replaceChildren(list, ungrouped.length === 0 ? hint('Every tag is part of a device.') : ungrouped.map(tag =>
    el('div', { className: 'list-row' }, [
      providerIcon(tag.provider),
      el('div', { className: 'list-row-main' }, [
        el('strong', { text: tag.name }),
        el('small', { className: 'muted', text: tagSummary(tag) + ' · last report ' + relativeTime(tag.last_seen_unix) })
      ]),
      el('button', {
        type: 'button', className: 'small-btn', onclick: () => openDeviceDialog({ tagIds: [tag.id], name: tag.name })
      }, [faIcon('plus'), ' Make device'])
    ])));
}

function suggestedColor() {
  const used = new Set(customDevices().map(d => d.color.toLowerCase()));
  return DEVICE_COLORS.find(c => !used.has(c)) || DEVICE_COLORS[0];
}

// Create (no device) or edit a device. tagIds / name pre-fill a new device.
function openDeviceDialog({ device = null, tagIds = [], name = '' } = {}) {
  const checked = new Set(device ? device.tags.map(t => t.id) : tagIds);
  let color = device ? device.color : suggestedColor();
  let icon = device && !device.auto_created ? deviceIconName(device) : DEFAULT_DEVICE_ICON;

  const nameInput = el('input', { type: 'text', required: true, maxlength: '100', autocomplete: 'off', placeholder: 'e.g. Keys, Backpack, Bike', value: device ? device.name : name });

  // Any colour: an eyedropper button over an invisible native picker (looks the same in every browser).
  const custom = el('input', { type: 'color', className: 'custom-color-input', 'aria-label': 'Pick any colour', value: color });
  const customButton = el('label', { className: 'custom-color', title: 'Pick any colour' }, [faIcon('eye-dropper'), custom]);
  const swatches = DEVICE_COLORS.map(c => el('button', {
    type: 'button', className: 'color-swatch', style: 'background:' + c, title: c, 'aria-label': 'Colour ' + c,
    dataset: { color: c }, onclick: () => setColor(c)
  }));
  const preview = el('span', { className: 'device-badge' });
  function setColor(c) {
    color = c;
    let matched = false;
    swatches.forEach(s => {
      const hit = s.dataset.color.toLowerCase() === c.toLowerCase();
      s.classList.toggle('active', hit);
      s.setAttribute('aria-pressed', String(hit));
      matched = matched || hit;
    });
    customButton.classList.toggle('active', !matched);
    customButton.style.background = matched ? '' : c;
    custom.value = c;
    preview.style.background = c;
  }
  custom.addEventListener('input', () => setColor(custom.value));

  const iconButton = name => el('button', {
    type: 'button', className: 'icon-option', title: name.replace(/-/g, ' '), 'aria-label': name.replace(/-/g, ' '),
    dataset: { icon: name }, onclick: () => setIcon(name)
  }, faIcon(name));
  const quick = DEVICE_ICONS.map(iconButton);
  // A picked icon that is not a quick pick shows up in this extra slot, before the "All icons" button.
  const extra = el('button', { type: 'button', className: 'icon-option', hidden: true, onclick: () => setIcon(extra.dataset.icon) });
  const browse = el('button', {
    type: 'button', className: 'icon-option icon-browse', title: 'All icons', 'aria-label': 'Browse all icons', 'aria-expanded': 'false'
  }, faIcon('ellipsis-h'));
  const search = el('input', { type: 'search', placeholder: 'Search about 1,000 icons, e.g. bag, pet, car', 'aria-label': 'Search icons', autocomplete: 'off' });
  const allGrid = el('div', { className: 'icon-picker icon-picker-all' });
  const allPanel = el('div', { className: 'icon-browser', hidden: true }, [search, allGrid]);
  let allButtons = [];

  // The quick picks fill exactly two rows, the last cell being "All icons". A picked icon that is
  // not among the visible quick picks takes the cell before it.
  const picker = el('div', { className: 'icon-picker icon-quick' });
  function layoutQuick() {
    const cols = getComputedStyle(picker).gridTemplateColumns.split(' ').filter(Boolean).length;
    if (!cols || cols < 2) return;
    const slots = cols * 2 - 1;
    const index = DEVICE_ICONS.indexOf(icon);
    const inQuick = index >= 0 && index < slots;
    const count = inQuick ? slots : slots - 1;
    quick.forEach((b, i) => { b.hidden = i >= count; });
    extra.hidden = inQuick;
    if (!inQuick) {
      extra.dataset.icon = icon;
      extra.title = icon.replace(/-/g, ' ');
      extra.setAttribute('aria-label', extra.title);
      replaceChildren(extra, faIcon(icon));
    }
  }

  function setIcon(name) {
    icon = name;
    layoutQuick();
    [...quick, extra, ...allButtons].forEach(b => {
      const on = b.dataset.icon === name;
      b.classList.toggle('active', on);
      b.setAttribute('aria-pressed', String(on));
    });
    replaceChildren(preview, faIcon(name));
  }

  function filterIcons() {
    const words = search.value.toLowerCase().trim().split(/\s+/).filter(Boolean);
    let shown = 0;
    for (const b of allButtons) {
      const hit = words.every(w => b.dataset.search.includes(' ' + w));  // word starts, so "cat" skips "location"
      b.hidden = !hit;
      if (hit) shown += 1;
    }
    allGrid.dataset.empty = shown ? '' : 'No icon matches.';
  }

  browse.addEventListener('click', async () => {
    allPanel.hidden = !allPanel.hidden;
    browse.setAttribute('aria-expanded', String(!allPanel.hidden));
    browse.classList.toggle('open', !allPanel.hidden);
    if (allPanel.hidden) return;
    if (!allButtons.length) {
      replaceChildren(allGrid, hint('Loading icons…'));
      try {
        const index = await loadIconIndex();
        allButtons = index.map(entry => {
          const [name, more] = entry.split('|');
          const b = iconButton(name);
          b.dataset.search = ' ' + name.replace(/-/g, ' ') + ' ' + (more || '');
          return b;
        });
        replaceChildren(allGrid, allButtons);
        setIcon(icon);
      } catch (err) {
        replaceChildren(allGrid, hint(err.message, true));
        return;
      }
    }
    search.focus();
    allButtons.find(b => b.dataset.icon === icon)?.scrollIntoView({ block: 'nearest' });
  });
  search.addEventListener('input', filterIcons);
  search.addEventListener('keydown', evt => { if (evt.key === 'Enter') evt.preventDefault(); });
  replaceChildren(picker, [...quick, extra, browse]);
  setColor(color);
  setIcon(icon);

  // Tags: this device's and ungrouped ones first, then the ones in other devices.
  const rank = tag => (device && tag.device.id === device.id) || checked.has(tag.id) ? 0 : tag.device.auto_created ? 1 : 2;
  const tags = allTags().sort((a, b) => rank(a) - rank(b) || byName(a, b) || a.provider.localeCompare(b.provider));
  const count = el('span', { className: 'muted' });
  const updateCount = () => { count.textContent = plural(checked.size, 'tag') + ' selected'; };
  const tagCheck = tag => {
    const box = el('input', { type: 'checkbox', value: tag.id });
    box.checked = checked.has(tag.id);
    box.addEventListener('change', () => {
      if (box.checked) checked.add(tag.id); else checked.delete(tag.id);
      updateCount();
    });
    const elsewhere = !tag.device.auto_created && !(device && tag.device.id === device.id);
    return el('label', { className: 'tag-check' }, [
      box, providerIcon(tag.provider),
      el('span', { className: 'grow', text: tag.name }),
      elsewhere ? el('small', { className: 'muted', text: 'in ' + tag.device.name }) : null
    ]);
  };
  // Tags already in another device are tucked away (unless pre-selected): moving them is rarely wanted.
  const main = tags.filter(t => rank(t) < 2);
  const others = tags.filter(t => rank(t) === 2);
  const otherList = el('div', { className: 'tag-check-others', hidden: true }, others.map(tagCheck));
  const otherToggle = others.length ? el('button', {
    type: 'button', className: 'tag-check-toggle', 'aria-expanded': 'false',
    onclick: () => {
      otherList.hidden = !otherList.hidden;
      otherToggle.setAttribute('aria-expanded', String(!otherList.hidden));
      replaceChildren(otherToggle, otherToggleLabel());
    }
  }) : null;
  const otherToggleLabel = () => [
    faIcon(otherList.hidden ? 'chevron-right' : 'chevron-down'),
    el('span', { className: 'grow', text: plural(others.length, 'tag') + ' already in other devices' })
  ];
  if (otherToggle) replaceChildren(otherToggle, otherToggleLabel());
  const checklist = el('div', { className: 'tag-checklist' }, [
    ...main.map(tagCheck),
    main.length ? null : el('p', { className: 'hint tag-check-empty', text: 'No ungrouped tags left.' }),
    otherToggle, otherList
  ]);
  updateCount();

  const result = openDialog({
    title: device ? 'Edit ' + device.name : 'New device',
    wide: true,
    confirmLabel: device ? 'Save' : 'Create device',
    body: [
      el('div', { className: 'device-form-top' }, [preview, field('Name', nameInput)]),
      el('div', { className: 'field' }, [el('span', { className: 'field-label', text: 'Colour' }), el('div', { className: 'color-picker' }, [...swatches, customButton])]),
      el('div', { className: 'field' }, [el('span', { className: 'field-label', text: 'Icon' }), picker, allPanel]),
      el('div', { className: 'field' }, [
        el('div', { className: 'field-label split' }, [el('span', { text: 'Tags' }), count]),
        checklist,
        el('small', { text: 'Tags that are in another device move to this one.' + (device ? ' Unticked tags go back to being tracked on their own.' : '') })
      ])
    ],
    onConfirm: async () => {
      const name = nameInput.value.trim();
      if (!name) throw new Error('Give the device a name.');
      if (!checked.size) throw new Error('Tick at least one tag.');
      const fields = { name, color, icon, tag_ids: [...checked] };
      if (device) await sendJson('PUT', '/api/devices/' + encodeURIComponent(device.id), fields);
      else await postJson('/api/devices', fields);
      await refreshInventory();
      refreshHistory();
    }
  });
  // The dialog is in the page now: size the quick picks to its width (and again if it changes).
  layoutQuick();
  const observer = new ResizeObserver(layoutQuick);
  observer.observe(picker);
  return result.finally(() => observer.disconnect());
}

async function deleteDevice(device) {
  const ok = await openDialog({
    title: 'Delete ' + device.name + '?',
    body: [el('p', { text: 'Its ' + plural(device.tags.length, 'tag') + ' stay, with all their location history, and show up on their own again. ' +
      'Only the device name, colour, icon and history setting are removed.' })],
    confirmLabel: 'Delete device',
    danger: true,
    onConfirm: () => sendJson('DELETE', '/api/devices/' + encodeURIComponent(device.id))
  });
  if (!ok) return;
  await refreshInventory();
  refreshHistory();
}


// ═════════════════════════════ Overview ═════════════════════════════
// One device list drives everything: what is ticked is what the map shows and what
// "Refresh" updates. Clicking a device opens focus mode (its trail by provider/tag and
// a table of its reports).

// ── Remembered view settings (per browser) ──
const STORE_HIDDEN_TAGS = 'opentag_hidden_tags';
const STORE_RANGE = 'opentag_range';
const STORE_TRAILS = 'opentag_trails';
const RANGE_PRESETS = [1, 3, 7, 30];
const TABLE_ROW_LIMIT = 2000;

function loadSetting(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch (e) {
    return fallback;
  }
}

function saveSetting(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* storage blocked */ }
}

// Unticked tags are stored (not ticked ones), so new tags show up ticked.
const hiddenTags = new Set(loadSetting(STORE_HIDDEN_TAGS, []));
let range = loadSetting(STORE_RANGE, { preset: 7 });
let showTrails = loadSetting(STORE_TRAILS, false) === true;

let historyEvents = [];
let historyTruncated = false;
let focusedDeviceId = null;
let selectedReportKey = null;
let fitPending = true;
let activeJobs = [];
let alertsCache = [];
let statusCache = null;
const expandedDevices = new Set();
const rowByDevice = new Map();
const pinByDevice = new Map();
const markerByReport = new Map();
let selectionLayer = null;

const PROVIDER_LABEL = { apple: 'Apple', google: 'Google' };
const PROVIDER_HUE = { apple: 145, google: 215 };

function providerIcon(provider, className) {
  return el('i', {
    className: 'fab fa-' + provider + ' provider-icon ' + provider + (className ? ' ' + className : ''),
    title: PROVIDER_LABEL[provider] || provider, 'aria-hidden': 'true'
  });
}

function isTagShown(tagId) {
  return !hiddenTags.has(tagId);
}

function setTagsShown(tagIds, shown) {
  for (const id of tagIds) {
    if (shown) hiddenTags.delete(id); else hiddenTags.add(id);
  }
  saveSetting(STORE_HIDDEN_TAGS, [...hiddenTags]);
  fitPending = true;
  renderOverview();
}

function deviceById(id) {
  return (inventory.devices || []).find(d => d.id === id) || null;
}

function sortedDevices() {
  return [...(inventory.devices || [])].sort((a, b) =>
    (a.auto_created - b.auto_created) || a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
}

function lastSeen(device) {
  return Math.max(0, ...device.tags.map(t => t.last_seen_unix || 0));
}

function reportKey(event) {
  return [event.tag_id, event.timestamp_unix, event.latitude, event.longitude].join('|');
}

function renderOverview() {
  renderDeviceList();
  renderMap();
  renderActionBar();
  if (focusedDeviceId) renderFocusPanel();
}

// ── Device list ──
function renderDeviceList() {
  const host = document.getElementById('device-list');
  if (!host) return;
  rowByDevice.clear();
  const devices = sortedDevices();
  if (devices.length === 0) {
    replaceChildren(host, hint('No devices yet. Upload key files in the Keys tab.'));
    return;
  }
  replaceChildren(host, devices.map(deviceEntry));
  updateDeviceStatus();
}

function deviceEntry(device) {
  const tagIds = device.tags.map(t => t.id);
  const shown = tagIds.filter(isTagShown).length;
  const checkbox = el('input', { type: 'checkbox', title: 'Show on the map and include in Refresh' });
  checkbox.checked = shown > 0 && shown === tagIds.length;
  checkbox.indeterminate = shown > 0 && shown < tagIds.length;
  checkbox.addEventListener('change', () => setTagsShown(tagIds, checkbox.checked));

  const providers = [...new Set(device.tags.map(t => t.provider))].sort();
  const expandable = device.tags.length > 1;
  const expanded = expandable && expandedDevices.has(device.id);
  const expand = expandable ? el('button', {
    type: 'button', className: 'icon-btn device-expand', title: expanded ? 'Hide tags' : 'Show tags',
    onclick: () => {
      if (expandedDevices.has(device.id)) expandedDevices.delete(device.id); else expandedDevices.add(device.id);
      renderDeviceList();
    }
  }, faIcon(expanded ? 'chevron-up' : 'chevron-down')) : el('span', { className: 'device-expand-spacer' });

  const row = el('div', { className: 'device-row', dataset: { deviceId: device.id }, title: 'Show ' + device.name + ' in detail' }, [
    checkbox,
    deviceIcon(device),
    el('span', { className: 'device-row-name', text: device.name }),
    el('span', { className: 'device-row-status' }),
    el('span', { className: 'device-row-providers' }, providers.map(p => providerIcon(p))),
    el('span', { className: 'device-row-seen', text: relativeTime(lastSeen(device)), title: 'Last seen ' + formatTime(lastSeen(device)) }),
    expand,
    faIcon('chevron-right', 'device-row-go')
  ]);
  row.addEventListener('click', evt => {
    if (evt.target.closest('input, button')) return;
    focusDevice(device.id);
  });
  row.addEventListener('mouseenter', () => linkHighlight(device.id, true));
  row.addEventListener('mouseleave', () => linkHighlight(device.id, false));
  rowByDevice.set(device.id, row);

  return el('div', { className: 'device-entry' + (shown === 0 ? ' hidden-on-map' : '') }, [
    row,
    expanded ? el('div', { className: 'device-subs' }, device.tags.map(tagToggle)) : null
  ]);
}

function tagMeta(tag) {
  if (tag.missing) return 'key file missing';
  if (tag.provider === 'apple') return plural(tag.key_count || 0, 'key');
  if (tag.kind === 'compound') return plural((tag.subtags || []).length, 'subtag');
  return 'tracker';
}

function tagToggle(tag) {
  const checkbox = el('input', { type: 'checkbox' });
  checkbox.checked = isTagShown(tag.id);
  checkbox.addEventListener('change', () => setTagsShown([tag.id], checkbox.checked));
  return el('label', { className: 'tag-toggle' }, [
    checkbox,
    providerIcon(tag.provider),
    el('span', { className: 'tag-toggle-name', text: tag.name }),
    el('span', { className: 'tag-toggle-meta', text: tagMeta(tag) + ' · ' + relativeTime(tag.last_seen_unix) })
  ]);
}

function linkHighlight(deviceId, on) {
  const row = rowByDevice.get(deviceId);
  if (row) row.classList.toggle('linked', on);
  const pin = pinByDevice.get(deviceId);
  if (pin) pin.classList.toggle('pulse', on);
}

function jobCoversDevice(job, device) {
  if (job.kind === 'google_sync') return false;
  const ids = job.params && job.params.tag_ids;
  return device.tags.some(t => t.provider === job.provider && (!ids || ids.includes(t.id)));
}

function alertsForDevice(device) {
  const names = device.tags.map(t => t.name);
  return alertsCache.filter(a => a.target && names.some(n => a.target === n || a.target.startsWith(n + ' / ')));
}

// Progress / problems on the rows (updated in place while jobs run).
function updateDeviceStatus() {
  for (const [deviceId, row] of rowByDevice) {
    const device = deviceById(deviceId);
    const host = row.querySelector('.device-row-status');
    if (!device || !host) continue;
    const job = activeJobs.find(j => jobCoversDevice(j, device));
    if (job) {
      const text = job.state === 'queued' ? 'queued' : (job.total > 1 ? job.progress + '/' + job.total : '');
      replaceChildren(host, [el('span', { className: 'spinner small' }), text]);
      host.title = describeJob(job);
      continue;
    }
    const alerts = alertsForDevice(device);
    if (alerts.length) {
      replaceChildren(host, faIcon('exclamation-triangle', 'device-row-warning'));
      host.title = alerts.map(a => a.target + ': ' + a.message).join('\n');
    } else {
      replaceChildren(host, []);
      host.removeAttribute('title');
    }
  }
}

// ── Action bar ──
function refreshableTags() {
  return allTags().filter(t => isTagShown(t.id) && !t.missing);
}

function renderActionBar() {
  const tags = refreshableTags();
  const devices = new Set(tags.map(t => t.device.id));
  const button = document.getElementById('refresh-selected-btn');
  if (button && !button.classList.contains('loading')) {
    replaceChildren(button, [faIcon('sync-alt'), ' Refresh ' + (devices.size === (inventory.devices || []).length && devices.size > 1
      ? 'all ' + devices.size : devices.size) + ' selected']);
    button.disabled = devices.size === 0;
  }
  const hasGoogle = tags.some(t => t.provider === 'google');
  const more = document.getElementById('more-actions');
  if (more) more.hidden = !hasGoogle;
  if (!hasGoogle) closeMenus();
}

async function refreshSelected() {
  const tags = refreshableTags();
  const apple = tags.filter(t => t.provider === 'apple').map(t => t.id);
  const google = tags.filter(t => t.provider === 'google').map(t => t.id);
  const feedback = (msg, isError) => setFeedback('action-feedback', msg, isError);
  if (!apple.length && !google.length) {
    feedback('Tick the devices to refresh first.');
    return;
  }
  feedback('');  // progress is shown on the device rows
  const run = (label, path, ids) => runJob(path, { tag_ids: ids }, () => updateDeviceStatus())
    .catch(err => ({ state: 'failed', error: err.message, label }));
  const results = await Promise.all([
    apple.length ? run('Apple fetch', '/api/apple/fetch', apple) : null,
    google.length ? run('Google fetch', '/api/google/fetch', google) : null
  ].filter(Boolean));
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

// ── Time range ──
function toLocalInput(unix) {
  const d = new Date(unix * 1000);
  const pad = n => String(n).padStart(2, '0');
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + 'T' + pad(d.getHours()) + ':' + pad(d.getMinutes());
}

function fromLocalInput(value) {
  const ms = value ? new Date(value).getTime() : NaN;
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
}

// {from, to}: unix seconds; to = null means "now".
function rangeBounds() {
  const now = Math.floor(Date.now() / 1000);
  if (range.preset === 'custom') {
    const from = fromLocalInput(range.from);
    const to = range.now ? null : fromLocalInput(range.to);
    if (from === null || (!range.now && to === null)) return null;
    return { from, to };
  }
  const days = RANGE_PRESETS.includes(range.preset) ? range.preset : 7;
  return { from: now - days * 86400, to: null };
}

function rangeLabel() {
  const b = rangeBounds();
  const day = unix => toLocalInput(unix).slice(0, 10);
  if (!b) return 'range';
  return day(b.from) + '_' + day(b.to || Math.floor(Date.now() / 1000));
}

function renderRangeControls() {
  document.querySelectorAll('#range-chips [data-range]').forEach(chip => {
    const value = chip.dataset.range === 'custom' ? 'custom' : parseInt(chip.dataset.range, 10);
    chip.classList.toggle('active', value === range.preset);
  });
  const panel = document.getElementById('range-custom');
  if (panel) panel.hidden = range.preset !== 'custom';
  const from = document.getElementById('range-from');
  const to = document.getElementById('range-to');
  const now = document.getElementById('range-now');
  if (range.preset === 'custom' && from && to && now) {
    from.value = range.from || '';
    now.checked = !!range.now;
    to.classList.toggle('is-now', !!range.now);  // still clickable: clicking it releases "Now"
    to.value = range.now ? toLocalInput(Date.now() / 1000) : (range.to || '');
  }
  const trails = document.getElementById('trails-toggle');
  if (trails) {
    trails.checked = showTrails;
    trails.closest('label').hidden = !!focusedDeviceId;  // focus mode always shows the trail
  }
}

function setRange(next) {
  range = next;
  saveSetting(STORE_RANGE, range);
  fitPending = true;
  renderRangeControls();
  refreshHistory();
}

function initRangeControls() {
  document.querySelectorAll('#range-chips [data-range]').forEach(chip => chip.addEventListener('click', () => {
    if (chip.dataset.range !== 'custom') {
      setRange({ preset: parseInt(chip.dataset.range, 10) });
      return;
    }
    if (range.preset === 'custom') return;
    const b = rangeBounds() || { from: Date.now() / 1000 - 7 * 86400 };
    setRange({ preset: 'custom', from: toLocalInput(b.from), to: toLocalInput(Date.now() / 1000), now: true });
  }));
  const from = document.getElementById('range-from');
  const to = document.getElementById('range-to');
  const now = document.getElementById('range-now');
  const apply = () => {
    const next = { preset: 'custom', from: from.value, to: now.checked ? range.to : to.value, now: now.checked };
    if (!now.checked && !next.to) next.to = toLocalInput(Date.now() / 1000);
    setRange(next);
  };
  from?.addEventListener('change', apply);
  to?.addEventListener('change', apply);
  // Picking an end date means it is no longer "now": untick Now as soon as the field is used.
  const releaseNow = () => {
    if (!now || !now.checked) return;
    now.checked = false;
    setRange({ preset: 'custom', from: from.value, to: toLocalInput(Date.now() / 1000), now: false });
  };
  to?.addEventListener('pointerdown', releaseNow);
  to?.addEventListener('focus', releaseNow);
  now?.addEventListener('change', apply);
  document.getElementById('trails-toggle')?.addEventListener('change', evt => {
    showTrails = evt.target.checked;
    saveSetting(STORE_TRAILS, showTrails);
    renderMap();
  });
  renderRangeControls();
}

// ── History + map ──
async function refreshHistory() {
  const bounds = rangeBounds();
  if (!bounds) return;
  if (bounds.to !== null && bounds.to < bounds.from) {
    setMapNote('The end of the range is before its start.');
    return;
  }
  try {
    const query = '/api/history?from=' + bounds.from + (bounds.to !== null ? '&to=' + bounds.to : '');
    const data = await getJson(query);
    historyEvents = data.events || [];
    historyTruncated = !!data.truncated;
  } catch (err) {
    setMapNote('Could not load history: ' + err.message);
    return;
  }
  renderMap();
  if (focusedDeviceId) renderFocusPanel();
}

function setMapNote(text) {
  const note = document.getElementById('map-note');
  if (!note) return;
  note.textContent = text || '';
  note.hidden = !text;
}

function visibleEvents() {
  return historyEvents.filter(e =>
    typeof e.latitude === 'number' && typeof e.longitude === 'number' && isTagShown(e.tag_id) &&
    (focusedDeviceId ? e.device_id === focusedDeviceId : deviceById(e.device_id)));
}

// In focus mode every tag (and every subtag of a Google compound) gets its own shade
// of its provider colour.
function seriesKey(event) {
  return event.tag_id + '|' + (event.part || '');
}

function seriesShades(events) {
  const series = {};
  for (const event of events) {
    const list = series[event.provider] || (series[event.provider] = new Map());
    list.set(seriesKey(event), (event.tag || '') + ' ' + (event.part || ''));
  }
  const shades = {};
  for (const [provider, list] of Object.entries(series)) {
    const keys = [...list.keys()].sort((a, b) => list.get(a).localeCompare(list.get(b), undefined, { numeric: true }));
    keys.forEach((key, i) => {
      const light = keys.length > 1 ? 30 + Math.round(32 * i / (keys.length - 1)) : 42;
      shades[key] = 'hsl(' + (PROVIDER_HUE[provider] || 30) + ', 70%, ' + light + '%)';
    });
  }
  return shades;
}

function reportPopup(event) {
  return el('div', { className: 'report-popup' }, [
    el('strong', { text: event.tag || 'tag' }), ' ', providerIcon(event.provider), el('br'),
    event.part && event.part !== event.tag ? el('div', { text: event.part }) : null,
    el('div', { text: formatTime(event.timestamp_unix) + ' (' + relativeTime(event.timestamp_unix) + ')' }),
    typeof event.accuracy === 'number' ? el('div', { text: '± ' + Math.round(event.accuracy) + ' m' }) : null
  ]);
}

function devicePin(device, latest) {
  const icon = deviceIconName(device);
  const node = el('div', { className: 'device-pin' }, faIcon(icon));
  node.style.setProperty('--pin', device.color);
  const marker = L.marker([latest.latitude, latest.longitude], {
    icon: L.divIcon({ html: node, className: 'device-pin-wrap', iconSize: [30, 30], iconAnchor: [15, 15] }),
    zIndexOffset: 1000, keyboard: false
  });
  marker.bindTooltip(device.name + ' · ' + relativeTime(latest.timestamp_unix), { direction: 'top', offset: [0, -14] });
  marker.on('mouseover', () => linkHighlight(device.id, true));
  marker.on('mouseout', () => linkHighlight(device.id, false));
  marker.on('click', () => { if (focusedDeviceId) selectReport(reportKey(latest), true); else focusDevice(device.id); });
  pinByDevice.set(device.id, node);
  return marker;
}

// A colour token of the current theme (for map layers, which cannot use CSS variables).
function themeColor(name, fallback) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function renderMap() {
  if (!map || !markerLayer || !window.L) return;
  markerLayer.clearLayers();
  polylineLayer.clearLayers();
  pinByDevice.clear();
  markerByReport.clear();

  const byDevice = new Map();
  for (const event of visibleEvents()) {
    if (!byDevice.has(event.device_id)) byDevice.set(event.device_id, []);
    byDevice.get(event.device_id).push(event);
  }
  const allPoints = [];
  for (const [deviceId, events] of byDevice) {
    const device = deviceById(deviceId);
    events.sort((a, b) => (a.timestamp_unix || 0) - (b.timestamp_unix || 0));
    const points = events.map(e => [e.latitude, e.longitude]);
    allPoints.push(...points);
    const latest = events[events.length - 1];
    const focused = deviceId === focusedDeviceId;
    if ((focused || showTrails) && events.length > 1) {
      L.polyline(points, {
        color: focused ? themeColor('--muted', '#3e5560') : device.color, weight: 2, opacity: focused ? 0.75 : 0.45,
        dashArray: focused ? '6 5' : null, interactive: false
      })
        .addTo(polylineLayer);
      const shades = focused ? seriesShades(events) : null;
      events.slice(0, -1).forEach((event, i) => {
        const age = events.length > 2 ? i / (events.length - 2) : 1;  // 0 = oldest
        const color = focused ? (shades[seriesKey(event)] || device.color) : device.color;
        const marker = L.circleMarker([event.latitude, event.longitude], {
          radius: focused ? 6 : 4, color: focused ? '#ffffff' : color, weight: focused ? 1.5 : 1,
          fillColor: color, fillOpacity: focused ? 0.95 : 0.2 + 0.6 * age, opacity: focused ? 0.9 : 0.2 + 0.6 * age
        }).bindPopup(reportPopup(event));
        if (focused) marker.on('click', () => selectReport(reportKey(event), true, false));
        marker.addTo(markerLayer);
        markerByReport.set(reportKey(event), marker);
      });
    }
    const pin = devicePin(device, latest).addTo(markerLayer);
    markerByReport.set(reportKey(latest), pin);
  }
  if (selectedReportKey) drawSelection(selectedReportKey, false);

  if (!allPoints.length) {
    const anyTicked = refreshableTags().length > 0 || allTags().some(t => isTagShown(t.id));
    setMapNote(!(inventory.devices || []).length ? '' : anyTicked ? 'No locations in this time range.' : 'No devices ticked.');
  } else {
    setMapNote(historyTruncated ? 'Showing the newest ' + historyEvents.length + ' locations only; narrow the time range to see all.' : '');
  }
  if (fitPending && allPoints.length) {
    map.fitBounds(allPoints, { padding: [40, 40], maxZoom: 15 });
    fitPending = false;
  }
}

// ── Focus mode ──
function focusDevice(deviceId) {
  if (!deviceById(deviceId)) return;
  if (focusedDeviceId) linkHighlight(focusedDeviceId, false);
  focusedDeviceId = deviceId;
  selectedReportKey = null;
  fitPending = true;
  document.getElementById('device-panel').hidden = true;
  document.getElementById('focus-panel').hidden = false;
  closeMenus();
  renderRangeControls();
  renderFocusBadge();
  renderMap();
  renderFocusPanel();
}

function exitFocus() {
  if (!focusedDeviceId) return;
  focusedDeviceId = null;
  selectedReportKey = null;
  fitPending = true;
  if (selectionLayer) selectionLayer.clearLayers();
  document.getElementById('device-panel').hidden = false;
  document.getElementById('focus-panel').hidden = true;
  renderRangeControls();
  renderFocusBadge();
  renderOverview();
}

function renderFocusBadge() {
  const badge = document.getElementById('focus-badge');
  if (!badge) return;
  const device = focusedDeviceId && deviceById(focusedDeviceId);
  badge.hidden = !device;
  document.querySelector('.map-wrap')?.classList.toggle('focused', !!device);
  if (!device) return;
  replaceChildren(badge, [
    deviceIcon(device), el('span', { text: 'Focused on ' + device.name }),
    el('button', { type: 'button', className: 'icon-btn', title: 'Back to all devices (Esc)', onclick: exitFocus }, faIcon('times'))
  ]);
}

function focusedReports() {
  return visibleEvents().sort((a, b) => (b.timestamp_unix || 0) - (a.timestamp_unix || 0));
}

function renderFocusPanel() {
  const host = document.getElementById('focus-panel');
  const device = deviceById(focusedDeviceId);
  if (!host) return;
  if (!device) { exitFocus(); return; }
  const reports = focusedReports();
  const shades = seriesShades(reports);
  const shown = reports.slice(0, TABLE_ROW_LIMIT);

  const table = el('table', { className: 'report-table' }, [
    el('thead', {}, el('tr', {}, ['Time', '', 'Tag', 'Accuracy', 'Position'].map(h => el('th', { text: h })))),
    el('tbody', {}, shown.map(event => {
      const key = reportKey(event);
      const tr = el('tr', { className: 'report-row' + (key === selectedReportKey ? ' selected' : ''), dataset: { key } }, [
        el('td', { text: formatShort(event.timestamp_unix), title: formatTime(event.timestamp_unix) }),
        el('td', {}, el('span', { className: 'report-swatch', style: 'background:' + (shades[seriesKey(event)] || device.color) }, providerIcon(event.provider))),
        el('td', { text: reportTagName(event) }),
        el('td', { text: typeof event.accuracy === 'number' ? Math.round(event.accuracy) + ' m' : '—' }),
        el('td', { className: 'mono', text: event.latitude.toFixed(5) + ', ' + event.longitude.toFixed(5) })
      ]);
      tr.addEventListener('click', () => selectReport(key, false, true));
      return tr;
    }))
  ]);

  replaceChildren(host, [
    el('div', { className: 'focus-head' }, [
      el('button', { type: 'button', className: 'back-btn', title: 'Back to all devices (Esc)', onclick: exitFocus }, [faIcon('arrow-left'), ' Back']),
      deviceIcon(device),
      el('h2', { text: device.name })
    ]),
    el('div', { className: 'focus-tags' }, device.tags.map(tagToggle)),
    el('div', { className: 'report-tools' }, [
      el('span', { className: 'report-count', text: plural(reports.length, 'report') + (reports.length > shown.length ? ' (table shows the newest ' + shown.length + ')' : '') }),
      el('button', { type: 'button', className: 'small-btn', title: 'Copy as tab-separated text (pastes into a spreadsheet)', onclick: evt => copyReports(evt.currentTarget) }, [faIcon('copy'), ' Copy']),
      el('button', { type: 'button', className: 'small-btn', title: 'Download as CSV', onclick: downloadReports }, [faIcon('download'), ' CSV'])
    ]),
    reports.length ? el('div', { className: 'report-table-wrap' }, table) : hint('No reports for the ticked tags in this time range.')
  ]);
  if (selectedReportKey) showReportDetails(selectedReportKey, false);
}

function reportTagName(event) {
  return event.part && event.part !== event.tag ? event.part.replace('[OTB-C] ', '') : (event.tag || '');
}

function formatShort(unix) {
  const d = new Date(unix * 1000);
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function drawSelection(key, pan) {
  if (!selectionLayer) return;
  selectionLayer.clearLayers();
  const event = historyEvents.find(e => reportKey(e) === key);
  if (!event) return;
  L.circleMarker([event.latitude, event.longitude], {
    radius: 14, color: '#ff961f', weight: 3, fill: false, interactive: false, className: 'selection-ring'
  }).addTo(selectionLayer);
  if (pan) map.panTo([event.latitude, event.longitude]);
}

// fromMap: the click came from the map (scroll the table); fromTable: pan the map.
function selectReport(key, fromMap, fromTable) {
  selectedReportKey = key;
  drawSelection(key, !!fromTable);
  const marker = markerByReport.get(key);
  if (marker && fromTable && marker.getPopup()) marker.openPopup();
  showReportDetails(key, !!fromMap);
}

function showReportDetails(key, scroll) {
  const host = document.getElementById('focus-panel');
  if (!host) return;
  host.querySelectorAll('.report-details').forEach(node => node.remove());
  host.querySelectorAll('.report-row.selected').forEach(node => node.classList.remove('selected'));
  const row = [...host.querySelectorAll('.report-row')].find(r => r.dataset.key === key);
  const event = historyEvents.find(e => reportKey(e) === key);
  if (!row || !event) return;
  row.classList.add('selected');
  const coords = event.latitude.toFixed(6) + ', ' + event.longitude.toFixed(6);
  const lat = encodeURIComponent(event.latitude), lon = encodeURIComponent(event.longitude);
  const detail = el('tr', { className: 'report-details' }, el('td', { colspan: '5' }, [
    el('div', {}, [el('strong', { text: 'Time ' }), formatTime(event.timestamp_unix) + ' (unix ' + event.timestamp_unix + ')']),
    el('div', {}, [el('strong', { text: 'Tag ' }), (event.tag || '') + (event.part && event.part !== event.tag ? ' / ' + reportTagName(event) : '') +
      ' (' + (PROVIDER_LABEL[event.provider] || event.provider) + ')']),
    el('div', {}, [
      el('strong', { text: 'Position ' }), el('span', { className: 'mono', text: coords }), ' ',
      el('button', { type: 'button', className: 'small-btn', onclick: evt => copyText(coords, evt.currentTarget) }, [faIcon('copy'), ' Copy'])
    ]),
    typeof event.accuracy === 'number' ? el('div', {}, [el('strong', { text: 'Accuracy ' }), '± ' + Math.round(event.accuracy) + ' m']) : null,
    el('div', { className: 'report-links' }, [
      el('a', { href: 'https://www.openstreetmap.org/?mlat=' + lat + '&mlon=' + lon + '#map=17/' + lat + '/' + lon, target: '_blank', rel: 'noopener noreferrer' },
        [faIcon('external-link-alt'), ' OpenStreetMap']),
      el('a', { href: 'https://www.google.com/maps/search/?api=1&query=' + lat + ',' + lon, target: '_blank', rel: 'noopener noreferrer' },
        [faIcon('external-link-alt'), ' Google Maps'])
    ])
  ]));
  row.after(detail);
  if (scroll) row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

// ── Copy / CSV ──
const REPORT_COLUMNS = ['time', 'unix_time', 'provider', 'tag', 'subtag', 'latitude', 'longitude', 'accuracy_m'];

function reportRecords() {
  return focusedReports().map(e => [
    new Date(e.timestamp_unix * 1000).toISOString(), e.timestamp_unix, e.provider, e.tag || '',
    e.part && e.part !== e.tag ? reportTagName(e) : '', e.latitude, e.longitude,
    typeof e.accuracy === 'number' ? Math.round(e.accuracy) : ''
  ]);
}

async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (err) {
    const area = el('textarea', { style: 'position:fixed;opacity:0' });
    area.value = text;
    document.body.appendChild(area);
    area.select();
    try { document.execCommand('copy'); } finally { area.remove(); }
  }
  if (button) {
    const original = [...button.childNodes];
    replaceChildren(button, 'Copied');
    setTimeout(() => replaceChildren(button, original), 1200);
  }
}

function copyReports(button) {
  const lines = [REPORT_COLUMNS].concat(reportRecords()).map(r => r.map(v => String(v).replace(/[\t\n]/g, ' ')).join('\t'));
  copyText(lines.join('\n'), button);
}

function downloadReports() {
  const quote = v => /[",\n]/.test(String(v)) ? '"' + String(v).replace(/"/g, '""') + '"' : String(v);
  const csv = [REPORT_COLUMNS].concat(reportRecords()).map(r => r.map(quote).join(',')).join('\r\n') + '\r\n';
  const device = deviceById(focusedDeviceId);
  const slug = (device ? device.name : 'device').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'device';
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  const link = el('a', { href: url, download: slug + '_' + rangeLabel() + '.csv' });
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ── Health strip: status dots, activity, alerts ──
const HEALTH_ITEMS = [
  { key: 'apple', label: 'Apple', title: 'Apple fetch', job: j => j.kind === 'apple_fetch' },
  { key: 'gaps', label: 'Gaps', title: 'Apple gap recovery', job: () => false },
  { key: 'google', label: 'Google', title: 'Google fetch', job: j => j.kind === 'google_fetch' },
  { key: 'keys', label: 'Keys', title: 'Google key announcement', job: j => j.kind === 'google_sync' }
];

const STATUS_KIND = {
  fetch_location: 'Manual fetch', auto_fetch_location: 'Automatic fetch',
  sync_keys: 'Manual sync', auto_sync_keys: 'Automatic sync'
};

// {dot, summary (short), detail (may wrap), when, next}; times are unix seconds.
function gapRecoveryInfo(gap, last) {
  if (!gap || !gap.enabled) return { dot: 'none', summary: 'Off', detail: 'Turn on with apple_gap_recovery in config.toml.' };
  let info;
  if (gap.running && gap.total > 0) {
    const left = Math.max(0, gap.total - gap.done);
    const minutes = Math.ceil(left * (gap.request_interval_seconds || 60) / 60);
    info = {
      dot: 'busy', summary: gap.done + ' of ' + plural(gap.total, 'key') + ' checked',
      detail: 'Checking for missed reports; about ' +
        (minutes < 90 ? minutes + ' min' : Math.round(minutes / 60) + ' h') + ' left.'
    };
  } else if (gap.next_pass_unix) {
    info = { dot: 'ok', summary: 'Pass complete (' + plural(gap.total, 'key') + ')', next: gap.next_pass_unix };
  } else {
    info = { dot: 'none', summary: 'Waiting', detail: 'Starts when no other Apple requests are queued.' };
  }
  if (last) info.when = last.timestamp_unix;
  if (last && last.ok === false) Object.assign(info, { dot: 'error', detail: 'Last request failed: ' + last.error });
  return info;
}

function statusSummary(key, status) {
  const d = status.details || {};
  const parts = [STATUS_KIND[status.kind] || (status.kind || 'OK').replace(/_/g, ' ')];
  if (key === 'apple' && d.counts) parts.push(plural(d.counts.decoded || 0, 'report'));
  if (key === 'google' && typeof d.stored === 'number') parts.push(plural(d.stored, 'new location'));
  if (key === 'keys' && typeof d.devices_uploaded === 'number') parts.push(plural(d.devices_uploaded, 'tracker') + ' announced');
  return parts.join(' · ');
}

function healthInfo(item) {
  const data = statusCache || {};
  if (item.key === 'gaps') return gapRecoveryInfo(data.gap_recovery, data.apple_singleton);
  const status = data[{ apple: 'apple', google: 'google', keys: 'google_keys' }[item.key]];
  let info;
  if (!status) info = { dot: 'none', summary: 'No data yet' };
  else if (status.ok) info = { dot: 'ok', summary: statusSummary(item.key, status), when: status.timestamp_unix };
  else info = { dot: 'error', summary: 'Failed', detail: status.error || 'Unknown error', when: status.timestamp_unix };
  if (item.key === 'keys' && data.google_keys_schedule && data.google_keys_schedule.next_due_unix) {
    info.next = data.google_keys_schedule.next_due_unix;
  }
  const running = activeJobs.find(item.job);
  if (running) {
    info.dot = 'busy';
    info.summary = running.state === 'queued' ? 'Queued' : 'Running' + (running.total > 1 ? ' (' + running.progress + '/' + running.total + ')' : '');
    info.detail = running.state === 'queued' ? null : running.stage;
  }
  return info;
}

function healthRow(item, info) {
  const times = [];
  if (info.when) times.push(el('span', { text: relativeTime(info.when), title: formatTime(info.when) }));
  if (info.next) times.push(el('span', { text: 'next ' + relativeFuture(info.next), title: formatTime(info.next) }));
  return el('div', { className: 'health-row' }, [
    el('div', { className: 'health-row-head' }, [
      el('span', { className: 'status-dot ' + info.dot }),
      el('span', { className: 'health-row-title', text: item.title }),
      el('span', { className: 'health-row-times' }, times)
    ]),
    el('div', { className: 'health-row-summary', text: info.summary }),
    info.detail ? el('div', { className: 'health-row-detail', text: info.detail }) : null
  ]);
}

function renderHealth() {
  const strip = document.getElementById('health-items');
  if (strip) {
    replaceChildren(strip, HEALTH_ITEMS.map(item => {
      const info = healthInfo(item);
      return el('button', {
        type: 'button', className: 'health-item', title: item.title + ': ' + info.summary,
        onclick: () => togglePanel('health-panel')
      }, [el('span', { className: 'status-dot ' + info.dot }), item.label]);
    }));
  }
  const panel = document.getElementById('health-panel');
  if (panel) {
    replaceChildren(panel, [
      el('div', { className: 'panel-head' }, [
        el('h2', { text: 'Status' }),
        el('button', { type: 'button', className: 'icon-btn', title: 'Close', onclick: () => togglePanel(null) }, faIcon('times'))
      ]),
      ...HEALTH_ITEMS.map(item => healthRow(item, healthInfo(item))),
      el('h3', { text: 'Activity' }),
      activeJobs.length ? el('div', { className: 'jobs-container' }, activeJobs.map(jobRow)) : hint('Nothing running right now.')
    ]);
  }
  const bell = document.getElementById('alerts-btn');
  if (bell) {
    const count = alertsCache.length;
    replaceChildren(bell, [faIcon('bell'), count ? el('span', { className: 'bell-count', text: String(count) }) : null]);
    bell.classList.toggle('has-alerts', count > 0);
    bell.title = count ? plural(count, 'alert') : 'No alerts';
  }
}

function togglePanel(id) {
  for (const other of ['health-panel', 'alerts-panel']) {
    const node = document.getElementById(other);
    if (node) node.hidden = other === id ? !node.hidden : true;
  }
}

async function refreshStatus() {
  try {
    statusCache = await getJson('/api/status');
  } catch (err) {
    statusCache = null;
  }
  renderHealth();
  renderProviderSchedules();
}

function alertTarget(alert) {
  if (!alert.target || alert.target === 'apple_locations' || alert.target === 'haystack_report') return '';
  return alert.target;
}

async function refreshAlerts() {
  try {
    alertsCache = (await getJson('/api/alerts')).alerts || [];
  } catch (err) {
    return;
  }
  renderHealth();
  updateDeviceStatus();
  const list = document.getElementById('alerts-list');
  if (!list) return;
  replaceChildren(list, alertsCache.length === 0 ? hint('No alerts.') : alertsCache.map(alert => {
    const dismiss = el('button', { type: 'button', className: 'icon-btn', title: 'Dismiss' }, faIcon('times'));
    dismiss.addEventListener('click', async () => {
      try { await postJson('/api/alerts/' + alert.id + '/dismiss'); } finally { refreshAlerts(); }
    });
    const target = alertTarget(alert);
    return el('div', { className: 'alert-item' }, [
      PROVIDER_LABEL[alert.provider] ? providerIcon(alert.provider) : faIcon('exclamation-triangle', 'provider-icon'),
      el('div', { className: 'alert-body' }, [
        el('div', { className: 'alert-title' }, [
          el('span', { text: (alert.type || 'alert').replace(/_/g, ' ') }),
          target ? el('span', { className: 'alert-where', text: ' · ' + target }) : null,
          alert.count > 1 ? el('span', { className: 'alert-count', text: '×' + alert.count }) : null
        ]),
        el('div', { className: 'alert-message', text: alert.message }),
        el('small', { text: (alert.count > 1 ? 'first ' + formatTime(alert.first_unix) + ' · last ' : '') + formatTime(alert.last_unix) })
      ]),
      dismiss
    ]);
  }));
  const clear = document.getElementById('clear-alerts-btn');
  if (clear) clear.hidden = alertsCache.length === 0;
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
  activeJobs = list.filter(j => j.active);
  updateDeviceStatus();
  renderHealth();
  // A job finished since the last look (e.g. an automatic one): show its results.
  const finished = [...knownActiveJobs].filter(id => !activeJobs.some(j => j.id === id));
  knownActiveJobs.clear();
  activeJobs.forEach(j => knownActiveJobs.add(j.id));
  if (finished.length) refreshAfterJobs();
  jobsTimer = setTimeout(refreshJobs, activeJobs.length ? 2000 : 20000);
}

function refreshAfterJobs() {
  return Promise.all([refreshStatus(), refreshAlerts(), refreshInventory()]).then(refreshHistory);
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

function initOverview() {
  document.addEventListener('themechange', () => renderMap());
  initRangeControls();
  bindClick('refresh-selected-btn', refreshSelected);
  document.getElementById('select-all-btn')?.addEventListener('click', () => setTagsShown(allTags().map(t => t.id), true));
  document.getElementById('select-none-btn')?.addEventListener('click', () => setTagsShown(allTags().map(t => t.id), false));
  document.getElementById('more-actions-btn')?.addEventListener('click', evt => {
    evt.stopPropagation();
    const menu = document.getElementById('more-actions-menu');
    if (menu) menu.hidden = !menu.hidden;
  });
  document.getElementById('menu-sync-google')?.addEventListener('click', () => {
    closeMenus();
    syncWithGoogle('action-feedback');
  });
  document.getElementById('alerts-btn')?.addEventListener('click', () => togglePanel('alerts-panel'));
  document.getElementById('close-alerts-btn')?.addEventListener('click', () => togglePanel(null));
  bindClick('clear-alerts-btn', async () => {
    try { await postJson('/api/alerts/dismiss-all', {}); } finally { await refreshAlerts(); }
  });
  document.addEventListener('keydown', evt => {
    if (evt.key !== 'Escape' || !focusedDeviceId) return;
    if (document.querySelector('.modal-overlay')) return;
    exitFocus();
  });
  // Relative times ("12 min ago") and "now"-anchored ranges move on.
  // (Skipped in focus mode so an open popup / selected report is not redrawn under you.)
  setInterval(() => {
    renderProviderSchedules();
    if (focusedDeviceId) return;
    renderDeviceList();
    if (range.preset !== 'custom' || range.now) refreshHistory();
  }, 60000);
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

// ── Mobile: the sidebar is a bottom sheet over the map (tap or drag the handle) ──
function initSheet() {
  const sheet = document.getElementById('overview-sheet');
  const handle = document.getElementById('sheet-handle');
  if (!sheet || !handle) return;
  const setExpanded = open => {
    sheet.classList.toggle('expanded', open);
    sheet.style.removeProperty('--sheet-drag');
    handle.setAttribute('aria-expanded', String(open));
  };
  let drag = null;
  let suppressClick = false;
  handle.addEventListener('pointerdown', evt => {
    drag = { y: evt.clientY, h: sheet.getBoundingClientRect().height, moved: false };
    handle.setPointerCapture(evt.pointerId);
  });
  handle.addEventListener('pointermove', evt => {
    if (!drag) return;
    const dy = drag.y - evt.clientY;
    if (!drag.moved && Math.abs(dy) < 6) return;
    drag.moved = true;
    sheet.classList.add('dragging');
    const max = sheet.parentElement.getBoundingClientRect().height - 12;
    sheet.style.setProperty('--sheet-drag', Math.max(96, Math.min(max, drag.h + dy)) + 'px');
  });
  const end = evt => {
    if (!drag) return;
    const { moved, y } = drag;
    drag = null;
    sheet.classList.remove('dragging');
    if (!moved) return;
    suppressClick = true;
    const dy = y - evt.clientY;
    const open = sheet.classList.contains('expanded');
    setExpanded(Math.abs(dy) < 40 ? open : dy > 0);
  };
  handle.addEventListener('pointerup', end);
  handle.addEventListener('pointercancel', end);
  handle.addEventListener('click', () => {
    if (suppressClick) { suppressClick = false; return; }
    setExpanded(!sheet.classList.contains('expanded'));
  });
}

// ── Desktop: drag the handle between map and side panel (remembered per browser) ──
const STORE_SIDEBAR_WIDTH = 'opentag_sidebar_width';
const SIDEBAR_MIN = 300;
const MAP_MIN = 320;

function initSplitHandle() {
  const grid = document.querySelector('.overview-grid');
  const handle = document.getElementById('split-handle');
  if (!grid || !handle) return;
  let width = loadSetting(STORE_SIDEBAR_WIDTH, null);
  let frame = null;

  const defaultWidth = () => parseInt(getComputedStyle(grid).getPropertyValue('--sidebar-default'), 10) || 400;
  const clamp = w => {
    const max = Math.max(SIDEBAR_MIN, grid.getBoundingClientRect().width - MAP_MIN);
    return Math.round(Math.min(max, Math.max(SIDEBAR_MIN, w)));
  };
  const apply = () => {
    if (typeof width === 'number') grid.style.setProperty('--sidebar-w', clamp(width) + 'px');
    else grid.style.setProperty('--sidebar-w', defaultWidth() + 'px');
    handle.setAttribute('aria-valuenow', String(parseInt(grid.style.getPropertyValue('--sidebar-w'), 10)));
    if (map && !frame) frame = requestAnimationFrame(() => { frame = null; map.invalidateSize(); });
  };
  const save = () => saveSetting(STORE_SIDEBAR_WIDTH, typeof width === 'number' ? clamp(width) : null);

  let dragging = false;
  handle.addEventListener('pointerdown', evt => {
    if (evt.button !== 0) return;
    dragging = true;
    handle.setPointerCapture(evt.pointerId);
    document.body.classList.add('resizing');
    evt.preventDefault();
  });
  handle.addEventListener('pointermove', evt => {
    if (!dragging) return;
    width = clamp(grid.getBoundingClientRect().right - evt.clientX - 6);
    apply();
  });
  const stop = () => {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove('resizing');
    save();
  };
  handle.addEventListener('pointerup', stop);
  handle.addEventListener('pointercancel', stop);
  handle.addEventListener('dblclick', () => { width = null; apply(); save(); });
  handle.addEventListener('keydown', evt => {
    const step = evt.shiftKey ? 80 : 20;
    const current = parseInt(grid.style.getPropertyValue('--sidebar-w'), 10) || defaultWidth();
    if (evt.key === 'ArrowLeft') width = clamp(current + step);
    else if (evt.key === 'ArrowRight') width = clamp(current - step);
    else if (evt.key === 'Home' || evt.key === 'Enter') width = null;
    else return;
    evt.preventDefault();
    apply();
    save();
  });
  handle.setAttribute('aria-valuemin', String(SIDEBAR_MIN));
  window.addEventListener('resize', apply);
  apply();
}

// ── Tabs: the URL hash (#keys, #devices) picks one, so links from other pages land on it ──
const TABS = ['overview', 'keys', 'devices'];

function showTab(name) {
  if (!TABS.includes(name)) name = 'overview';
  document.querySelectorAll('.tab-btn[data-tab]').forEach(btn => {
    const on = btn.dataset.tab === name;
    btn.classList.toggle('active', on);
    if (on) btn.setAttribute('aria-current', 'page'); else btn.removeAttribute('aria-current');
  });
  document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + name));
  if (name === 'overview' && map) setTimeout(() => map.invalidateSize(), 50);
}

function initDashboard() {
  window.addEventListener('hashchange', () => showTab(location.hash.slice(1)));
  showTab(location.hash.slice(1));

  bindClick('refresh-google-keys-btn', () => syncWithGoogle('google-feedback'));
  bindUpload({
    cardId: 'apple-card', buttonId: 'apple-upload-btn', inputId: 'accessories-file',
    path: '/api/upload/accessories', feedbackId: 'apple-feedback',
    describe: r => 'Added ' + plural(r.saved.items, 'accessory', 'accessories') + ' (' + plural(r.tags_added, 'new tag') + ')' +
      (r.job ? '. Fetching their locations now; progress shows on the Overview.' : '') +
      ((r.warnings || []).length ? ' | ' + r.warnings.join(' | ') : '')
  });
  bindUpload({
    cardId: 'google-card', buttonId: 'google-upload-btn', inputId: 'secrets-file',
    path: '/api/upload/secrets', feedbackId: 'google-feedback',
    describe: r => (r.saved.replaced ? 'Replaced' : 'Uploaded') + ' secrets.json (' + plural(r.tags_added, 'tag') + ' added, ' +
      plural(r.tags_removed, 'tag') + ' removed)',
    confirm: () => !keyFiles.some(f => f.category === 'secrets') || confirmDialog({
      title: 'Replace secrets.json?',
      body: [el('p', { text: 'Google tags that are not in the new file are removed, together with their location history. Tags that are still in it keep theirs.' })],
      confirmLabel: 'Replace'
    })
  });
  document.getElementById('new-device-btn')?.addEventListener('click', () => openDeviceDialog());
  document.querySelectorAll('.info-btn[data-info]').forEach(button => button.addEventListener('click', () => {
    const box = document.getElementById(button.dataset.info);
    if (!box) return;
    box.hidden = !box.hidden;
    button.setAttribute('aria-expanded', String(!box.hidden));
  }));

  initMap();
  setTimeout(() => { if (map) map.invalidateSize(); }, 100);
  initOverview();
  initSheet();
  initSplitHandle();

  refreshFiles();
  refreshInventory().then(refreshHistory);
  refreshStatus();
  refreshAlerts();
  refreshJobs();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initDashboard);
} else {
  initDashboard();
}
