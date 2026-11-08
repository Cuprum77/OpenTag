// Shared by the admin (user management) and account (password change) pages.
(function () {
  const toggle = document.getElementById('theme-toggle');
  let dark = false;
  try { dark = localStorage.getItem('opentag_dark') === 'true'; } catch (e) { /* storage blocked */ }
  document.body.classList.toggle('dark', dark);
  if (toggle) {
    toggle.textContent = dark ? '☀️' : '🌙';
    toggle.addEventListener('click', () => {
      dark = !dark;
      document.body.classList.toggle('dark', dark);
      toggle.textContent = dark ? '☀️' : '🌙';
      try { localStorage.setItem('opentag_dark', dark); } catch (e) { /* storage blocked */ }
    });
  }
})();

async function api(method, path, payload) {
  const res = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers: payload === undefined ? {} : { 'Content-Type': 'application/json' },
    body: payload === undefined ? undefined : JSON.stringify(payload)
  });
  let data = {};
  try { data = await res.json(); } catch (e) { /* non-JSON */ }
  if (res.status === 401) { window.location.href = '/auth/login'; throw new Error('Session expired'); }
  if (!res.ok) throw new Error(data.error || ('Request failed (' + res.status + ')'));
  return data;
}

function feedback(id, message, isError) {
  const node = document.getElementById(id);
  if (!node) return;
  node.textContent = message;
  node.classList.remove('feedback-success', 'feedback-error');
  node.classList.add(isError ? 'feedback-error' : 'feedback-success');
}

// ── Admin: users ──
async function loadUsers() {
  const host = document.getElementById('users-list');
  if (!host) return;
  let users;
  try {
    users = (await api('GET', '/api/admin/users')).users;
  } catch (err) {
    host.textContent = err.message;
    return;
  }
  host.textContent = '';
  for (const user of users) {
    const info = document.createElement('div');
    info.className = 'file-info';
    const name = document.createElement('strong');
    name.textContent = user.username;
    const meta = document.createElement('span');
    meta.textContent = user.role + ' | created ' + new Date(user.created_unix * 1000).toLocaleDateString();
    info.append(name, meta);

    const row = document.createElement('div');
    row.className = 'file-row';
    row.appendChild(info);
    if (user.role === 'user') {
      const actions = document.createElement('div');
      const reset = document.createElement('button');
      reset.type = 'button';
      reset.textContent = 'Reset password';
      reset.addEventListener('click', () => resetPassword(user));
      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'delete-btn';
      del.textContent = 'Delete';
      del.addEventListener('click', () => deleteUser(user));
      actions.append(reset, ' ', del);
      row.appendChild(actions);
    }
    host.appendChild(row);
  }
}

async function resetPassword(user) {
  const password = window.prompt('New password for ' + user.username + ' (min. 8 characters):');
  if (!password) return;
  try {
    await api('POST', '/api/admin/users/' + user.id + '/password', { password });
    feedback('admin-feedback', 'Password of ' + user.username + ' changed; their sessions were signed out.');
  } catch (err) {
    feedback('admin-feedback', err.message, true);
  }
}

async function deleteUser(user) {
  const typed = window.prompt('This permanently deletes ' + user.username + ', including their key files, devices and ' +
    'location history. Type the username to confirm:');
  if (typed !== user.username) return;
  try {
    await api('DELETE', '/api/admin/users/' + user.id);
    feedback('admin-feedback', 'Deleted ' + user.username + '.');
  } catch (err) {
    feedback('admin-feedback', err.message, true);
  }
  loadUsers();
}

const createForm = document.getElementById('create-user-form');
if (createForm) {
  createForm.addEventListener('submit', async evt => {
    evt.preventDefault();
    const username = document.getElementById('new-username').value.trim();
    const password = document.getElementById('new-password').value;
    try {
      await api('POST', '/api/admin/users', { username, password });
      feedback('admin-feedback', 'Created ' + username + '.');
      createForm.reset();
    } catch (err) {
      feedback('admin-feedback', err.message, true);
    }
    loadUsers();
  });
  loadUsers();
}

// ── Account: own password ──
const passwordForm = document.getElementById('password-form');
if (passwordForm) {
  passwordForm.addEventListener('submit', async evt => {
    evt.preventDefault();
    const current = document.getElementById('current-password').value;
    const next = document.getElementById('new-password').value;
    if (next !== document.getElementById('confirm-password').value) {
      feedback('account-feedback', 'The new passwords do not match.', true);
      return;
    }
    try {
      await api('POST', '/auth/password', { current_password: current, new_password: next });
      feedback('account-feedback', 'Password changed.');
      passwordForm.reset();
    } catch (err) {
      feedback('account-feedback', err.message, true);
    }
  });
}
