const map = L.map('map').setView([37.7749, -122.4194], 3);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '&copy; OpenStreetMap contributors'
}).addTo(map);
const markerLayer = L.layerGroup().addTo(map);

function showJson(id, payload) {
  document.getElementById(id).textContent = JSON.stringify(payload, null, 2);
}

function setFilesFeedback(message, isError = false) {
  const el = document.getElementById('files-feedback');
  el.textContent = message || '';
  el.style.color = isError ? '#9a2f2f' : '#41565a';
}

function renderFiles(files) {
  const host = document.getElementById('files-list');
  host.innerHTML = '';
  const secretsInput = document.getElementById('secrets-file');
  const secretsBtn = document.getElementById('upload-secrets-btn');
  const googleHint = document.getElementById('google-upload-hint');

  const rows = Array.isArray(files) ? files : [];
  const hasSecrets = rows.some((f) => f && f.filename === 'secrets.json');
  if (secretsInput) {
    secretsInput.disabled = hasSecrets;
  }
  if (secretsBtn) {
    secretsBtn.disabled = hasSecrets;
  }
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
    info.innerHTML = `<strong>${file.filename}</strong><span>${file.category} | ${file.size} bytes | ${updated}</span>`;

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

async function getJson(path) {
  const res = await fetch(path, { credentials: 'same-origin' });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error || 'Request failed');
  }
  return data;
}

async function postFile(path, file) {
  const body = new FormData();
  body.append('file', file);
  const res = await fetch(path, {
    method: 'POST',
    body,
    credentials: 'same-origin'
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error || 'Upload failed');
  }
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
  const res = await fetch(`/api/keyfiles/${encodeURIComponent(filename)}`, {
    method: 'DELETE',
    credentials: 'same-origin'
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error || 'Delete failed');
  }
  return data;
}

async function refreshTags() {
  try {
    const data = await getJson('/api/tags/raw');
    showJson('tags-output', data);
  } catch (err) {
    showJson('tags-output', { error: String(err) });
  }
}

async function refreshStatus() {
  try {
    const data = await getJson('/api/status/fetch');
    showJson('status-output', data);
  } catch (err) {
    showJson('status-output', { error: String(err) });
  }
}

async function refreshErrors() {
  try {
    const data = await getJson('/api/status/errors');
    showJson('errors-output', data);
  } catch (err) {
    showJson('errors-output', { error: String(err) });
  }
}

function renderHistoryMap(events) {
  markerLayer.clearLayers();
  const points = [];
  for (const event of events || []) {
    if (typeof event.latitude !== 'number' || typeof event.longitude !== 'number') {
      continue;
    }
    points.push([event.latitude, event.longitude]);
    const color = event.provider === 'google' ? '#1f78ff' : '#18a96b';
    const marker = L.circleMarker([event.latitude, event.longitude], {
      radius: 6,
      color,
      fillColor: color,
      fillOpacity: 0.8
    });
    marker.bindPopup(`${event.provider} | ${event.tag || 'tag'} | ${new Date((event.timestamp_unix || 0) * 1000).toLocaleString()}`);
    marker.addTo(markerLayer);
  }

  if (points.length > 0) {
    map.fitBounds(points, { padding: [20, 20], maxZoom: 14 });
  }
}

async function refreshCombinedHistory() {
  try {
    const data = await getJson('/api/history/combined?limit=300');
    showJson('history-output', data);
    renderHistoryMap(data.events || []);
  } catch (err) {
    showJson('history-output', { error: String(err) });
  }
}

async function postJson(path, payload) {
  const res = await fetch(path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    credentials: 'same-origin',
    body: JSON.stringify(payload || {})
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error || 'Request failed');
  }
  return data;
}

async function listGoogleTargets() {
  try {
    const data = await getJson('/api/google/targets');
    showJson('google-output', data);
  } catch (err) {
    showJson('google-output', { error: String(err) });
  }
}

async function refreshGoogleKeys() {
  try {
    const data = await postJson('/api/google/refresh-keys', {});
    showJson('google-output', data);
    await refreshStatus();
  } catch (err) {
    showJson('google-output', { error: String(err) });
  }
}

async function refreshAppleLocations() {
  try {
    const data = await postJson('/api/apple/fetch', { days: 7 });
    showJson('apple-output', data);
    await refreshStatus();
  } catch (err) {
    showJson('apple-output', { error: String(err) });
  }
}

document.getElementById('refresh-files').addEventListener('click', refreshFiles);
document.getElementById('refresh-tags').addEventListener('click', refreshTags);
document.getElementById('refresh-status').addEventListener('click', refreshStatus);
document.getElementById('refresh-errors').addEventListener('click', refreshErrors);
document.getElementById('reload-history').addEventListener('click', refreshCombinedHistory);
document.getElementById('apple-fetch').addEventListener('click', refreshAppleLocations);
document.getElementById('google-targets').addEventListener('click', listGoogleTargets);
document.getElementById('google-refresh-keys').addEventListener('click', refreshGoogleKeys);
document.getElementById('files-list').addEventListener('click', async (evt) => {
  const target = evt.target;
  if (!(target instanceof HTMLElement) || !target.classList.contains('delete-btn')) {
    return;
  }

  const filename = target.dataset.filename;
  if (!filename) {
    return;
  }

  const confirmed = window.confirm(`Delete ${filename}?`);
  if (!confirmed) {
    return;
  }

  try {
    await deleteKeyfile(filename);
    setFilesFeedback(`Deleted ${filename}`);
    await refreshFiles();
    await refreshTags();
    await refreshErrors();
  } catch (err) {
    setFilesFeedback(String(err), true);
  }
});

document.getElementById('upload-accessories-form').addEventListener('submit', async (evt) => {
  evt.preventDefault();
  const file = document.getElementById('accessories-file').files[0];
  if (!file) {
    return;
  }

  try {
    await postFile('/api/upload/accessories', file);
    setFilesFeedback('Uploaded Apple file');
    await refreshFiles();
    await refreshTags();
  } catch (err) {
    setFilesFeedback(String(err), true);
  }
});

document.getElementById('upload-secrets-form').addEventListener('submit', async (evt) => {
  evt.preventDefault();
  const secretsBtn = document.getElementById('upload-secrets-btn');
  if (secretsBtn && secretsBtn.disabled) {
    setFilesFeedback('Delete existing secrets.json before uploading a new one.', true);
    return;
  }
  const file = document.getElementById('secrets-file').files[0];
  if (!file) {
    return;
  }

  try {
    await postFile('/api/upload/secrets', file);
    setFilesFeedback('Uploaded Google file');
    await refreshFiles();
    await refreshTags();
  } catch (err) {
    setFilesFeedback(String(err), true);
  }
});

document.getElementById('google-fetch-form').addEventListener('submit', async (evt) => {
  evt.preventDefault();
  const canonicId = document.getElementById('google-canonic-id').value.trim();
  const compoundName = document.getElementById('google-compound-name').value.trim();
  if ((canonicId && compoundName) || (!canonicId && !compoundName)) {
    showJson('google-output', { error: 'Provide either canonic id or compound name' });
    return;
  }

  const body = canonicId ? { canonic_id: canonicId } : { compound_name: compoundName };
  try {
    const data = await postJson('/api/google/fetch', body);
    showJson('google-output', data);
    await refreshStatus();
  } catch (err) {
    showJson('google-output', { error: String(err) });
  }
});

refreshFiles();
refreshTags();
refreshStatus();
refreshErrors();
refreshCombinedHistory();
