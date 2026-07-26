const $ = (id) => document.getElementById(id);
const phone = $('phone');
const player = $('player');

let checkinId = null;
let audioCtx = null;
let ringTimer = null;
let lastStatuses = {};
const openCards = new Set();   // survives the re-render on every board update

const esc = (s) =>
  String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// Milestone times are on the simulated clock, so show them the way a planner
// would — "today 5:44 PM" — never a raw ISO string.
function when(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const t = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  return sameDay ? `today ${t}` : `${days[d.getDay()]} ${t}`;
}

/* ── audio ─────────────────────────────────────────────────────────────── */
// Browsers block audio until a gesture. Arming once up front means the ring —
// which fires with nobody touching the laptop — is still audible.
function arm() {
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  audioCtx.resume();
  $('arm').style.display = 'none';
}

function ringtone(on) {
  clearInterval(ringTimer);
  if (!on || !audioCtx) return;
  const beep = () => {
    const t = audioCtx.currentTime;
    [0, 0.42].forEach((off) => {
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.type = 'sine'; o.frequency.value = 620;
      g.gain.setValueAtTime(0.0001, t + off);
      g.gain.exponentialRampToValueAtTime(0.22, t + off + 0.03);
      g.gain.exponentialRampToValueAtTime(0.0001, t + off + 0.34);
      o.connect(g).connect(audioCtx.destination);
      o.start(t + off); o.stop(t + off + 0.36);
    });
  };
  beep();
  ringTimer = setInterval(beep, 2200);
}

// Text lands first, voice follows on its own request. The audio arrives a few
// seconds later and that is fine — nobody is waiting on a blank screen.
function play(turnId) {
  if (!turnId) return;
  player.src = `/voice/${turnId}`;
  player.play().catch(() => {});
}

/* ── transcript ────────────────────────────────────────────────────────── */
function bubble(role, text, extra = {}) {
  const el = document.createElement('div');
  el.className = `bubble ${role}`;
  el.textContent = text;

  if (extra.recalled?.length) {
    const r = document.createElement('div');
    r.className = 'recall';
    r.textContent = `↺ remembered: ${extra.recalled[0].blocker} — "${extra.recalled[0].evidence}"`;
    el.appendChild(r);
  }
  if (extra.blocker) {
    const c = document.createElement('div');
    c.className = 'chip' + (extra.confidence < 0.6 ? ' low' : '');
    c.innerHTML = `${extra.blocker} <span class="conf">· ${extra.confidence}</span> → ${extra.strategy}`;
    el.appendChild(c);
  }
  if (extra.change) {
    const c = document.createElement('div');
    c.className = 'change';
    c.textContent = `✓ ${extra.change}`;
    el.appendChild(c);
  }

  const t = $('transcript');
  t.appendChild(el);
  t.scrollTop = t.scrollHeight;
  return el;
}

function thinking(on) {
  document.querySelectorAll('.bubble.think').forEach((e) => e.remove());
  if (on) bubble('agent think', 'listening…');
}

/* ── board ─────────────────────────────────────────────────────────────── */
async function refresh() {
  const b = await (await fetch('/board')).json();

  $('simNow').textContent = b.sim_now.slice(11, 16);
  const pill = $('modePill');
  pill.textContent = b.mode;
  pill.className = 'pill ' + b.mode;

  $('goalTitle').textContent = b.goals.length ? 'Your plan' : 'No goal yet';

  const wrap = $('milestones');
  wrap.innerHTML = '';
  for (const g of b.goals) {
    const h = document.createElement('div');
    h.className = 'goal-head';
    h.textContent = g.title;
    wrap.appendChild(h);

    for (const m of g.milestones) {
      const el = document.createElement('div');
      el.className = 'ms';
      el.dataset.status = m.status;
      if (openCards.has(m.id)) el.classList.add('open');

      const due = m.status === 'done' ? 'completed' : `due ${when(m.start_at)}`;
      el.innerHTML =
        `<div class="ms-row">
           <span class="caret">▸</span>
           <div class="ms-title">${esc(m.title)}</div>
           <div class="ms-meta">${m.est_min} min<br><span class="ms-status">${m.status}</span></div>
         </div>
         <div class="ms-body">
           <p class="ms-note">${esc(m.note || 'No description.')}</p>
           <p class="ms-due">${due}</p>
         </div>`;

      el.querySelector('.ms-row').onclick = () => {
        el.classList.toggle('open');
        openCards.has(m.id) ? openCards.delete(m.id) : openCards.add(m.id);
      };

      if (lastStatuses[m.id] && lastStatuses[m.id] !== m.status) el.classList.add('flash');
      lastStatuses[m.id] = m.status;
      wrap.appendChild(el);
    }
  }

  $('ledger').innerHTML = b.ledger.length
    ? b.ledger.map((l) => `<li><b>${l.blocker}</b> — "${l.evidence}"</li>`).join('')
    : '<li>nothing yet</li>';

  $('commitments').innerHTML = b.commitments.length
    ? b.commitments.map((c) => `<li><b>${c.size_min} min</b> — ${c.text}</li>`).join('')
    : '<li>nothing agreed yet</li>';

  $('clipRow').innerHTML = '';
  for (const c of b.clips) {
    const btn = document.createElement('button');
    btn.textContent = c.replace(/\.\w+$/, '');
    btn.onclick = () => sendTurn({ clip: c });
    $('clipRow').appendChild(btn);
  }
}

/* ── call flow ─────────────────────────────────────────────────────────── */
new EventSource('/events').onmessage = (e) => {
  const ev = JSON.parse(e.data);
  if (ev.type === 'ring') {
    checkinId = ev.checkin_id;
    $('ringReason').textContent = `You were meant to start: ${ev.title}`;
    $('callTitle').textContent = ev.title;
    phone.dataset.state = 'ringing';
    ringtone(true);
  }
  if (ev.type === 'board') refresh();
};

async function answerCall() {
  if (phone.dataset.state !== 'ringing') return;
  ringtone(false);
  phone.dataset.state = 'incall';
  $('transcript').innerHTML = '';
  thinking(true);
  const r = await (await fetch(`/answer/${checkinId}`, { method: 'POST' })).json();
  thinking(false);
  bubble('agent', r.text, { recalled: r.recalled });
  play(r.turn_id);
}

async function endCall() {
  ringtone(false);
  if (phone.dataset.state === 'idle') return;
  if (checkinId) await fetch(`/hangup/${checkinId}`, { method: 'POST' });
  checkinId = null;
  phone.dataset.state = 'idle';
  refresh();
}

$('answerBtn').onclick = answerCall;
$('declineBtn').onclick = endCall;
$('endBtn').onclick = endCall;

async function sendTurn(payload) {
  if (!checkinId) return;
  const fd = new FormData();
  if (payload.blob) fd.append('audio', payload.blob, 'turn.webm');
  if (payload.clip) fd.append('clip', payload.clip);
  if (payload.text) fd.append('text', payload.text);

  thinking(true);
  let r;
  try {
    r = await (await fetch(`/turn/${checkinId}`, { method: 'POST', body: fd })).json();
  } catch (err) {
    thinking(false);
    bubble('agent', 'connection dropped — try again');
    return;
  }
  thinking(false);

  bubble('user', r.user_text);
  bubble('agent', r.reply_text, {
    blocker: r.blocker, confidence: r.confidence,
    strategy: r.strategy, change: r.board_change,
  });
  play(r.turn_id);
  refresh();

  if (r.close) setTimeout(() => { phone.dataset.state = 'idle'; checkinId = null; }, 4500);
}

$('typeBox').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.target.value.trim()) {
    sendTurn({ text: e.target.value.trim() });
    e.target.value = '';
  }
});

/* ── push to talk ──────────────────────────────────────────────────────── */
// Push-to-talk, never open mic: a hall with 200 builders in it will happily
// transcribe the room. Hard stop at 25s because sync STT is a sub-30s endpoint.
function recorder(btn, onDone) {
  let rec = null, chunks = [], stopTimer = null;

  const start = async (e) => {
    e.preventDefault();
    if (rec) return;
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    rec = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' });
    chunks = [];
    rec.ondataavailable = (ev) => chunks.push(ev.data);
    rec.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      onDone(new Blob(chunks, { type: 'audio/webm' }));
      rec = null;
    };
    rec.start();
    btn.classList.add('rec');
    stopTimer = setTimeout(stop, 25000);
  };

  const stop = (e) => {
    e?.preventDefault();
    clearTimeout(stopTimer);
    btn.classList.remove('rec');
    if (rec?.state === 'recording') rec.stop();
  };

  btn.addEventListener('pointerdown', start);
  btn.addEventListener('pointerup', stop);
  btn.addEventListener('pointerleave', stop);
}

recorder($('talkBtn'), (blob) => sendTurn({ blob }));
recorder($('goalMic'), async (blob) => {
  const fd = new FormData();
  fd.append('audio', blob, 'goal.webm');
  $('goalText').value = 'transcribing…';
  const r = await (await fetch('/goal', { method: 'POST', body: fd })).json();
  $('goalText').value = r.transcript || '';
  refresh();
});

/* ── goal intake + reset ───────────────────────────────────────────────── */
async function addGoal() {
  const text = $('goalText').value.trim();
  if (!text || $('goalSend').disabled) return;
  const fd = new FormData();
  fd.append('text', text);
  $('goalSend').disabled = true;
  await fetch('/goal', { method: 'POST', body: fd });
  $('goalSend').disabled = false;
  $('goalText').value = '';
  refresh();
}

$('goalSend').onclick = addGoal;

$('resetBtn').onclick = async () => {
  ringtone(false);
  checkinId = null;
  lastStatuses = {};
  phone.dataset.state = 'idle';
  $('transcript').innerHTML = '';
  await fetch('/reset', { method: 'POST' });
  refresh();
};

/* ── shortcuts ─────────────────────────────────────────────────────────── */
// Reaching for the mouse mid-demo is the one thing that makes a live call look
// staged. Keyed off e.code so they survive a non-US layout.
const isTyping = (el) =>
  !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable);

document.addEventListener('keydown', (e) => {
  if (e.repeat) return;

  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    addGoal();
    return;
  }

  // Shift+, and Shift+. type "<" and ">", so they must stay inert while the
  // goal box or the excuse box has focus.
  if (isTyping(e.target) || !e.shiftKey || e.ctrlKey || e.metaKey || e.altKey) return;

  if (e.code === 'Comma' || e.key === '<') { e.preventDefault(); answerCall(); }
  if (e.code === 'Period' || e.key === '>') { e.preventDefault(); endCall(); }
});

$('armBtn').onclick = arm;
refresh();
setInterval(() => fetch('/board').then((r) => r.json())
  .then((b) => ($('simNow').textContent = b.sim_now.slice(11, 16))), 1000);
