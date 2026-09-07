const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
let statusData = null;

const api = async (path, opts = {}) => {
  const r = await fetch(path, {
    ...opts,
    headers: {"Content-Type": "application/json", ...(opts.headers || {})},
  });
  let data = null;
  try { data = await r.json(); } catch {}
  if (!r.ok) throw new Error(data?.detail || `HTTP ${r.status}`);
  return data;
};

function toast(text) {
  const el = $("#toast");
  el.textContent = text;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 3000);
}

function tab(name) {
  $$('nav button').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
  $$('.tab').forEach((s) => s.classList.toggle('active', s.id === `tab-${name}`));
  if (name === 'history') loadEvents();
  if (name === 'vehicles') loadVehicles();
  if (name === 'settings') loadSettingsTab();
}

$$('nav button').forEach((b) => b.onclick = () => tab(b.dataset.tab));

function image(id, url, available) {
  const el = $(id);
  if (available) {
    el.src = `${url}?t=${Date.now()}`;
    el.style.display = 'block';
  } else {
    el.removeAttribute('src');
    el.style.display = 'none';
  }
}

async function loadStatus() {
  statusData = await api('api/status');
  $('#version').textContent = `v${statusData.version}`;
  const r = statusData.last_result || {};
  $('#last-plate').textContent = r.plate || '—';
  const conf = r.confidence == null ? '' : ` · ${(Number(r.confidence) * 100).toFixed(1)}%`;
  $('#last-meta').textContent = r.error ? `Ошибка: ${r.error}` : (r.occurred_at || 'Распознаваний ещё не было') + conf;
  const badge = $('#access-badge');
  badge.className = 'access ' + (r.allowed ? 'good' : (r.plate ? 'bad' : 'neutral'));
  badge.textContent = r.allowed ? (r.gate_opened ? 'Доступ разрешён · ворота открыты' : 'Доступ разрешён') : (r.plate ? 'Доступ запрещён' : 'Нет данных');
  image('#sent-image', 'media/last_sent.jpg', statusData.images.sent);
  image('#result-image', 'media/last_result.jpg', statusData.images.result);
  renderDahuaStatus();
}

$('#recognize-btn').onclick = async () => {
  const b = $('#recognize-btn');
  b.disabled = true;
  b.textContent = 'Распознаю…';
  try {
    await api('api/recognize', {method: 'POST'});
    await loadStatus();
    toast('Распознавание завершено');
  } catch (e) {
    toast(e.message);
  } finally {
    b.disabled = false;
    b.textContent = 'Распознать сейчас';
  }
};

async function loadSettingsTab() {
  try {
    await loadStatus();
    await loadEntities();
  } catch (e) {
    toast(e.message);
  }
}

async function loadEntities() {
  const e = await api('api/ha/entities');
  const s = statusData?.settings || {};
  fillSelect('#camera-entity', e.cameras, s.camera_entity, 'Не выбрана');
  fillSelect('#trigger-entity', e.triggers, s.trigger_entity, 'Не выбран');
  fillSelect('#gate-entity', e.gates, s.gate_entity, 'Не открывать автоматически');
  $('#api-url').value = s.api_url || 'https://api-alpr.pirogovx.ru';
  $('#trigger-mode').value = s.trigger_mode || 'none';
  $('#dahua-url').value = s.dahua_url || '';
  $('#dahua-username').value = s.dahua_username || 'admin';
  $('#dahua-password').value = '';
  $('#dahua-password').placeholder = s.dahua_password_set ? 'Пароль сохранён. Оставьте пустым, чтобы не менять' : 'Введите пароль Dahua';
  $('#dahua-motion-cooldown').value = s.dahua_motion_cooldown ?? 10;
  $('#plate-type').value = s.plate_type || 'auto';
  $('#min-confidence').value = s.min_confidence ?? 0.85;
  $('#gate-cooldown').value = s.gate_cooldown ?? 30;
  $('#api-key').value = '';
  $('#api-key').placeholder = s.api_key_set ? 'Ключ сохранён. Оставьте пустым, чтобы не менять' : 'Введите API key';
  updateTriggerFields();
  renderDahuaStatus();
}

function fillSelect(sel, items, value, empty) {
  const el = $(sel);
  el.innerHTML = '';
  el.add(new Option(empty, ''));
  for (const i of items) el.add(new Option(`${i.name} (${i.entity_id})`, i.entity_id));
  el.value = value || '';
}

function updateTriggerFields() {
  const mode = $('#trigger-mode').value;
  $('#ha-trigger-row').classList.toggle('hidden', mode !== 'ha');
  $$('.dahua-trigger-row').forEach((el) => el.classList.toggle('hidden', mode !== 'dahua'));
}

$('#trigger-mode').onchange = updateTriggerFields;

function renderDahuaStatus() {
  const el = $('#dahua-status');
  if (!el || !statusData) return;
  const d = statusData.dahua || {};
  if (($('#trigger-mode')?.value || statusData.settings?.trigger_mode) !== 'dahua') {
    el.textContent = 'Dahua trigger выключен';
    return;
  }
  if (d.connected) {
    let text = `Dahua VideoMotion: подключено к ${d.camera_url || 'камере'}`;
    if (d.last_event_at) text += ` · последнее событие ${d.last_action || ''} ${d.last_event_at}`;
    el.textContent = text;
  } else {
    el.textContent = `Dahua VideoMotion: ${d.last_error || 'подключение...'}`;
  }
}

$('#settings-form').onsubmit = async (e) => {
  e.preventDefault();
  const body = {
    api_url: $('#api-url').value,
    api_key: $('#api-key').value,
    camera_entity: $('#camera-entity').value,
    trigger_mode: $('#trigger-mode').value,
    trigger_entity: $('#trigger-entity').value,
    dahua_url: $('#dahua-url').value,
    dahua_username: $('#dahua-username').value,
    dahua_password: $('#dahua-password').value,
    dahua_motion_cooldown: Number($('#dahua-motion-cooldown').value),
    plate_type: $('#plate-type').value,
    gate_entity: $('#gate-entity').value,
    min_confidence: Number($('#min-confidence').value),
    gate_cooldown: Number($('#gate-cooldown').value),
  };
  try {
    const saved = await api('api/settings', {method: 'PUT', body: JSON.stringify(body)});
    statusData.settings = saved;
    $('#api-key').value = '';
    $('#dahua-password').value = '';
    $('#settings-state').textContent = 'Сохранено';
    await new Promise((resolve) => setTimeout(resolve, 500));
    await loadStatus();
    updateTriggerFields();
    toast('Настройки сохранены');
  } catch (err) {
    toast(err.message);
  }
};

async function loadVehicles() {
  const rows = await api('api/vehicles');
  const box = $('#vehicles-list');
  box.innerHTML = rows.length ? '' : '<div class="muted">Список пока пуст</div>';
  for (const v of rows) {
    const d = document.createElement('div');
    d.className = 'list-item';
    d.innerHTML = `<div><div class="list-title">${esc(v.plate)} · ${esc(v.name || 'Без имени')}</div><div class="list-meta">${v.enabled ? 'Активен' : 'Отключён'} · ${v.open_gate ? 'Открывать ворота' : 'Не открывать ворота'}${v.note ? ' · ' + esc(v.note) : ''}</div></div><button class="small">Удалить</button>`;
    d.querySelector('button').onclick = async () => {
      if (confirm(`Удалить ${v.plate}?`)) {
        await api(`api/vehicles/${v.id}`, {method: 'DELETE'});
        loadVehicles();
      }
    };
    box.appendChild(d);
  }
}

$('#add-vehicle').onclick = () => {
  $('#vehicle-form').reset();
  $('#vehicle-enabled').checked = true;
  $('#vehicle-open-gate').checked = true;
  $('#vehicle-dialog').showModal();
};
$('#vehicle-cancel').onclick = () => $('#vehicle-dialog').close();
$('#vehicle-form').onsubmit = async (e) => {
  e.preventDefault();
  try {
    await api('api/vehicles', {
      method: 'POST',
      body: JSON.stringify({
        plate: $('#vehicle-plate').value,
        name: $('#vehicle-name').value,
        note: $('#vehicle-note').value,
        enabled: $('#vehicle-enabled').checked,
        open_gate: $('#vehicle-open-gate').checked,
      }),
    });
    $('#vehicle-dialog').close();
    loadVehicles();
    toast('Автомобиль сохранён');
  } catch (err) {
    toast(err.message);
  }
};

async function loadEvents() {
  const rows = await api('api/events?limit=100');
  const box = $('#events-list');
  box.innerHTML = rows.length ? '' : '<div class="muted">История пока пустая</div>';
  for (const r of rows) {
    const d = document.createElement('div');
    d.className = 'list-item';
    d.innerHTML = `<div><div class="list-title">${esc(r.plate || 'Номер не найден')}</div><div class="list-meta">${esc(r.occurred_at)}${r.confidence != null ? ' · ' + (Number(r.confidence) * 100).toFixed(1) + '%' : ''} · ${r.error ? 'Ошибка: ' + esc(r.error) : (r.allowed ? (r.gate_opened ? 'Разрешён · ворота открыты' : 'Разрешён') : 'Отказ')}</div></div>`;
    box.appendChild(d);
  }
}

function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
}

(async () => {
  try {
    await loadStatus();
    await loadVehicles();
  } catch (e) {
    toast(e.message);
  }
})();
