const $ = selector => document.querySelector(selector);
const run = document.body.dataset.run;
const feedback = $('#release-feedback');
const integer = new Intl.NumberFormat('zh-CN');
const CHECK_LABELS = {windows: '同站同向窗口重叠', offsets: '抵掉超过放行数', stations: '站点不在本批闸机记录',
  net_split: '净补拆分不平', accounted: '批次记录缺失'};
let busy = false;

function shanghaiIso(value) {
  if (!value) return '';
  return `${value.length === 16 ? `${value}:00` : value}+08:00`;
}

async function post(url, body) {
  const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(result.message || '操作失败，请重试。');
    error.detail = result.detail;
    throw error;
  }
  return result;
}

function say(text, failed = false) {
  feedback.textContent = text;
  feedback.classList.toggle('error', failed);
}

function changed(text) {
  say(text);
  document.dispatchEvent(new CustomEvent('release-changed'));
  refresh();
}

async function refresh() {
  try {
    const response = await fetch(`/api/releases/${encodeURIComponent(run)}?view=main&limit=1`);
    const data = await response.json();
    $('#release-released').textContent = integer.format(data.totals.released);
    $('#release-offset').textContent = integer.format(data.totals.offset);
    $('#release-pending').textContent = integer.format(data.totals.pending);
    $('#release-verified').textContent = integer.format(data.totals.verified);
    const state = $('#release-state');
    const readonly = data.readonly;
    if (readonly) state.textContent = '样例只读';
    else if (!data.published) state.textContent = '未发布';
    else if (data.dirty) state.textContent = `已发布 ${data.published.ledger_id} · 有未发布变更`;
    else state.textContent = `已发布 ${data.published.ledger_id}`;
    state.className = `signal ${readonly || !data.published || data.dirty ? 'idle' : 'pass'}`;
    document.querySelectorAll('#release-ops button').forEach(button => { button.disabled = readonly; });
  } catch (error) {
    say('补偿账读取失败', true);
  }
}

function wireDialog(id, open, close, form, collect, url, done) {
  const dialog = $(`#${id}`);
  $(`#${open}`).onclick = () => dialog.showModal();
  $(`#${close}`).onclick = () => dialog.close();
  const output = dialog.querySelector('output');
  form.onsubmit = async event => {
    event.preventDefault();
    if (busy) return;
    busy = true;
    output.textContent = '提交中…';
    output.classList.remove('error');
    try {
      const result = await post(typeof url === 'function' ? url(collect(form)) : url, collect(form));
      dialog.close();
      form.reset();
      changed(done(result));
    } catch (error) {
      output.textContent = error.message;
      output.classList.add('error');
    } finally {
      busy = false;
    }
  };
}

wireDialog('release-enter', 'release-enter-open', 'release-enter-close', $('#release-enter-form'),
  form => ({batch: form.batch.value.trim(), station: form.station.value, direction: form.direction.value,
    start: shanghaiIso(form.start.value), end: shanghaiIso(form.end.value),
    released: Number(form.released.value), entered_by: form.entered_by.value.trim(), note: form.note.value.trim()}),
  `/api/releases/${encodeURIComponent(run)}/batches`,
  result => `批次 ${result.batch} v${result.version}${result.reused ? ' 同内容复用，未重复累计' : ' 已补录，待核对'}`);

wireDialog('release-verify', 'release-verify-open', 'release-verify-close', $('#release-verify-form'),
  form => ({verifier: form.verifier.value.trim(), note: form.note.value.trim()}),
  () => `/api/releases/${encodeURIComponent(run)}/batches/${encodeURIComponent($('#release-verify-batch').value.trim())}/verify`,
  result => `批次 ${result.batch} v${result.version}${result.reused ? ' 此前已核对' : ' 已核对，计入发布口径'}`);

wireDialog('release-void', 'release-void-open', 'release-void-close', $('#release-void-form'),
  form => ({note: form.note.value.trim()}),
  () => `/api/releases/${encodeURIComponent(run)}/batches/${encodeURIComponent($('#release-void-batch').value.trim())}/void`,
  result => `批次 ${result.batch} v${result.version}${result.reused ? ' 此前已作废' : ' 已作废，记录保留'}`);

$('#release-publish').onclick = async () => {
  if (busy) return;
  busy = true;
  say('正在勾稽并发布…');
  try {
    const result = await post(`/api/releases/${encodeURIComponent(run)}/publish`);
    changed(result.reused ? `补偿账 ${result.ledger_id} 已是最新发布` : `补偿账 ${result.ledger_id} 已发布`);
  } catch (error) {
    const failed = error.detail ? Object.entries(error.detail).filter(([, ok]) => !ok).map(([name]) => CHECK_LABELS[name] || name) : [];
    say(`${error.message}${failed.length ? `：${failed.join('、')}` : ''}`, true);
    refresh();
  } finally {
    busy = false;
  }
};

refresh();
