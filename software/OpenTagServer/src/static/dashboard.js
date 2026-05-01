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
}

function showJson(id, payload) {
  const host = document.getElementById(id);
  if (host) host.textContent = JSON.stringify(payload, null, 2);
}

function setFilesFeedback(message, isError = false) {
  const el = document.getElementById('files-feedback');
  el.textContent = message || '';
  el.style.color = isError ? '#9a2f2f' : '#41565a';
}

function setGoogleFeedback(message, isError = false) {
  const el = document.getElementById('google-targets-feedback');
  if (!el) return;
  el.textContent = message || '';
  el.style.color = isError ? '#9a2f2f' : '#41565a';
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

// ── File management ──
function renderFiles(files) {
  const host = document.getElementById('files-list');
  if (!host) return;
  host.innerHTML = '';
  const secretsInput = document.getElementById('secrets-file');
  const secretsBtn = document.getElementById('upload-secrets-btn');
  const googleHint = document.getElementById('google-upload-hint');

  const rows = Array.isArray(files) ? files : [];
  const hasSecrets = rows.some((f) => f && f.filename === 'secrets.json');
  if (secretsInput) secretsInput.disabled = hasSecrets;
  if (secretsBtn) secretsBtn.disabled = hasSecrets;
  if (googleHint) {
    googleHint.textContent = hasSecrets
      ? 'Google auth file already exists. Delete secrets.json to upload a replacement.'
      : '';
  }

  if (rows.length === 0) {
    host.innerHTML = '<p class="hint">No auth files uploaded yet.</p>';
    return;
  }

  for (const file of rows) {
    const row = document.createElement('div');
    row.className = 'file-row';
    const info = document.createElement('div');
    info.className = 'file-info';
    const updated = file.updated_unix ? new Date(file.updated_unix * 1000).toLocaleString() : 'n/a';
    info.innerHTML = '<strong>' + file.filename + '</strong><span>' + file.category + ' | ' + file.size + ' bytes | ' + updated + '</span>';
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
    renderFiles(data.files || []);
    setFilesFeedback('');
  } catch (err) {
    renderFiles([]);
    setFilesFeedback(String(err), true);
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

// ── Errors rendering ──
function renderErrors(data) {
  const container = document.getElementById('errors-container');
  if (!container) return;
  if (debugMode) showJson('errors-output', data);

  const errors = data.errors || [];
  if (errors.length === 0) { container.innerHTML = '<p class="hint">No errors</p>'; return; }

  let html = '';
  for (const err of errors) {
    const timeStr = err.timestamp_unix ? new Date(err.timestamp_unix * 1000).toLocaleString() : '';
    html += '<div class="error-item"><strong>' + (err.provider || 'unknown') + '</strong>: ' + (err.error || 'Unknown error') + (timeStr ? '<br><small>' + timeStr + '</small>' : '') + '</div>';
  }
  container.innerHTML = html;
}

async function refreshErrors() {
  try {
    const data = await getJson('/api/status/errors');
    renderErrors(data);
  } catch (err) {
    const container = document.getElementById('errors-container');
    if (container) container.innerHTML = '<p class="hint" style="color:#9a2f2f">' + err + '</p>';
    if (debugMode) showJson('errors-output', { error: String(err) });
  }
}

// ── History rendering ──
function renderHistoryMap(events) {
  if (!map || !markerLayer || !window.L) return;
  markerLayer.clearLayers();
  const points = [];
  for (const event of events || []) {
    if (typeof event.latitude !== 'number' || typeof event.longitude !== 'number') continue;
    points.push([event.latitude, event.longitude]);
    const color = event.provider === 'google' ? '#1f78ff' : '#18a96b';
    const marker = L.circleMarker([event.latitude, event.longitude], {
      radius: 6, color: color, fillColor: color, fillOpacity: 0.8
    });
    marker.bindPopup(event.provider + ' | ' + (event.tag || 'tag') + ' | ' + new Date((event.timestamp_unix || 0) * 1000).toLocaleString());
    marker.addTo(markerLayer);
  }
  if (points.length > 0) map.fitBounds(points, { padding: [20, 20], maxZoom: 14 });
}

async function refreshCombinedHistory() {
  try {
    const data = await getJson('/api/history/combined?limit=300');
    renderHistoryMap(data.events || []);
    if (debugMode) showJson('history-output', data);
  } catch (err) {
    if (debugMode) showJson('history-output', { error: String(err) });
  }
}

// ── Google fetch ──
async function listGoogleTargets(showFeedback = true) {
  try {
    const data = await getJson('/api/google/targets');
    renderGoogleTargetOptions(data);
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
    setGoogleFeedback('Nothing selected. Pick any Apple or Google entries and run fetch.');
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
    setGoogleFeedback('Completed with errors. Success=' + successCount + ', Failed=' + failures.length + '. ' + failures[0], true);
  } else {
    setGoogleFeedback('Fetched selected items successfully. Operations=' + successCount + '.');
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
    setGoogleFeedback('No Google selections found. Key refresh skipped.');
    return;
  }

  try {
    await postJson('/api/google/refresh-keys', {});
    setGoogleFeedback('Google keys refreshed for current selection (' + totalGoogleSelected + ' selected).');
    await listGoogleTargets(false);
    await refreshStatus();
  } catch (err) {
    setGoogleFeedback(String(err), true);
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

  const refreshFilesBtn = document.getElementById('refresh-files');
  if (refreshFilesBtn) refreshFilesBtn.addEventListener('click', withLoading(refreshFilesBtn, refreshFiles));
  const reloadHistoryBtn = document.getElementById('reload-history');
  if (reloadHistoryBtn) reloadHistoryBtn.addEventListener('click', withLoading(reloadHistoryBtn, refreshCombinedHistory));
  const refreshTagsBtn = document.getElementById('refresh-tags');
  if (refreshTagsBtn) refreshTagsBtn.addEventListener('click', withLoading(refreshTagsBtn, refreshTags));
  const refreshStatusBtn = document.getElementById('refresh-status');
  if (refreshStatusBtn) refreshStatusBtn.addEventListener('click', withLoading(refreshStatusBtn, refreshStatus));
  const refreshErrorsBtn = document.getElementById('refresh-errors');
  if (refreshErrorsBtn) refreshErrorsBtn.addEventListener('click', withLoading(refreshErrorsBtn, refreshErrors));
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

  const appleAccessorySelect = document.getElementById('apple-accessory-select');
  if (appleAccessorySelect) {
    appleAccessorySelect.addEventListener('change', () => {
      const selected = appleAccessorySelect.options[appleAccessorySelect.selectedIndex];
      if (selected && selected.value) {
        setFilesFeedback('Selected Apple accessory: ' + selected.textContent);
      } else {
        setFilesFeedback('');
      }
    });
  }

  const filesList = document.getElementById('files-list');
  if (filesList) {
    filesList.addEventListener('click', async (evt) => {
      const target = evt.target;
      if (!(target instanceof HTMLElement) || !target.classList.contains('delete-btn')) return;
      const filename = target.dataset.filename;
      if (!filename) return;
      if (!window.confirm('Delete ' + filename + '?')) return;
      try {
        await deleteKeyfile(filename);
        setFilesFeedback('Deleted ' + filename);
        await refreshFiles();
        await refreshTags();
        await refreshErrors();
      } catch (err) {
        setFilesFeedback(String(err), true);
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
        setFilesFeedback('Uploaded Apple file');
        await refreshFiles();
        await refreshTags();
      } catch (err) {
        setFilesFeedback(String(err), true);
      }
    });
  }

  const secretsForm = document.getElementById('upload-secrets-form');
  if (secretsForm) {
    secretsForm.addEventListener('submit', async (evt) => {
      evt.preventDefault();
      const secretsBtn = document.getElementById('upload-secrets-btn');
      if (secretsBtn && secretsBtn.disabled) {
        setFilesFeedback('Delete existing secrets.json before uploading a new one.', true);
        return;
      }
      const fileInput = document.getElementById('secrets-file');
      const file = fileInput && fileInput.files ? fileInput.files[0] : null;
      if (!file) return;
      try {
        await postFile('/api/upload/secrets', file);
        setFilesFeedback('Uploaded Google file');
        await refreshFiles();
        await refreshTags();
      } catch (err) {
        setFilesFeedback(String(err), true);
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
