// Admin (user management) and account (own password, sessions) pages. Needs common.js.

const api = (method, path, payload) => sendJson(method, path, payload);

// ── Admin: users ──
async function loadUsers() {
  const host = document.getElementById('users-list');
  if (!host) return;
  let users;
  try {
    users = (await api('GET', '/api/admin/users')).users;
  } catch (err) {
    replaceChildren(host, hint(err.message, true));
    return;
  }
  const rows = users.map(user => {
    const isUser = user.role === 'user';
    const actions = isUser ? menuButton([
      { icon: 'key', label: 'Reset password', onclick: () => resetPassword(user) },
      { icon: 'sign-out-alt', label: 'Sign out everywhere', onclick: () => signOutEverywhere(user) },
      { icon: 'trash-alt', label: 'Delete user', danger: true, onclick: () => deleteUser(user) }
    ], { title: 'Actions for ' + user.username }) : null;
    return el('tr', {}, [
      el('td', { className: 'user-cell' }, el('div', {}, [
        faIcon('user-circle', 'user-avatar'),
        el('strong', { text: user.username }),
        user.self ? el('span', { className: 'badge', text: 'you' }) : null
      ])),
      el('td', { 'data-label': 'Role' }, el('span', { className: 'badge' + (isUser ? '' : ' accent'), text: isUser ? 'User' : 'Admin' })),
      el('td', { 'data-label': 'Created', text: formatDate(user.created_unix) }),
      el('td', { 'data-label': 'Signed in', text: user.sessions ? plural(user.sessions, 'session') : 'No sessions' }),
      el('td', { className: 'actions-cell' }, actions)
    ]);
  });
  replaceChildren(host, el('table', { className: 'data-table users-table' }, [
    el('thead', {}, el('tr', {}, ['User', 'Role', 'Created', 'Signed in', ''].map(h => el('th', { text: h })))),
    el('tbody', {}, rows)
  ]));
}

function passwordInput(id, autocomplete) {
  return el('input', { id, type: 'password', minlength: '8', required: true, autocomplete: autocomplete || 'new-password' });
}

async function newUser() {
  const username = el('input', { id: 'new-username', required: true, pattern: '[A-Za-z0-9_\\-]{1,64}', autocomplete: 'off', spellcheck: 'false' });
  const password = passwordInput('new-password');
  const created = await openDialog({
    title: 'New user',
    body: [
      field('Username', username, 'Letters, digits, _ and -.'),
      field('Password', password, 'At least 8 characters. Share it with them; they can change it on their account page.')
    ],
    confirmLabel: 'Create user',
    onConfirm: async () => {
      await api('POST', '/api/admin/users', { username: username.value.trim(), password: password.value });
      return username.value.trim();
    }
  });
  if (created) setFeedback('admin-feedback', 'Created ' + created + '.');
  loadUsers();
}

async function resetPassword(user) {
  const password = passwordInput('reset-password');
  const ok = await openDialog({
    title: 'Reset password of ' + user.username,
    body: [
      field('New password', password, 'At least 8 characters.'),
      el('p', { className: 'hint', text: 'This also signs ' + user.username + ' out everywhere.' })
    ],
    confirmLabel: 'Reset password',
    onConfirm: () => api('POST', '/api/admin/users/' + user.id + '/password', { password: password.value })
  });
  if (ok) setFeedback('admin-feedback', 'Changed the password of ' + user.username + ' and signed them out everywhere.');
  loadUsers();
}

async function signOutEverywhere(user) {
  const result = await openDialog({
    title: 'Sign ' + user.username + ' out everywhere?',
    body: [el('p', { text: 'Every browser and device where ' + user.username + ' is signed in has to sign in again. ' +
      'Their password stays the same.' })],
    confirmLabel: 'Sign out everywhere',
    danger: true,
    onConfirm: () => api('POST', '/api/admin/users/' + user.id + '/sessions/revoke')
  });
  if (result) setFeedback('admin-feedback', 'Signed ' + user.username + ' out (' + plural(result.ended, 'session') + ' ended).');
  loadUsers();
}

async function deleteUser(user) {
  const typed = el('input', { autocomplete: 'off', spellcheck: 'false', required: true });
  const ok = await openDialog({
    title: 'Delete ' + user.username + '?',
    body: [
      el('p', { text: 'This permanently deletes ' + user.username + ', including their key files, devices and location history. It cannot be undone.' }),
      field('Type the username to confirm', typed)
    ],
    confirmLabel: 'Delete user',
    danger: true,
    onConfirm: async () => {
      if (typed.value !== user.username) throw new Error('The username does not match.');
      await api('DELETE', '/api/admin/users/' + user.id);
    }
  });
  if (ok) setFeedback('admin-feedback', 'Deleted ' + user.username + '.');
  loadUsers();
}

// ── Account: own password and sessions ──
async function loadSessions() {
  const summary = document.getElementById('sessions-summary');
  const button = document.getElementById('revoke-others-btn');
  if (!summary) return;
  try {
    const { sessions } = await api('GET', '/auth/sessions');
    const others = Math.max(0, sessions - 1);
    summary.textContent = others
      ? 'You are signed in here and in ' + plural(others, 'other browser or device', 'other browsers or devices') + '.'
      : 'You are only signed in here.';
    button.hidden = others === 0;
  } catch (err) {
    summary.textContent = err.message;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const newUserBtn = document.getElementById('new-user-btn');
  if (newUserBtn) {
    newUserBtn.addEventListener('click', newUser);
    loadUsers();
  }

  const passwordForm = document.getElementById('password-form');
  if (passwordForm) {
    const submit = passwordForm.querySelector('button[type=submit]');
    passwordForm.addEventListener('submit', async evt => {
      evt.preventDefault();
      const current = document.getElementById('current-password').value;
      const next = document.getElementById('new-password').value;
      if (next !== document.getElementById('confirm-password').value) {
        setFeedback('account-feedback', 'The new passwords do not match.', true);
        return;
      }
      await withLoading(submit, async () => {
        try {
          await api('POST', '/auth/password', { current_password: current, new_password: next });
          setFeedback('account-feedback', 'Password changed.');
          passwordForm.reset();
        } catch (err) {
          setFeedback('account-feedback', err.message, true);
        }
      })();
      loadSessions();
    });
  }

  const revoke = document.getElementById('revoke-others-btn');
  if (revoke) {
    revoke.addEventListener('click', withLoading(revoke, async () => {
      try {
        const { ended } = await api('POST', '/auth/sessions/revoke-others');
        setFeedback('sessions-feedback', 'Signed out ' + plural(ended, 'other session') + '.');
      } catch (err) {
        setFeedback('sessions-feedback', err.message, true);
      }
      await loadSessions();
    }));
    loadSessions();
  }
});
