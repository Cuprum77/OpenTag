// ── Theme / Dark Mode ──
function initDarkMode() {
  const toggle = document.getElementById('theme-toggle');
  const loginToggle = document.getElementById('login-theme-toggle');
  const isDark = localStorage.getItem('opentag_dark') === 'true';

  if (isDark) {
    document.body.classList.add('dark');
  }

  function updateIcon(dark) {
    const btn = toggle || loginToggle;
    if (btn) btn.textContent = dark ? '☀️' : '🌙';
  }
  updateIcon(isDark);

  function toggleTheme() {
    const nowDark = !document.body.classList.contains('dark');
    document.body.classList.toggle('dark', nowDark);
    localStorage.setItem('opentag_dark', nowDark);
    updateIcon(nowDark);
  }

  if (toggle) toggle.addEventListener('click', toggleTheme);
  if (loginToggle) loginToggle.addEventListener('click', toggleTheme);
}

// Initialize dark mode immediately (before DOMContentLoaded so it applies before paint)
initDarkMode();

// ── Tab switching ──
let map = null;
let markerLayer = null;
let polylineLayer = null;
let lastGoogleTargets = [];
let lastGoogleCompounds = [];

// ── Debug toggle (persisted in localStorage) ──
const debugToggle = document.getElementById('debug-mode-toggle');
const debugPanels = document.querySelectorAll('.debug-panel');
let debugMode = localStorage.getItem('opentag_debug') === 'true';
if (debugToggle) debugToggle.checked = debugMode;

function toggleDebug(show) {
  debugMode = show;
  localStorage.setItem('opentag_debug', show);
  debugPanels.forEach(p => p.style.display = show ? 'block' : 'none');
}
toggleDebug(debugMode);
if (debugToggle) debugToggle.addEventListener('change', () => toggleDebug(debugToggle.checked));

function initMap() {
  const mapHost = document.getElementById('map');
  if (!mapHost) return;
  if (!window.L) {
    mapHost.innerHTML = '<p class="hint" style="padding:12px">Map unavailable. Leaflet failed to load.</p>';
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

function showJson(id, payload) {
  const host = document.getElementById(id);
  if (host) host.textContent = JSON.stringify(payload, null, 2);
}

function setAppleFeedback(message, isError = false) {
  const el = document.getElementById('apple-feedback');
  if (!el) return;
  el.textContent = message || '';
  el.classList.remove('feedback-success', 'feedback-error');
  el.classList.add(isError ? 'feedback-error' : 'feedback-success');
}

function setGoogleFeedback(message, isError = false) {
  const el = document.getElementById('google-feedback');
  if (!el) return;
  el.textContent = message || '';
  el.classList.remove('feedback-success', 'feedback-error');
  el.classList.add(isError ? 'feedback-error' : 'feedback-success');
}

function setOverviewGoogleFeedback(message, isError = false) {
  const el = document.getElementById('google-targets-feedback');
  if (!el) return;
  el.textContent = message || '';
  el.classList.remove('feedback-success', 'feedback-error');
  el.classList.add(isError ? 'feedback-error' : 'feedback-success');
}

// ── Loading throbber ──
function withLoading(button, asyncFn) {
  return async () => {
    if (button.classList.contains('loading')) return;
    button.classList.add('loading');
    button.disabled = true;
    const originalText = button.textContent;
    button.innerHTML = '<span class="spinner"></span>' + originalText;
    try {
      await asyncFn();
    } finally {
      button.classList.remove('loading');
      button.disabled = false;
      button.textContent = originalText;
    }
  };
}

// ── Apple accessories list ──
function renderAppleAccessoriesList(accessories) {
  const host = document.getElementById('apple-accessories-list');
  if (!host) return;
  host.innerHTML = '';

  const rows = Array.isArray(accessories) ? accessories : [];
  if (rows.length === 0) {
    host.innerHTML = '<p class="hint">No accessories uploaded yet.</p>';
    return;
  }

  for (const acc of rows) {
    const row = document.createElement('div');
    row.className = 'key-row apple';
    const header = document.createElement('div');
    header.className = 'key-row-header';
    const info = document.createElement('div');
    info.className = 'key-row-info';
    const name = acc.name || ('Tracker #' + (acc.id || ''));
    const count = typeof acc.key_count === 'number' ? acc.key_count : 0;
    const nameEl = document.createElement('span');
    nameEl.className = 'key-name';
    nameEl.textContent = name;
    const metaEl = document.createElement('span');
    metaEl.className = 'key-meta';
    metaEl.textContent = count + ' key' + (count !== 1 ? 's' : '');
    info.appendChild(nameEl);
    info.appendChild(metaEl);
    header.appendChild(info);
    row.appendChild(header);
    host.appendChild(row);
  }
}

// ── Google targets list ──
function renderGoogleTargetsList(targets, compounds) {
  const host = document.getElementById('google-targets-list');
  if (!host) return;
  host.innerHTML = '';

  const targetRows = Array.isArray(targets) ? targets : [];
  const compoundRows = Array.isArray(compounds) ? compounds : [];

  if (targetRows.length === 0 && compoundRows.length === 0) {
    host.innerHTML = '<p class="hint">No Google targets found. Upload secrets.json and refresh keys.</p>';
    return;
  }

  if (compoundRows.length > 0) {
    for (const compound of compoundRows) {
      const row = document.createElement('div');
      row.className = 'key-row google';
      const label = compound.base_name || compound.compound_id;
      const subtags = compound.subtags || [];
      const subtagCount = subtags.length;
      const keyCount = typeof compound.requested_key_count === 'number' ? compound.requested_key_count : 0;

      const header = document.createElement('div');
      header.className = 'key-row-header';

      const info = document.createElement('div');
      info.className = 'key-row-info';
      const nameEl = document.createElement('span');
      nameEl.className = 'key-name';
      nameEl.textContent = label;
      const metaEl = document.createElement('span');
      metaEl.className = 'key-meta';
      metaEl.textContent = 'compound | ' + keyCount + ' keys | ' + subtagCount + ' subtag' + (subtagCount !== 1 ? 's' : '');
      info.appendChild(nameEl);
      info.appendChild(metaEl);
      header.appendChild(info);

      if (subtagCount > 0) {
        const expandBtn = document.createElement('button');
        expandBtn.type = 'button';
        expandBtn.className = 'key-row-expand-btn';
        expandBtn.textContent = '▼';
        const details = document.createElement('div');
        details.className = 'key-row-details';
        for (const subtag of subtags) {
          const subtagEl = document.createElement('div');
          subtagEl.className = 'key-subtag';
          const nameSpan = document.createElement('span');
          nameSpan.className = 'key-subtag-name';
          nameSpan.textContent = subtag.name || 'unnamed';
          const countSpan = document.createElement('span');
          countSpan.className = 'key-subtag-count';
          countSpan.textContent = (subtag.key_count || 0) + ' keys';
          subtagEl.appendChild(nameSpan);
          subtagEl.appendChild(countSpan);
          details.appendChild(subtagEl);
        }
        expandBtn.addEventListener('click', () => {
          const isOpen = details.classList.toggle('open');
          expandBtn.textContent = isOpen ? '▲' : '▼';
        });
        header.appendChild(expandBtn);
        row.appendChild(header);
        row.appendChild(details);
      } else {
        row.appendChild(header);
      }
      host.appendChild(row);
    }
  }

  if (targetRows.length > 0) {
    for (const target of targetRows) {
      const row = document.createElement('div');
      row.className = 'key-row google';
      const header = document.createElement('div');
      header.className = 'key-row-header';
      const info = document.createElement('div');
      info.className = 'key-row-info';
      const label = deriveGoogleTargetLabel(target);
      const value = deriveGoogleTargetValue(target);
      const nameEl = document.createElement('span');
      nameEl.className = 'key-name';
      nameEl.textContent = label;
      const metaEl = document.createElement('span');
      metaEl.className = 'key-meta';
      metaEl.textContent = value !== label ? value : 'target';
      info.appendChild(nameEl);
      info.appendChild(metaEl);
      header.appendChild(info);
      row.appendChild(header);
      host.appendChild(row);
    }
  }
}

// ── File management (per section) ──
function renderAppleFiles(files) {
  const host = document.getElementById('apple-files-list');
  if (!host) return;
  host.innerHTML = '';

  const rows = (Array.isArray(files) ? files : []).filter(f => f && f.category === 'accessories');
  if (rows.length === 0) return;

  for (const file of rows) {
    const row = document.createElement('div');
    row.className = 'file-row';
    const info = document.createElement('div');
    info.className = 'file-info';
    const updated = file.updated_unix ? new Date(file.updated_unix * 1000).toLocaleString() : 'n/a';
    info.innerHTML = '<strong>' + file.filename + '</strong><span>' + file.size + ' bytes | ' + updated + '</span>';
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'delete-btn';
    btn.dataset.filename = file.filename;
    btn.textContent = 'Delete';
    row.appendChild(info);
    row.appendChild(btn);
    host.appendChild(row);
  }
}

function renderGoogleFiles(files) {
  const host = document.getElementById('google-files-list');
  if (!host) return;
  host.innerHTML = '';

  const secretsInput = document.getElementById('secrets-file');
  const secretsBtn = document.getElementById('upload-secrets-btn');
  const googleHint = document.getElementById('google-upload-hint');

  const rows = (Array.isArray(files) ? files : []).filter(f => f && f.category === 'secrets');
  const hasSecrets = rows.length > 0;
  if (secretsInput) secretsInput.disabled = hasSecrets;
  if (secretsBtn) secretsBtn.disabled = hasSecrets;
  if (googleHint) {
    googleHint.textContent = hasSecrets
      ? 'Google auth file already exists. Delete secrets.json to upload a replacement.'
      : '';
  }

  if (rows.length === 0) {
    host.innerHTML = '<p class="hint">No secrets.json uploaded.</p>';
    return;
  }

  for (const file of rows) {
    const row = document.createElement('div');
    row.className = 'file-row';
    const info = document.createElement('div');
    info.className = 'file-info';
    const updated = file.updated_unix ? new Date(file.updated_unix * 1000).toLocaleString() : 'n/a';
    info.innerHTML = '<strong>' + file.filename + '</strong><span>' + file.size + ' bytes | ' + updated + '</span>';
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'delete-btn';
    btn.dataset.filename = file.filename;
    btn.textContent = 'Delete';
    row.appendChild(info);
    row.appendChild(btn);
    host.appendChild(row);
  }
}

function renderAppleAccessoryOptions(accessories) {
  const select = document.getElementById('apple-accessory-select');
  if (!select) return;

  const previousValues = new Set(Array.from(select.selectedOptions || []).map((option) => option.value));
  select.innerHTML = '';
  select.appendChild(makeOption('', 'Select an Apple accessory'));

  const rows = Array.isArray(accessories) ? accessories : [];
  for (const accessory of rows) {
    if (!accessory || typeof accessory !== 'object') continue;
    const value = accessory.id != null ? String(accessory.id) : (accessory.name || '');
    if (!value) continue;
    const label = accessory.name || ('Tracker #' + value);
    const count = typeof accessory.key_count === 'number' ? accessory.key_count : 0;
    const source = accessory.source_file || 'unknown';
    const option = makeOption(value, label + ' (' + count + ' keys, ' + source + ')');
    if (previousValues.has(value)) option.selected = true;
    select.appendChild(option);
  }
}

function makeOption(value, label) {
  const option = document.createElement('option');
  option.value = value;
  option.textContent = label;
  return option;
}

function renderGoogleCompoundOptions(compounds) {
  const select = document.getElementById('google-compound-name');
  if (!select) return;

  lastGoogleCompounds = Array.isArray(compounds) ? compounds : [];
  const previousValues = new Set(Array.from(select.selectedOptions || []).map((option) => option.value));
  select.innerHTML = '';
  select.appendChild(makeOption('', 'Select a compound'));

  for (const compound of lastGoogleCompounds) {
    if (!compound || typeof compound !== 'object') continue;
    const value = compound.base_name || compound.compound_id;
    if (!value) continue;
    const label = compound.base_name || compound.compound_id;
    const requested = typeof compound.requested_key_count === 'number' ? compound.requested_key_count : 0;
    const subtitle = requested ? ' (' + requested + ' keys)' : '';
    const option = makeOption(value, label + subtitle);
    if (previousValues.has(value)) option.selected = true;
    select.appendChild(option);
  }
}

function deriveGoogleTargetValue(target) {
  return target && (target.canonic_id || target.canonicId || target.target_id || target.device_id || target.deviceId || target.id || target.label || target.resolved_device_name || target.name) || '';
}

function deriveGoogleTargetLabel(target) {
  if (!target || typeof target !== 'object') return 'Unknown target';
  return target.label || target.resolved_device_name || target.name || target.compound_name || target.canonic_id || target.canonicId || target.id || 'Google target';
}

function renderGoogleTargetOptions(payload) {
  const select = document.getElementById('google-canonic-id');
  if (!select) return;

  const includeCompounds = Boolean(document.getElementById('google-expand-compounds') && document.getElementById('google-expand-compounds').checked);

  const previousValues = new Set(Array.from(select.selectedOptions || []).map((option) => option.value));
  select.innerHTML = '';
  select.appendChild(makeOption('', 'Select a tracker'));

  const rows = Array.isArray(payload && payload.targets) ? payload.targets : [];
  lastGoogleTargets = rows;

  for (const target of rows) {
    const value = deriveGoogleTargetValue(target);
    if (!value) continue;
    const label = deriveGoogleTargetLabel(target);
    const option = makeOption(value, label);
    if (previousValues.has(value)) option.selected = true;
    select.appendChild(option);
  }

  if (includeCompounds) {
    const optgroup = document.createElement('optgroup');
    optgroup.label = 'Compound pieces';
    for (const compound of lastGoogleCompounds) {
      if (!compound || typeof compound !== 'object') continue;
      const compoundLabel = compound.base_name || compound.compound_id;
      const subtags = Array.isArray(compound.subtags) ? compound.subtags : [];
      for (const subtag of subtags) {
        if (!subtag || typeof subtag !== 'object') continue;
        const pieceName = subtag.name || '';
        if (!pieceName) continue;
        const pieceLabel = compoundLabel + ' / ' + pieceName;
        const option = makeOption(pieceLabel, pieceLabel + (subtag.key_count ? ' (' + subtag.key_count + ' keys)' : ''));
        if (previousValues.has(pieceLabel)) option.selected = true;
        optgroup.appendChild(option);
      }
    }
    if (optgroup.children.length > 0) select.appendChild(optgroup);
  }
}

function refreshGoogleTargetOptions() {
  renderGoogleTargetOptions({ targets: lastGoogleTargets });
}

function getMultiSelectedValues(selectId) {
  const select = document.getElementById(selectId);
  if (!select) return [];
  return Array.from(select.selectedOptions || [])
    .map((option) => option.value)
    .filter((value) => Boolean(value));
}

async function getJson(path) {
  const res = await fetch(path, { credentials: 'same-origin' });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Request failed');
  return data;
}

async function postFile(path, file) {
  const body = new FormData();
  body.append('file', file);
  const res = await fetch(path, { method: 'POST', body, credentials: 'same-origin' });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Upload failed');
  return data;
}

async function postJson(path, payload) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify(payload || {})
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Request failed');
  return data;
}

async function refreshFiles() {
  try {
    const data = await getJson('/api/keyfiles');
    renderAppleFiles(data.files || []);
    renderGoogleFiles(data.files || []);
  } catch (err) {
    renderAppleFiles([]);
    renderGoogleFiles([]);
  }
}

async function deleteKeyfile(filename) {
  const res = await fetch('/api/keyfiles/' + encodeURIComponent(filename), {
    method: 'DELETE',
    credentials: 'same-origin'
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Delete failed');
  return data;
}

// ── Tags rendering (structured cards) ──
function renderTags(data) {
  const container = document.getElementById('tags-container');
  if (!container) return;
  if (debugMode) showJson('tags-output', data);

  const appleTags = data.apple_tags || [];
  const googleCompounds = data.google_compounds || [];

  if (appleTags.length === 0 && googleCompounds.length === 0) {
    container.innerHTML = '<p class="hint">No tags found. Upload key files to get started.</p>';
    return;
  }

  let html = '';
  for (const tag of appleTags) {
    html += '<div class="tag-card apple"><div class="tag-name">' + (tag.name || 'Tracker #' + tag.id) + '</div><div class="tag-meta">' + tag.key_count + ' keys | ' + (tag.source_file || 'unknown') + '</div></div>';
  }
  for (const compound of googleCompounds) {
    const subtagCount = compound.subtags ? compound.subtags.length : 0;
    html += '<div class="tag-card google"><div class="tag-name">' + (compound.base_name || compound.compound_id) + '</div><div class="tag-meta">' + compound.requested_key_count + ' keys | ' + subtagCount + ' subtags</div></div>';
  }
  container.innerHTML = html;
}

async function refreshTags() {
  try {
    const data = await getJson('/api/tags/raw');
    renderTags(data);
    renderAppleAccessoryOptions(data.apple_tags || []);
    renderGoogleCompoundOptions(data.google_compounds || []);
    renderAppleAccessoriesList(data.apple_tags || []);
    await listGoogleTargets(false);
  } catch (err) {
    const container = document.getElementById('tags-container');
    if (container) container.innerHTML = '<p class="hint" style="color:#9a2f2f">' + err + '</p>';
    if (debugMode) showJson('tags-output', { error: String(err) });
  }
}

// ── Status rendering (structured badges) ──
function renderStatus(data) {
  const container = document.getElementById('status-container');
  if (!container) return;
  if (debugMode) showJson('status-output', data);

  const providers = [
    { key: 'google', label: 'Google' },
    { key: 'apple', label: 'Apple' },
    { key: 'apple_merge', label: 'Apple Merge' }
  ];

  let html = '';
  for (const { key, label } of providers) {
    const status = data[key];
    let dotClass = 'none';
    let text = 'No data';
    if (status) {
      if (status.ok === false) { dotClass = 'error'; text = status.error || 'Failed'; }
      else if (status.ok === true) { dotClass = 'ok'; text = status.kind || 'OK'; }
    }
    const timeStr = status && status.timestamp_unix ? new Date(status.timestamp_unix * 1000).toLocaleTimeString() : '';
    html += '<div class="status-badge"><span class="status-dot ' + dotClass + '"></span><span class="status-label">' + label + '</span><span>' + text + '</span>' + (timeStr ? '<span class="status-time">' + timeStr + '</span>' : '') + '</div>';
  }
  container.innerHTML = html;
}

async function refreshStatus() {
  try {
    const data = await getJson('/api/status/fetch');
    renderStatus(data);
  } catch (err) {
    const container = document.getElementById('status-container');
    if (container) container.innerHTML = '<p class="hint" style="color:#9a2f2f">' + err + '</p>';
    if (debugMode) showJson('status-output', { error: String(err) });
  }
}

// ── Alerts rendering ──
function renderAlerts(data) {
  const container = document.getElementById('errors-container');
  if (!container) return;
  if (debugMode) showJson('errors-output', data);

  const alerts = data.alerts || [];
  if (alerts.length === 0) { container.innerHTML = '<p class="hint">No errors</p>'; return; }

  let html = '';
  for (const alert of alerts) {
    const timeStr = alert.created_unix ? new Date(alert.created_unix * 1000).toLocaleString() : '';
    const typeStr = alert.type ? ' <span class="alert-type">' + alert.type + '</span>' : '';
    const targetStr = alert.target ? ' <span class="alert-target">[' + (alert.target || '') + ']</span>' : '';
    html += '<div class="error-item"><strong>' + (alert.provider || 'unknown') + '</strong>: ' + (alert.error || 'Unknown error') + typeStr + targetStr + (timeStr ? '<br><small>' + timeStr + '</small>' : '') + '</div>';
  }
  container.innerHTML = html;
}

async function refreshErrors() {
  try {
    const data = await getJson('/api/status/alerts');
    renderAlerts(data);
  } catch (err) {
    const container = document.getElementById('errors-container');
    if (container) container.innerHTML = '<p class="hint" style="color:#9a2f2f">' + err + '</p>';
    if (debugMode) showJson('errors-output', { error: String(err) });
  }
}

// ── History rendering ──
function renderHistoryMap(events, options = {}) {
  if (!map || !markerLayer || !window.L) {
    console.warn('renderHistoryMap: map not ready', { hasMap: !!map, hasMarkerLayer: !!markerLayer, hasLeaflet: !!window.L });
    return;
  }

  try {
    markerLayer.clearLayers();
    if (polylineLayer) polylineLayer.clearLayers();
  } catch (err) {
    console.error('renderHistoryMap: layer clear error', err);
    return;
  }

  const connectDots = options.connectDots || false;
  const highlightLatest = options.highlightLatest !== false;

  // Sort by timestamp ascending for connection order
  const sorted = [...(events || [])].sort((a, b) => (a.timestamp_unix || 0) - (b.timestamp_unix || 0));
  const points = [];

  if (!sorted.length) {
    console.log('renderHistoryMap: no events to display');
    return;
  }

  console.log('renderHistoryMap: rendering', sorted.length, 'events');

  // Track latest timestamp per provider
  const latestByProvider = {};
  for (const event of sorted) {
    const provider = event.provider || 'unknown';
    const ts = event.timestamp_unix || 0;
    if (!latestByProvider[provider] || ts > latestByProvider[provider]) {
      latestByProvider[provider] = ts;
    }
  }

  for (const event of sorted) {
    if (typeof event.latitude !== 'number' || typeof event.longitude !== 'number') continue;
    points.push([event.latitude, event.longitude]);

    const provider = event.provider || 'unknown';
    const isLatest = (event.timestamp_unix || 0) === latestByProvider[provider];

    let fillColor = event.provider === 'google' ? '#1f78ff' : '#18a96b';
    let fillOpacity = 0.8;
    let radius = 6;
    let weight = 1;

    if (highlightLatest && !isLatest) {
      fillOpacity = 0.3; // 70% transparent
    } else if (highlightLatest && isLatest) {
      fillColor = event.provider === 'google' ? '#1fcbff' : '#29ffa2';
      fillOpacity = 1.0;
      radius = 8;
      weight = 2;
    }

    try {
      const marker = L.circleMarker([event.latitude, event.longitude], {
        radius: radius,
        color: fillColor,
        fillColor: fillColor,
        fillOpacity: fillOpacity,
        weight: weight
      });
      marker.bindPopup(
        event.provider + ' | ' + (event.tag || 'tag') + ' | ' +
        new Date((event.timestamp_unix || 0) * 1000).toLocaleString() +
        (isLatest && highlightLatest ? ' LATEST' : '')
      );
      marker.addTo(markerLayer);
    } catch (err) {
      console.error('renderHistoryMap: marker creation error', err, event);
    }
  }

  // Connect dots with polyline
  if (connectDots && points.length > 1) {
    if (!polylineLayer) polylineLayer = L.layerGroup().addTo(map);
    try {
      const line = L.polyline(points, {
        color: '#ff8c00',
        weight: 2,
        opacity: 0.5,
        dashArray: '5, 8'
      });
      line.addTo(polylineLayer);
    } catch (err) {
      console.error('renderHistoryMap: polyline error', err);
    }
  }

  if (points.length > 0) {
    try {
      map.fitBounds(points, { padding: [20, 20], maxZoom: 14 });
    } catch (err) {
      console.error('renderHistoryMap: fitBounds error', err);
    }
  }
}

async function refreshCombinedHistory() {
  try {
    const slider = document.getElementById('history-days-slider');
    const days = slider ? parseInt(slider.value, 10) : 7;
    const connectDots = document.getElementById('connect-dots-toggle')?.checked || false;
    const highlightLatest = document.getElementById('highlight-latest-toggle')?.checked !== false;

    console.log('refreshCombinedHistory: days=', days, 'connectDots=', connectDots, 'highlightLatest=', highlightLatest);

    const data = await getJson('/api/history/combined?days=' + encodeURIComponent(days));
    console.log('refreshCombinedHistory: received', (data.events || []).length, 'events');
    renderHistoryMap(data.events || [], { connectDots, highlightLatest });
    if (debugMode) showJson('history-output', data);
  } catch (err) {
    console.error('refreshCombinedHistory: error', err);
    if (debugMode) showJson('history-output', { error: String(err) });
  }
}

// ── Google fetch ──
async function listGoogleTargets(showFeedback = true) {
  try {
    const data = await getJson('/api/google/targets');
    renderGoogleTargetOptions(data);
    renderGoogleTargetsList(data.targets || [], lastGoogleCompounds);
    const count = Array.isArray(data.targets) ? data.targets.length : 0;
    if (showFeedback) {
      setGoogleFeedback(count > 0 ? 'Loaded ' + count + ' Google target' + (count === 1 ? '' : 's') + '.' : 'No Google targets were returned.');
    }
    if (debugMode) showJson('google-output', data);
  } catch (err) {
    if (showFeedback) {
      setGoogleFeedback(String(err), true);
    }
    if (debugMode) showJson('google-output', { error: String(err) });
  }
}

async function refreshGoogleKeys() {
  try {
    const data = await postJson('/api/google/refresh-keys', {});
    setGoogleFeedback('Google keys refreshed successfully at ' + new Date().toLocaleTimeString());
    if (debugMode) showJson('google-output', data);
    await listGoogleTargets(false);
    refreshGoogleTargetOptions();
    await refreshStatus();
  } catch (err) {
    setGoogleFeedback(String(err), true);
    if (debugMode) showJson('google-output', { error: String(err) });
  }
}

async function fetchAllSelected() {
  const appleSelected = getMultiSelectedValues('apple-accessory-select');
  const googleCompoundsSelected = getMultiSelectedValues('google-compound-name');
  const googleTrackersSelected = getMultiSelectedValues('google-canonic-id');

  if (appleSelected.length === 0 && googleCompoundsSelected.length === 0 && googleTrackersSelected.length === 0) {
    setOverviewGoogleFeedback('Nothing selected. Pick any Apple or Google entries and run fetch.');
    return;
  }

  const failures = [];
  let successCount = 0;

  if (appleSelected.length > 0) {
    try {
      await postJson('/api/apple/fetch', { days: 7 });
      successCount += 1;
    } catch (err) {
      failures.push('Apple fetch: ' + String(err));
    }
  }

  for (const compoundName of googleCompoundsSelected) {
    try {
      await postJson('/api/google/fetch', { compound_name: compoundName });
      successCount += 1;
    } catch (err) {
      failures.push('Google compound ' + compoundName + ': ' + String(err));
    }
  }

  for (const canonicId of googleTrackersSelected) {
    try {
      await postJson('/api/google/fetch', { canonic_id: canonicId });
      successCount += 1;
    } catch (err) {
      failures.push('Google tracker ' + canonicId + ': ' + String(err));
    }
  }

  if (failures.length > 0) {
    setOverviewGoogleFeedback('Completed with errors. Success=' + successCount + ', Failed=' + failures.length + '. ' + failures[0], true);
  } else {
    setOverviewGoogleFeedback('Fetched selected items successfully. Operations=' + successCount + '.');
  }

  if (debugMode) {
    showJson('google-output', {
      action: 'fetch_selected',
      selected: {
        apple: appleSelected,
        google_compounds: googleCompoundsSelected,
        google_trackers: googleTrackersSelected,
      },
      success_count: successCount,
      failures,
    });
  }

  await refreshStatus();
  await refreshCombinedHistory();
  await refreshErrors();
}

async function refreshGoogleKeysForSelected() {
  const googleCompoundsSelected = getMultiSelectedValues('google-compound-name');
  const googleTrackersSelected = getMultiSelectedValues('google-canonic-id');
  const totalGoogleSelected = googleCompoundsSelected.length + googleTrackersSelected.length;

  if (totalGoogleSelected === 0) {
    setOverviewGoogleFeedback('No Google selections found. Key refresh skipped.');
    return;
  }

  try {
    await postJson('/api/google/refresh-keys', {});
    setOverviewGoogleFeedback('Google keys refreshed for current selection (' + totalGoogleSelected + ' selected).');
    await listGoogleTargets(false);
    await refreshStatus();
  } catch (err) {
    setOverviewGoogleFeedback(String(err), true);
    if (debugMode) showJson('google-output', { error: String(err), action: 'refresh_keys_selected' });
  }
}

async function refreshAppleLocations() {
  try {
    const data = await postJson('/api/apple/fetch', { days: 7 });
    if (debugMode) showJson('apple-output', data);
    await refreshStatus();
  } catch (err) {
    if (debugMode) showJson('apple-output', { error: String(err) });
  }
}

// ── Event listeners ──
function initDashboard() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
      btn.classList.add('active');
      const tab = document.getElementById('tab-' + btn.dataset.tab);
      if (tab) tab.classList.add('active');
    });
  });

  // History slider display update
  const historySlider = document.getElementById('history-days-slider');
  if (historySlider) {
    historySlider.addEventListener('input', () => {
      const valueEl = document.getElementById('history-days-value');
      if (valueEl) valueEl.textContent = historySlider.value;
    });
  }

  // Re-fetch history on toggle changes
  const connectDotsToggle = document.getElementById('connect-dots-toggle');
  if (connectDotsToggle) {
    connectDotsToggle.addEventListener('change', refreshCombinedHistory);
  }

  const highlightLatestToggle = document.getElementById('highlight-latest-toggle');
  if (highlightLatestToggle) {
    highlightLatestToggle.addEventListener('change', refreshCombinedHistory);
  }

  const reloadHistoryBtn = document.getElementById('reload-history');
  if (reloadHistoryBtn) reloadHistoryBtn.addEventListener('click', withLoading(reloadHistoryBtn, refreshCombinedHistory));

  // Clear alerts button
  const clearAlertsBtn = document.getElementById('clear-alerts-btn');
  if (clearAlertsBtn) {
    clearAlertsBtn.addEventListener('click', withLoading(clearAlertsBtn, async () => {
      try {
        const data = await postJson('/api/status/alerts/clear', {});
        await refreshErrors();
      } catch (err) {
        const container = document.getElementById('errors-container');
        if (container) container.innerHTML = '<p class="hint" style="color:#9a2f2f">' + err + '</p>';
      }
    }));
  }
  const fetchSelectedBtn = document.getElementById('fetch-selected-btn');
  if (fetchSelectedBtn) fetchSelectedBtn.addEventListener('click', withLoading(fetchSelectedBtn, fetchAllSelected));
  const refreshKeysSelectedBtn = document.getElementById('refresh-keys-selected-btn');
  if (refreshKeysSelectedBtn) refreshKeysSelectedBtn.addEventListener('click', withLoading(refreshKeysSelectedBtn, refreshGoogleKeysForSelected));

  const googleExpandCompounds = document.getElementById('google-expand-compounds');
  if (googleExpandCompounds) {
    googleExpandCompounds.addEventListener('change', () => {
      refreshGoogleTargetOptions();
      if (debugMode) showJson('google-output', {
        google_targets: lastGoogleTargets.length,
        google_compounds: lastGoogleCompounds.length,
        include_compound_pieces: googleExpandCompounds.checked
      });
    });
  }

  // Google refresh keys button (Keys tab)
  const refreshGoogleKeysBtn = document.getElementById('refresh-google-keys-btn');
  if (refreshGoogleKeysBtn) {
    refreshGoogleKeysBtn.addEventListener('click', withLoading(refreshGoogleKeysBtn, async () => {
      try {
        const data = await postJson('/api/google/refresh-keys', {});
        setGoogleFeedback('Google keys refreshed successfully at ' + new Date().toLocaleTimeString());
        if (debugMode) showJson('google-output', data);
        await listGoogleTargets(false);
        await refreshStatus();
      } catch (err) {
        setGoogleFeedback(String(err), true);
        if (debugMode) showJson('google-output', { error: String(err) });
      }
    }));
  }

  // File delete handlers (per section)
  const appleFilesList = document.getElementById('apple-files-list');
  if (appleFilesList) {
    appleFilesList.addEventListener('click', async (evt) => {
      const target = evt.target;
      if (!(target instanceof HTMLElement) || !target.classList.contains('delete-btn')) return;
      const filename = target.dataset.filename;
      if (!filename) return;
      if (!window.confirm('Delete ' + filename + '?')) return;
      try {
        await deleteKeyfile(filename);
        setAppleFeedback('Deleted ' + filename);
        await refreshFiles();
        await refreshTags();
        await refreshErrors();
      } catch (err) {
        setAppleFeedback(String(err), true);
      }
    });
  }

  const googleFilesList = document.getElementById('google-files-list');
  if (googleFilesList) {
    googleFilesList.addEventListener('click', async (evt) => {
      const target = evt.target;
      if (!(target instanceof HTMLElement) || !target.classList.contains('delete-btn')) return;
      const filename = target.dataset.filename;
      if (!filename) return;
      if (!window.confirm('Delete ' + filename + '?')) return;
      try {
        await deleteKeyfile(filename);
        setGoogleFeedback('Deleted ' + filename);
        await refreshFiles();
        await refreshTags();
        await refreshErrors();
        await listGoogleTargets(false);
      } catch (err) {
        setGoogleFeedback(String(err), true);
      }
    });
  }

  const accessoriesForm = document.getElementById('upload-accessories-form');
  if (accessoriesForm) {
    accessoriesForm.addEventListener('submit', async (evt) => {
      evt.preventDefault();
      const fileInput = document.getElementById('accessories-file');
      const file = fileInput && fileInput.files ? fileInput.files[0] : null;
      if (!file) return;
      try {
        await postFile('/api/upload/accessories', file);
        setAppleFeedback('Uploaded Apple accessories file');
        await refreshFiles();
        await refreshTags();
      } catch (err) {
        setAppleFeedback(String(err), true);
      }
    });
  }

  const secretsForm = document.getElementById('upload-secrets-form');
  if (secretsForm) {
    secretsForm.addEventListener('submit', async (evt) => {
      evt.preventDefault();
      const secretsBtn = document.getElementById('upload-secrets-btn');
      if (secretsBtn && secretsBtn.disabled) {
        setGoogleFeedback('Delete existing secrets.json before uploading a new one.', true);
        return;
      }
      const fileInput = document.getElementById('secrets-file');
      const file = fileInput && fileInput.files ? fileInput.files[0] : null;
      if (!file) return;
      try {
        await postFile('/api/upload/secrets', file);
        setGoogleFeedback('Uploaded Google secrets file');
        await refreshFiles();
        await refreshTags();
        await listGoogleTargets(false);
      } catch (err) {
        setGoogleFeedback(String(err), true);
      }
    });
  }

  initMap();
  // Invalidate map size after layout settles (flexbox map container)
  setTimeout(() => { if (map) map.invalidateSize(); }, 100);

  refreshFiles();
  refreshTags();
  refreshStatus();
  refreshErrors();
  refreshCombinedHistory();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initDashboard);
} else {
  initDashboard();
}
