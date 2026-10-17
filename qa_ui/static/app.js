/* qa-ui (docs/qa-ui.md): the page. No build step, no library, no external address.
   Every text that comes from the workspace is set as text, never as HTML. */
'use strict';

const MODULES = ['functional-web', 'exploratory', 'api', 'accessibility', 'mobile'];
const FLAG_TEXT = {
  'no-expected': 'no expected result: the run will mark it Question',
  'vague-expected': 'the expected result names no observable outcome',
  'several-expected': 'several expected results: split the case',
  'duplicate-id': 'the same ID came more than once: the first one is kept',
  'not-in-library': 'not in the library: add it before planning',
};

const S = {
  token: '', info: null, screen: 'intake', busy: '',
  cases: [], notes: [], dropped: [], library: null, analysed: false,
  moved: {}, sug: null, ticked: new Set(), plan: null,
  reading: null, asking: false, wantProposals: false,
  newIds: {}, newTicked: new Set(), casesDry: null, casesDone: null,
  runId: null, run: null, logName: null, log: null, applyOut: null, timer: null,
  environment: '', build: '', editing: null,
};

/* ------------------------------------------------------------------ small helpers */

function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === false || value === null || value === undefined) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, '');
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

const $ = (id) => document.getElementById(id);
const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + 's')}`;

function banner(message) {
  const el = $('banner');
  el.textContent = message || '';
  el.hidden = !message;
}

async function api(method, path, body) {
  const options = { method, headers: { 'X-QA-Token': S.token } };
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (e) {
    throw new Error('The interface does not answer: is `python -m qa_ui` still running?');
  }
  let data = {};
  try { data = await response.json(); } catch (e) { /* an empty or non-JSON answer: the status says it */ }
  if (!response.ok) throw new Error(data.error || `The request failed (${response.status}).`);
  return data;
}

/* Runs an action with the busy text shown and the error, if any, in the banner. */
async function act(label, fn) {
  if (S.busy) return;
  S.busy = label;
  banner('');
  render();
  try {
    await fn();
  } catch (e) {
    banner(e.message);
  } finally {
    S.busy = '';
    render();
  }
}

function statusChip(status) {
  const s = String(status || '').toLowerCase();
  const kind = s.startsWith('question') ? 'ask' : s === 'pass' ? 'ok' : s === 'fail' ? 'fail'
    : s === 'blocked' ? 'warn' : s === 'running' ? 'accent' : '';
  return h('span', { class: `chip ${kind}`.trim(), text: status });
}

function field(label, control, id) {
  control.id = id;
  return h('div', { class: 'field' }, h('label', { for: id, text: label }), control);
}

function output(text) {
  return h('pre', { class: 'out', tabindex: '0', text: text || '(no output)' });
}

/* ------------------------------------------------------------------ data */

async function refreshState(force) {
  S.info = await api('GET', '/api/state' + (force ? '?refresh=1' : ''));
  if (!S.environment) S.environment = S.info.test_environment || S.info.project.environments[0] || '';
  if (S.info.run && !S.runId) openRun(S.info.run.id);
}

function assignmentsForPlan() {
  const out = {};
  for (const [id, a] of Object.entries(S.sug ? S.sug.assignments : {})) out[id] = { module: a.module };
  return out;
}

async function refreshSuggestion(resetTicks) {
  S.sug = await api('POST', '/api/suggest', { cases: S.cases, notes: S.notes, moved: S.moved });
  if (resetTicks) {
    S.ticked = new Set(S.sug.cards.filter((c) => c.level === 'recommended').map((c) => c.key));
  }
  await refreshPlan();
}

async function refreshPlan(save) {
  S.plan = await api('POST', '/api/plan', {
    cases: S.cases, assignments: assignmentsForPlan(), cards: [...S.ticked], save: !!save,
  });
}

function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('The file could not be read.'));
    reader.onload = () => resolve({ name: file.name, data: String(reader.result).split(',')[1] || '' });
    reader.readAsDataURL(file);
  });
}

async function analyse() {
  const text = $('in-text').value;
  const picked = $('in-file').files[0];
  const body = { library: $('in-library').checked };
  if (text.trim()) body.text = text;
  if (picked) body.file = await readFile(picked);
  const got = await api('POST', '/api/intake', body);
  Object.assign(S, {
    cases: got.cases, notes: got.notes, dropped: got.dropped, library: got.library, analysed: true,
    moved: {}, reading: null, newIds: {}, newTicked: new Set(), casesDry: null, casesDone: null,
  });
  if (!S.cases.length) {
    S.sug = null;
    S.plan = null;
    throw new Error(`No case was found${S.notes.length ? ` (${plural(S.notes.length, 'line')} kept as notes)` : ''}: `
      + 'a case is a line that starts with an ID, e.g. "CHK-013 Apply an expired coupon → the coupon is refused".');
  }
  await refreshSuggestion(true);
  go('suggest');
}

/* ------------------------------------------------------------------ runs and run logs */

function stopPolling() {
  if (S.timer) clearInterval(S.timer);
  S.timer = null;
}

async function pollRun() {
  if (!S.runId) return;
  try {
    S.run = await api('GET', `/api/runs/${encodeURIComponent(S.runId)}`);
  } catch (e) {
    stopPolling();
    banner(e.message);
    return;
  }
  try {
    if (S.run.state !== 'running') {
      stopPolling();
      if (S.run.batch) await openLog(S.run.batch, true);
      await refreshState();
    } else if (S.run.batch && !S.logName) {
      await openLog(S.run.batch, true);          // once: the planned cases of the batch
    } else if (S.run.run_log && !S.run.run_log.error) {
      S.log = Object.assign({}, S.log, S.run.run_log);
    }
  } catch (e) { banner(e.message); }
  renderFromPoll();
}

function openRun(id) {
  stopPolling();
  S.runId = id;
  S.run = null;
  S.log = null;
  S.logName = null;
  S.applyOut = null;
  S.editing = null;
  S.timer = setInterval(pollRun, 2000);
  pollRun();
}

async function openLog(name, keepRun) {
  if (!keepRun) {
    stopPolling();
    S.runId = null;
    S.run = null;
    S.applyOut = null;
  }
  S.logName = name;
  S.editing = null;
  S.log = await api('GET', `/api/runlogs/${encodeURIComponent(name)}`);
}

async function startBatch(name) {
  const run = await api('POST', '/api/runs', { batch: name, environment: S.environment, build: S.build });
  await refreshState();
  go('run');
  openRun(run.id);
}

async function startAudit(kind) {
  const run = await api('POST', '/api/runs', { audit: kind });
  await refreshState();
  go('run');
  openRun(run.id);
}

async function editItem(tc, body) {
  const name = S.logName || (S.run && S.run.batch);
  await api('POST', `/api/runlogs/${encodeURIComponent(name)}/items/${encodeURIComponent(tc)}`, body);
  S.applyOut = null;
  await openLog(name, true);
  await refreshState();
}

async function applyLog(confirmIt) {
  const name = S.logName || (S.run && S.run.batch);
  S.applyOut = await api('POST', `/api/runlogs/${encodeURIComponent(name)}/apply`, { confirm: !!confirmIt });
  if (confirmIt) {
    await openLog(name, true);
    await refreshState();
  }
}

/* ------------------------------------------------------------------ navigation */

function go(screen) {
  S.screen = screen;
  render();
  const heading = document.querySelector(`#screen-${screen} h1`);
  if (heading) {
    heading.setAttribute('tabindex', '-1');
    heading.focus();
  }
  window.scrollTo(0, 0);
}

function renderTop() {
  const info = S.info;
  $('project').textContent = info ? `${info.project.name || 'workspace'} · ${info.project.tracker || 'no tracker'}` : '';
  for (const button of document.querySelectorAll('.step')) {
    const screen = button.dataset.screen;
    if (screen === S.screen) button.setAttribute('aria-current', 'step');
    else button.removeAttribute('aria-current');
    button.disabled = screen === 'suggest' && !S.sug;
  }
  const pill = $('status');
  if (S.busy) {
    pill.className = 'pill busy';
    pill.textContent = S.busy;
  } else if (info && info.run) {
    pill.className = 'pill busy';
    pill.textContent = `Running: ${info.run.label}`;
  } else if (info && !info.claude) {
    pill.className = 'pill warn';
    pill.textContent = 'Claude Code not found';
  } else {
    pill.className = 'pill ok';
    pill.textContent = info ? 'Ready' : 'Connecting…';
  }
}

/* ------------------------------------------------------------------ screen 1: intake */

function projectPanel() {
  const info = S.info;
  if (!info) return null;
  const missing = info.checks.filter((c) => !c.ok);
  return h('section', { class: 'panel' },
    h('h2', { text: 'Project' }),
    h('dl', { class: 'kv' },
      h('dt', { text: 'Name' }), h('dd', { text: info.project.name || '(not set)' }),
      h('dt', { text: 'Tracker' }), h('dd', { text: info.project.tracker || '(not set)' }),
      h('dt', { text: 'Modules' }), h('dd', {}, h('div', { class: 'chips' },
        info.project.modules.map((m) => h('span', { class: 'chip accent', text: m })))),
      h('dt', { text: 'Environments' }), h('dd', { text: info.project.environments.join(', ') || '(none with a URL)' }),
      h('dt', { text: 'Claude Code' }), h('dd', { text: info.claude ? 'found' : 'not found: batches cannot start' })),
    h('h2', { text: 'This computer' }),
    h('ul', { class: 'plain' }, info.checks.map((c) => h('li', { class: `checkline ${c.ok ? 'ok' : 'missing'}` },
      h('span', { class: 'mark', text: c.ok ? 'ok' : 'missing' }),
      h('span', {}, h('strong', { text: c.tool }), c.ok ? '' : ` — ${c.detail}`)))),
    h('div', { class: 'row' },
      h('button', { type: 'button', class: 'btn small', disabled: !!S.busy,
        onclick: () => act('Checking this computer…', () => refreshState(true)) }, 'Check again'),
      h('span', { class: 'note', text: missing.length ? `${plural(missing.length, 'check')} missing` : 'Everything found.' })));
}

function listsPanel() {
  const info = S.info;
  if (!info) return null;
  const pending = info.batches.filter((b) => b.valid && !b.run_log);
  const envSelect = h('select', { onchange: (e) => { S.environment = e.target.value; } },
    h('option', { value: '', text: '(not given)' }),
    info.project.environments.map((k) => h('option', { value: k, selected: k === S.environment, text: k })));
  const buildInput = h('input', { type: 'text', value: S.build, placeholder: 'e.g. 2.4.1', oninput: (e) => { S.build = e.target.value; } });
  return h('section', { class: 'panel' },
    h('h2', { text: 'Planned batches' }),
    pending.length ? h('div', { class: 'row' },
      h('div', { class: 'grow' }, field('Environment', envSelect, `env-${S.screen}`)),
      h('div', { class: 'grow' }, field('Build tested', buildInput, `build-${S.screen}`))) : null,
    pending.length ? h('ul', { class: 'plain' }, pending.slice(0, 8).map((b) => h('li', { class: 'item' },
      h('span', {}, h('span', { class: 'mono', text: b.name }), ' ',
        h('span', { class: 'muted small', text: `${b.module || 'no module'} · ${plural(b.cases.length, 'case')}` })),
      b.module ? h('button', { type: 'button', class: 'btn small', disabled: !!S.busy || !!info.run,
        onclick: () => act('Starting the batch…', () => startBatch(b.name)) }, 'Start')
        : h('select', { 'aria-label': `Module of ${b.name}`, disabled: !!S.busy, onchange: (e) => {
          const module = e.target.value;
          if (!module) return;
          act('Setting the module…', async () => {
            await api('POST', `/api/batches/${encodeURIComponent(b.name)}/module`, { module });
            await refreshState();
          });
        } }, h('option', { value: '', text: 'Choose its module…' }),
        info.project.modules.map((m) => h('option', { value: m, text: m }))))))
      : h('p', { class: 'note', text: 'None waiting to run.' }),
    h('h2', { text: 'Run logs' }),
    info.run_logs.length ? h('ul', { class: 'plain' }, info.run_logs.slice(0, 8).map((r) => h('li', { class: 'item' },
      h('span', {}, h('span', { class: 'mono', text: r.name }), ' ',
        h('span', { class: 'muted small', text: r.error ? 'cannot be read'
          : `${plural(r.total, 'result')}${r.needs ? ` · ${r.needs} need you` : ''}` })),
      h('button', { type: 'button', class: 'btn small', disabled: !!S.busy,
        onclick: () => act('Opening the run log…', async () => { await openLog(r.batch); go('run'); }) }, 'Open'))))
      : h('p', { class: 'note', text: 'No run log yet.' }));
}

function libraryWarning() {
  if (!S.library || !S.library.warning) return null;
  return h('ul', { class: 'problems', role: 'status' }, h('li', { text: `Library: ${S.library.warning}` }));
}

function understoodPanel() {
  if (!S.analysed) return null;
  const areas = {};
  for (const c of S.cases) areas[c.area || '(no area)'] = (areas[c.area || '(no area)'] || 0) + 1;
  const flagged = S.cases.filter((c) => c.flags.length);
  return h('section', { class: 'panel' },
    h('h2', { text: 'What was understood' }),
    h('p', {}, h('span', { class: 'big', text: String(S.cases.length) }), ' ',
      h('span', { class: 'muted', text: S.cases.length === 1 ? 'case' : 'cases' })),
    h('div', { class: 'chips' }, Object.entries(areas).map(([a, n]) => h('span', { class: 'chip accent', text: `${a} · ${n}` }))),
    h('dl', { class: 'kv' },
      h('dt', { text: 'Notes' }), h('dd', { text: plural(S.notes.length, 'line') + ' kept as context' }),
      h('dt', { text: 'Flagged' }), h('dd', { text: plural(flagged.length, 'case') }),
      h('dt', { text: 'Library' }), h('dd', { text: S.library ? (S.library.error || S.library.detail) : '' })),
    libraryWarning());
}

function renderIntake() {
  const root = $('screen-intake');
  const keep = { text: $('in-text') ? $('in-text').value : '', library: $('in-library') ? $('in-library').checked : false };
  const fileInput = $('in-file') || h('input', { type: 'file', accept: '.csv,.tsv,.txt,.md,.xlsx,.docx' });
  const textarea = h('textarea', { class: 'cases', spellcheck: 'false',
    placeholder: 'CHK-011  Pay with a valid card → order created\nCHK-013  Apply an expired coupon → the coupon is refused' });
  textarea.value = keep.text;
  const lib = h('input', { type: 'checkbox', id: 'in-library' });
  lib.checked = keep.library;
  const libraryCount = S.library && !S.library.error ? ` (${S.library.count} cases)` : '';
  root.replaceChildren(
    h('h1', { id: 'h-intake', text: 'What do you want to test?' }),
    h('p', { class: 'lead', text: 'Paste test cases or notes, choose a file, or load the cases the project already has. '
      + 'The rules suggest a type of test for each case and say why. Nothing runs without your approval.' }),
    h('div', { class: 'cols' },
      h('div', { class: 'col-main' },
        h('section', { class: 'panel flush' },
          h('div', { class: 'panel-body' },
            field('Test cases, requirements or notes', textarea, 'in-text'),
            h('p', { class: 'note', text: 'One case per line: an ID, the title, then → and the expected result. '
              + 'Cells separated by a tab or | also work, with an optional header line. A line without an ID is kept as a note.' }),
            field('Or a file (.csv, .tsv, .txt, .md, .xlsx, .docx; up to 5 MB, read in memory and never stored)', fileInput, 'in-file'),
            h('label', { class: 'check', for: 'in-library' }, lib,
              h('span', { text: `Include the project's library${libraryCount}: the cases the workbook or the tracker already has` }))),
          h('div', { class: 'panel-foot' },
            h('p', { class: 'note', text: 'A new case is a proposal until you approve it.' }),
            h('div', { class: 'row' },
              h('button', { type: 'button', class: 'btn', disabled: !!S.busy, onclick: () => {
                $('in-text').value = '';
                $('in-file').value = '';
                $('in-library').checked = false;
              } }, 'Clear'),
              h('button', { type: 'button', class: 'btn primary', disabled: !!S.busy,
                onclick: () => act('Reading the cases…', analyse) }, 'Analyse and suggest tests'))))),
      h('div', { class: 'col-side' }, understoodPanel(), listsPanel(), projectPanel())));
}

/* ------------------------------------------------------------------ screen 2: suggestion */

function caseById(id) {
  return S.cases.find((c) => c.id === id);
}

function levelChip(level) {
  if (level === 'recommended') return h('span', { class: 'chip accent', text: 'Recommended' });
  if (level === 'optional') return h('span', { class: 'chip', text: 'Optional' });
  return h('span', { class: 'chip outline', text: 'Not suggested' });
}

function caseLine(id) {
  const c = caseById(id) || { id, title: '', flags: [] };
  const a = S.sug.assignments[id];
  const select = h('select', { 'aria-label': `Type of test of ${id}`, disabled: !!S.busy, onchange: (e) => {
    S.moved[id] = e.target.value;
    act('Applying the rules…', () => refreshSuggestion(false));
  } }, MODULES.map((m) => h('option', { value: m, selected: m === a.module, text: m })));
  return h('div', { class: 'caseline' },
    h('span', { class: 'mono', text: id }),
    h('span', { text: c.title }),
    select,
    h('span', { class: 'why', text: `Why: ${a.why}${c.flags.length ? ' · ' + c.flags.map((f) => FLAG_TEXT[f] || f).join('; ') : ''}` }));
}

function suggestionCard(card) {
  const box = h('input', { type: 'checkbox', 'aria-label': card.title, disabled: !!S.busy, onchange: (e) => {
    if (e.target.checked) S.ticked.add(card.key); else S.ticked.delete(card.key);
    act('Planning…', () => refreshPlan());
  } });
  box.checked = S.ticked.has(card.key);
  const kind = card.key === 'audit-a11y' ? 'a11y' : 'site';
  return h('article', { class: `panel card${card.level === 'not-suggested' ? ' dim' : ''}` },
    h('div', {}, box),
    h('div', { class: 'card-body' },
      h('div', { class: 'row' }, h('h2', { class: 'card-title', text: card.title }), levelChip(card.level),
        card.kind === 'module' && !card.active ? h('span', { class: 'chip warn', text: 'Not active in project.yaml' }) : null),
      h('div', { class: 'mono muted', text: `${card.key} · ${card.runs}` }),
      h('p', { class: 'small', text: card.reason }),
      h('div', { class: 'small muted', text: `Tool: ${card.tool}` }),
      card.blockers.length
        ? h('div', {}, h('div', { class: 'small', text: 'Cannot start yet:' }),
          h('ul', { class: 'blockers' }, card.blockers.map((b) => h('li', { text: b }))))
        : h('div', { class: 'small', text: card.kind === 'audit' ? 'Ready to start.' : 'Ready on this computer.' }),
      card.cases.length ? h('details', {}, h('summary', { text: `${plural(card.cases.length, 'case')}: see and move` }),
        h('div', { class: 'caselist' }, card.cases.map(caseLine))) : null,
      card.kind === 'audit' && S.ticked.has(card.key) ? h('div', {},
        h('button', { type: 'button', class: 'btn small', disabled: !!S.busy || !card.ready || !!(S.info && S.info.run),
          onclick: () => act('Starting the audit…', () => startAudit(kind)) }, 'Start this audit now')) : null));
}

function planPanel() {
  const plan = S.plan;
  if (!plan) return null;
  return h('section', { class: 'panel' },
    h('h2', { text: 'Batch plan' }),
    plan.batches.length ? h('ol', { class: 'plain' }, plan.batches.map((b, i) => h('li', { class: 'batch' },
      h('span', { class: 'n', text: String(i + 1) }),
      h('div', { class: 'stack' },
        h('strong', { text: `${b.area}${b.role ? ' · ' + b.role : ''} · ${b.module}` }),
        h('span', { class: 'mono small', text: b.cases.join(' → ') }),
        h('span', { class: 'note', text: `${b.name} · ${b.skill}`
          + (b.questions.length ? ` · ${b.questions.join(', ')} will be marked Question` : '') })))))
      : h('p', { class: 'note', text: 'Nothing to run yet: tick a type of test that has cases.' }),
    plan.warnings.map((w) => h('p', { class: 'note', text: `Warning: ${w}` })),
    h('p', { class: 'note', text: `${plural(plan.selected, 'case')} selected · ${plan.planned} planned · ${plan.apart.length} listed apart` }));
}

function newCasesPanel() {
  const fresh = S.cases.filter((c) => c.in_library === false);
  if (!fresh.length) return null;
  const chosen = fresh.filter((c) => S.newTicked.has(c.id));
  const dry = S.casesDry;
  return h('section', { class: 'panel' },
    h('h2', { text: 'New cases' }),
    h('p', { class: 'note', text: 'Not in the library. A case enters through the project\'s adapter, after you approve it; '
      + 'give each one its final ID in the project\'s pattern.' }),
    h('ul', { class: 'plain' }, fresh.map((c) => {
      const box = h('input', { type: 'checkbox', 'aria-label': `Add ${c.id}`, onchange: (e) => {
        if (e.target.checked) S.newTicked.add(c.id); else S.newTicked.delete(c.id);
        S.casesDry = null;
        render();
      } });
      box.checked = S.newTicked.has(c.id);
      const idInput = h('input', { type: 'text', 'aria-label': `Final ID of ${c.id}`, class: 'mono',
        value: S.newIds[c.id] !== undefined ? S.newIds[c.id] : c.id,
        oninput: (e) => { S.newIds[c.id] = e.target.value; },
        onchange: () => { S.casesDry = null; render(); } });
      return h('li', { class: 'stack' },
        h('div', { class: 'row' }, box, h('div', { class: 'grow' }, idInput)),
        h('span', { class: 'small', text: c.title }),
        c.why ? h('span', { class: 'note', text: `Claude: ${c.why}` }) : null);
    })),
    h('div', { class: 'row' },
      h('button', { type: 'button', class: 'btn small', disabled: !!S.busy || !chosen.length,
        onclick: () => act('Checking with the adapter…', async () => {
          S.casesDone = null;
          S.casesDry = await api('POST', '/api/cases', {
            cases: chosen.map((c) => Object.assign({}, c, { id: (S.newIds[c.id] !== undefined ? S.newIds[c.id] : c.id).trim() })),
          });
          S.casesDry.ids = chosen.map((c) => [c.id, (S.newIds[c.id] !== undefined ? S.newIds[c.id] : c.id).trim()]);
        }) }, `Check ${plural(chosen.length, 'case')} with the adapter (dry run)`)),
    dry ? h('div', { class: 'stack' },
      h('p', { class: 'small', text: `Dry run: ${dry.meaning} (exit code ${dry.code}).` }),
      output(dry.output),
      dry.can_confirm ? h('div', { class: 'row' },
        h('button', { type: 'button', class: 'btn primary small', disabled: !!S.busy,
          onclick: () => {
            if (!window.confirm(`Add ${plural(dry.ids.length, 'case')} to the library through the adapter? This writes to the project.`)) return;
            act('Adding the cases…', async () => {
              const done = await api('POST', '/api/cases', { confirm: dry.file });
              S.casesDone = done;
              S.casesDry = null;
              if (done.done) {
                for (const [oldId, newId] of dry.ids) {
                  const c = caseById(oldId);
                  if (!c) continue;
                  c.id = newId;
                  c.in_library = true;
                  c.flags = c.flags.filter((f) => f !== 'not-in-library');
                  if (S.moved[oldId]) { S.moved[newId] = S.moved[oldId]; delete S.moved[oldId]; }
                  S.newTicked.delete(oldId);
                  delete S.newIds[oldId];
                }
                await refreshSuggestion(false);
              }
            });
          } }, 'Add to the library')) : null) : null,
    S.casesDone ? h('div', { class: 'stack' },
      h('p', { class: 'small', text: `${S.casesDone.meaning} (exit code ${S.casesDone.code}).` }),
      output(S.casesDone.output)) : null);
}

function gapsPanel() {
  const flagged = S.cases.filter((c) => c.flags.some((f) => f !== 'not-in-library'));
  const gaps = S.reading && S.reading.ok ? S.reading.gaps : [];
  if (!flagged.length && !gaps.length && !S.dropped.length) return null;
  return h('section', { class: 'panel' },
    h('h2', { text: 'Specification gaps' }),
    h('ul', { class: 'plain' },
      flagged.map((c) => h('li', { class: 'stack' },
        h('span', {}, h('span', { class: 'mono', text: c.id }), ' ', h('span', { class: 'small', text: c.title })),
        h('span', { class: 'note', text: c.flags.filter((f) => f !== 'not-in-library').map((f) => FLAG_TEXT[f] || f).join('; ') }))),
      gaps.map((g) => h('li', { class: 'stack' },
        h('span', {}, h('span', { class: 'chip ask', text: 'Claude asks' }), ' ', g.id ? h('span', { class: 'mono', text: g.id }) : 'the notes'),
        h('span', { class: 'small', text: g.question })))),
    S.dropped.length ? h('p', { class: 'note', text: `Given more than once, the first kept: ${S.dropped.join(', ')}.` }) : null,
    h('p', { class: 'note', text: 'A gap is never filled with an assumption: answer it in the specification, or the run asks.' }));
}

function claudePanel() {
  const r = S.reading;
  const want = h('input', { type: 'checkbox', id: 'ask-proposals', onchange: (e) => { S.wantProposals = e.target.checked; } });
  want.checked = S.wantProposals;
  const different = r && r.ok ? Object.entries(r.assignments).filter(([id, a]) => S.sug.assignments[id] && S.sug.assignments[id].module !== a.module) : [];
  return h('section', { class: 'panel' },
    h('h2', { text: 'Ask Claude' }),
    h('p', { class: 'note', text: 'The rules read words. Claude Code reads the cases and the notes (no tool allowed) and '
      + 'answers with a type of test per case and the gaps it sees. It uses your Claude subscription.' }),
    h('label', { class: 'check', for: 'ask-proposals' }, want, h('span', { text: 'Also propose new cases' })),
    h('div', { class: 'row' },
      h('button', { type: 'button', class: 'btn small', disabled: !!S.busy || !(S.info && S.info.claude),
        onclick: () => act('Asking Claude…', async () => {
          S.reading = await api('POST', '/api/ask-claude', { cases: S.cases, notes: S.notes, proposals: S.wantProposals });
          if (S.reading.ok && S.reading.proposals.length) {
            const taken = new Set(S.cases.map((c) => c.id));
            let n = 0;
            for (const p of S.reading.proposals) {
              do { n += 1; } while (taken.has(`NEW-${n}`));
              p.id = `NEW-${n}`;
              taken.add(p.id);
              S.cases.push(p);
            }
            await refreshSuggestion(false);
          }
        }) }, 'Ask Claude')),
    r && !r.ok ? h('div', { class: 'stack' },
      h('p', { class: 'small', text: 'The answer was not the expected JSON, so nothing changed:' }), output(r.text)) : null,
    r && r.ok ? h('div', { class: 'stack' },
      h('p', { class: 'small', text: different.length
        ? `Claude reads ${plural(different.length, 'case')} differently from the rules:`
        : 'Claude agrees with the rules on every case.' }),
      h('ul', { class: 'plain' }, different.map(([id, a]) => h('li', { class: 'stack' },
        h('span', {}, h('span', { class: 'mono', text: id }), ` ${S.sug.assignments[id].module} → ${a.module}`),
        h('span', { class: 'note', text: a.why }),
        h('div', {}, h('button', { type: 'button', class: 'btn small', disabled: !!S.busy, onclick: () => {
          S.moved[id] = a.module;
          act('Applying the rules…', () => refreshSuggestion(false));
        } }, `Move ${id} to ${a.module}`))))),
      r.proposals.length ? h('p', { class: 'note', text: `${plural(r.proposals.length, 'proposal')} added to New cases.` }) : null) : null);
}

function renderSuggest() {
  const root = $('screen-suggest');
  if (!S.sug) { root.replaceChildren(); return; }
  const plan = S.plan || { batches: [], apart: [], selected: 0, planned: 0 };
  const envSelect = h('select', { onchange: (e) => { S.environment = e.target.value; } },
    h('option', { value: '', text: '(not given)' }),
    S.info.project.environments.map((k) => h('option', { value: k, selected: k === S.environment, text: k })));
  const buildInput = h('input', { type: 'text', value: S.build, placeholder: 'e.g. 2.4.1', oninput: (e) => { S.build = e.target.value; } });
  const first = plan.batches[0];
  const firstCard = first ? S.sug.cards.find((c) => c.key === first.module) : null;
  const save = (start) => act('Saving the plan…', async () => {
    await refreshPlan(true);
    const saved = S.plan.saved || [];
    await refreshState();
    S.plan = null;
    S.ticked = new Set([...S.ticked].filter((k) => k.startsWith('audit-')));
    await refreshPlan();
    if (start && saved.length) await startBatch(saved[0]);
    else go('run');
  });
  root.replaceChildren(
    h('h1', { id: 'h-suggest', text: 'Suggested tests' }),
    h('p', { class: 'lead', text: `From ${plural(S.cases.length, 'case')} and ${plural(S.notes.length, 'note')}. `
      + 'Tick what runs; open a card to see why each case is there and move it.' }),
    libraryWarning(),
    h('div', { class: 'cols' },
      h('div', { class: 'col-main' }, S.sug.cards.map(suggestionCard)),
      h('div', { class: 'col-side' }, planPanel(), newCasesPanel(), gapsPanel(), claudePanel())),
    h('div', { class: 'bar' },
      h('div', { class: 'row' },
        h('div', { class: 'grow' }, field('Environment of the run', envSelect, 'run-env')),
        h('div', { class: 'grow' }, field('Build or version tested', buildInput, 'run-build'))),
      h('div', { class: 'row' },
        h('button', { type: 'button', class: 'btn', disabled: !!S.busy, onclick: () => go('intake') }, 'Back'),
        h('button', { type: 'button', class: 'btn', disabled: !!S.busy || !plan.batches.length, onclick: () => save(false) },
          `Save ${plural(plan.batches.length, 'batch', 'batches')}`),
        h('button', { type: 'button', class: 'btn primary',
          disabled: !!S.busy || !first || !(firstCard && firstCard.ready) || !!(S.info && S.info.run),
          onclick: () => save(true) }, first ? `Save and start ${first.name}` : 'Save and start'))));
}

/* ------------------------------------------------------------------ screen 3: execution */

function defectCard(need) {
  const fields = Object.entries(need.new_defect || {}).filter(([k, v]) => k !== 'proposed' && typeof v === 'string');
  const editing = S.editing && S.editing.tc === need.tc ? S.editing : null;
  const body = [];
  if (editing && editing.mode === 'edit') {
    const inputs = {};
    body.push(h('div', { class: 'stack' }, fields.map(([k, v], i) => {
      const long = v.length > 60 || v.includes('\n');
      const control = long ? h('textarea', {}) : h('input', { type: 'text' });
      control.value = v;
      inputs[k] = control;
      return field(k, control, `edit-${i}`);
    })), h('div', { class: 'row' },
      h('button', { type: 'button', class: 'btn primary small', disabled: !!S.busy, onclick: () => {
        const changed = {};
        for (const [k, control] of Object.entries(inputs)) changed[k] = control.value;
        act('Saving…', () => editItem(need.tc, { action: 'edit', fields: changed }));
      } }, 'Save the text'),
      h('button', { type: 'button', class: 'btn small', onclick: () => { S.editing = null; render(); } }, 'Cancel')));
  } else if (editing && editing.mode === 'link') {
    const bug = h('input', { type: 'text', placeholder: 'e.g. BUG-41' });
    body.push(field('ID of the existing bug', bug, 'link-bug'), h('div', { class: 'row' },
      h('button', { type: 'button', class: 'btn primary small', disabled: !!S.busy,
        onclick: () => act('Saving…', () => editItem(need.tc, { action: 'link', bug: bug.value })) }, 'Link and drop the proposal'),
      h('button', { type: 'button', class: 'btn small', onclick: () => { S.editing = null; render(); } }, 'Cancel')));
  } else {
    body.push(h('dl', { class: 'kv' }, fields.flatMap(([k, v]) => [h('dt', { text: k }), h('dd', { text: v })])),
      h('p', { class: 'small', text: `Observed: ${need.actual_result}` }),
      h('div', { class: 'row' },
        h('button', { type: 'button', class: 'btn primary small', disabled: !!S.busy,
          onclick: () => act('Saving…', () => editItem(need.tc, { action: 'approve' })) }, 'Approve'),
        h('button', { type: 'button', class: 'btn small', disabled: !!S.busy,
          onclick: () => { S.editing = { tc: need.tc, mode: 'edit' }; render(); } }, 'Edit'),
        h('button', { type: 'button', class: 'btn small', disabled: !!S.busy,
          onclick: () => { S.editing = { tc: need.tc, mode: 'link' }; render(); } }, 'Link an existing bug'),
        h('button', { type: 'button', class: 'btn danger small', disabled: !!S.busy, onclick: () => {
          if (window.confirm(`Drop the proposed bug of ${need.tc}? The result stays as it is.`)) {
            act('Saving…', () => editItem(need.tc, { action: 'drop' }));
          }
        } }, 'Drop')),
      h('p', { class: 'note', text: 'Nothing reaches the tracker before you approve it and apply the run log.' }));
  }
  return h('section', { class: 'panel stack' },
    h('div', { class: 'row' }, h('span', { class: 'chip fail', text: 'Proposed bug' }), h('span', { class: 'mono small', text: need.tc })),
    body);
}

function questionCard(need, statuses) {
  const select = h('select', {}, statuses.map((s) => h('option', { value: s, text: s })));
  const observed = h('textarea', {});
  return h('section', { class: 'panel stack' },
    h('div', { class: 'row' }, h('span', { class: 'chip ask', text: 'Question' }), h('span', { class: 'mono small', text: need.tc })),
    h('h3', { text: need.text || '(the run gave no question text)' }),
    field('Final status', select, `q-status-${need.tc}`),
    field('What was observed (your answer, as the result)', observed, `q-actual-${need.tc}`),
    h('div', { class: 'row' },
      h('button', { type: 'button', class: 'btn primary small', disabled: !!S.busy,
        onclick: () => act('Saving…', () => editItem(need.tc, { action: 'answer', status: select.value, actual_result: observed.value })) },
        'Answer')),
    h('p', { class: 'note', text: 'A final report never has a case in Question.' }));
}

function applyPanel(log) {
  if (!log || !log.check) return null;
  const check = log.check;
  const out = S.applyOut;
  const isExcel = check.tracker === 'excel';
  const content = [];
  if (log.applied) {
    content.push(h('p', { class: 'small', text: `Already ${log.applied}. A correction goes to a new batch.` }));
  } else if (!check.ready) {
    content.push(h('p', { class: 'small', text: 'Not ready to apply:' }),
      h('ul', { class: 'problems' }, check.problems.map((p) => h('li', { text: p }))));
  } else {
    content.push(h('p', { class: 'small', text: `Ready: ${plural(check.summary.results, 'result')}, `
      + `${plural(check.summary.new_defects, 'new bug')}, ${plural(check.summary.linked_bugs, 'linked bug')} → ${check.summary.target}.` }));
    if (isExcel) {
      content.push(h('div', {}, h('button', { type: 'button', class: 'btn primary small', disabled: !!S.busy, onclick: () => {
        if (window.confirm(`Apply ${check.name} to ${check.summary.target}? The adapter writes the results and the new bugs to the workbook.`)) {
          act('Applying through the adapter…', () => applyLog(true));
        }
      } }, `Apply to ${check.summary.target}`)));
    } else {
      content.push(h('div', { class: 'row' },
        h('button', { type: 'button', class: 'btn small', disabled: !!S.busy,
          onclick: () => act('Running the dry run…', () => applyLog(false)) }, 'Show what would be sent (dry run)'),
        out && out.dry_run && out.ready ? h('button', { type: 'button', class: 'btn primary small', disabled: !!S.busy, onclick: () => {
          if (window.confirm(`Send ${check.name} to ${check.tracker}? This writes to the tracker.`)) {
            act('Sending through the adapter…', () => applyLog(true));
          }
        } }, `Send to ${check.tracker}`) : null));
    }
  }
  if (out && (out.output || out.code !== null)) {
    content.push(h('p', { class: 'small', text: `${out.meaning}${out.code !== null ? ` (exit code ${out.code})` : ''}.` }), output(out.output));
  }
  return h('section', { class: 'panel stack' }, h('h2', { text: 'Apply' }), content,
    h('p', { class: 'note', text: 'The interface does not update context.md nor commit: /checkpoint in a session does.' }));
}

function resultsTable(log) {
  const byCase = {};
  for (const item of log.items) byCase[item.tc] = item;
  const order = [...(log.planned || []), ...log.items.map((i) => i.tc).filter((tc) => !(log.planned || []).includes(tc))];
  return h('section', { class: 'panel flush' },
    h('div', { class: 'panel-head' },
      h('h2', { text: 'Cases' }),
      h('div', { class: 'row small muted' }, h('span', { text: `${log.total} of ${order.length} with a result` }),
        h('progress', { max: String(Math.max(order.length, 1)), value: String(log.total), 'aria-label': 'Cases with a result' }))),
    h('div', { class: 'scroll' }, h('table', {},
      h('thead', {}, h('tr', {}, ['ID', 'Status', 'Observed', 'Bug'].map((t) => h('th', { scope: 'col', text: t })))),
      h('tbody', {}, order.map((tc) => {
        const item = byCase[tc];
        return h('tr', {},
          h('td', { class: 'id', text: tc }),
          h('td', {}, item ? statusChip(item.status) : h('span', { class: 'chip outline', text: 'Not run' })),
          h('td', { text: item ? item.actual_result : '' }),
          h('td', { text: item ? (item.bug || (item.new_defect ? (item.proposed ? 'proposed' : 'new, approved') : '')) : '' }));
      })))));
}

function renderRun() {
  const root = $('screen-run');
  const run = S.run;
  const log = S.log && S.log.items ? S.log : null;
  const running = run && run.state === 'running';
  const title = run ? (running ? 'Run in progress' : `Run ${run.state}`) : log ? 'Run log' : 'Execution';
  const head = [h('h1', { id: 'h-run', text: title })];
  if (run) {
    head.push(h('p', { class: 'lead' }, `${run.label} · started ${run.started.replace('T', ' ')}`
      + (run.finished ? ` · ended ${run.finished.replace('T', ' ')}` : '')
      + (run.reason ? ` · ${run.reason}` : '')
      + (run.report ? ` · report: ${run.report}` : '')));
  } else if (log) {
    head.push(h('p', { class: 'lead mono', text: log.name
      + (log.run && (log.run.environment || log.run.build) ? ` · ${log.run.environment || ''} ${log.run.build || ''}` : '') }));
  } else {
    head.push(h('p', { class: 'lead', text: 'Start a planned batch, or open a run log, from the lists on the right.' }));
  }
  const main = [];
  if (running) {
    main.push(h('div', { class: 'row' }, h('button', { type: 'button', class: 'btn danger', disabled: !!S.busy, onclick: () => {
      if (window.confirm('Stop the run? The results already written are kept.')) {
        act('Stopping…', async () => { await api('POST', `/api/runs/${encodeURIComponent(S.runId)}/stop`, {}); await pollRun(); });
      }
    } }, 'Stop the run'), h('span', { class: 'note', text: 'Nobody answers the agent during the run: questions and proposed bugs wait here.' })));
  }
  if (log) {
    main.push(h('div', { class: 'tiles' }, log.counts.length
      ? log.counts.map((c) => h('div', { class: 'tile' }, h('div', {}, statusChip(c.status)), h('div', { class: 'n', text: String(c.n) })))
      : h('p', { class: 'note', text: running ? 'No result written yet.' : 'This run log has no result.' })));
    main.push(resultsTable(log));
  }
  if (run && run.refused && run.refused.length) {
    main.push(h('section', { role: 'status' }, h('h2', { text: `Refused tools · ${run.refused.length}` }),
      h('p', { class: 'note', text: 'Claude Code refused these uses: a batch gets only the tools of its module '
        + '(docs/qa-ui.md 8.1). Check the batch\'s module and the project\'s modules; the cases it could not run stay Not run.' }),
      h('ul', { class: 'problems' }, run.refused.map((r) => h('li', { class: 'mono small', text: r })))));
  }
  if (run) {
    main.push(h('section', {}, h('h2', { text: `Output (${plural(run.lines, 'line')}, redacted)` }),
      h('pre', { class: 'log', tabindex: '0', 'aria-label': 'Output of the run', text: run.tail.join('\n') || '(nothing yet)' })));
  }
  const side = [];
  if (log) {
    side.push(h('h2', { text: `Needs you · ${log.needs.length}` }));
    if (!log.needs.length) side.push(h('section', { class: 'panel' }, h('p', { class: 'note', text: 'Nothing is waiting for you in this run log.' })));
    for (const need of log.needs) side.push(need.kind === 'defect' ? defectCard(need) : questionCard(need, log.statuses || []));
    if (!running) side.push(applyPanel(log));
  }
  side.push(listsPanel());
  root.replaceChildren(...head, h('div', { class: 'cols' }, h('div', { class: 'col-main' }, main), h('div', { class: 'col-side' }, side)));
  const pre = root.querySelector('.log');
  if (pre && running) pre.scrollTop = pre.scrollHeight;
}

/* ------------------------------------------------------------------ start */

function render() {
  renderTop();
  for (const screen of ['intake', 'suggest', 'run']) $(`screen-${screen}`).hidden = screen !== S.screen;
  if (S.screen === 'intake') renderIntake();
  if (S.screen === 'suggest') renderSuggest();
  if (S.screen === 'run') renderRun();
}

/* The poll of a run never rebuilds a screen that is being typed in: on the other screens only the top changes,
   and on the execution screen, while a field has the focus, only the output. */
function renderFromPoll() {
  renderTop();
  if (S.screen !== 'run') return;
  const active = document.activeElement;
  const typing = active && $('screen-run').contains(active) && ['INPUT', 'TEXTAREA', 'SELECT'].includes(active.tagName);
  if (!typing) {
    renderRun();
    return;
  }
  const pre = document.querySelector('#screen-run .log');
  if (pre && S.run) {
    pre.textContent = S.run.tail.join('\n') || '(nothing yet)';
    pre.scrollTop = pre.scrollHeight;
  }
}

async function start() {
  const params = new URLSearchParams(window.location.search);
  const fromUrl = params.get('token');
  let stored = '';
  try { stored = window.sessionStorage.getItem('qa-ui-token') || ''; } catch (e) { /* storage may be unavailable */ }
  S.token = fromUrl || stored;
  if (fromUrl) {
    try { window.sessionStorage.setItem('qa-ui-token', fromUrl); } catch (e) { /* the token stays in memory */ }
    window.history.replaceState(null, '', window.location.pathname);
  }
  for (const button of document.querySelectorAll('.step')) button.addEventListener('click', () => go(button.dataset.screen));
  render();
  if (!S.token) {
    banner('Open the address `python -m qa_ui` printed: it holds the token of this start.');
    return;
  }
  try {
    await refreshState();
  } catch (e) {
    banner(e.message);
  }
  render();
}

start();
