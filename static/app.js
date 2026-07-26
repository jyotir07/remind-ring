const $ = (id) => document.getElementById(id);
const phone = $('phone');
const player = $('player');

let checkinId = null;
let audioCtx = null;
let ringTimer = null;
let lastStatuses = {};

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

  $('goalTitle').textContent = b.goal ? b.goal.title : 'No goal yet';

  $('milestones').innerHTML = '';
  for (const m of b.milestones) {
    const el = document.createElement('div');
    el.className = 'ms';
    el.dataset.status = m.status;
    el.innerHTML =
      `<div class="ms-title">${m.title}</div>
       <div class="ms-meta">${m.est_min} min<br><span class="ms-status">${m.status}</span></div>`;
    if (lastStatuses[m.id] && lastStatuses[m.id] !== m.status) el.classList.add('flash');
    lastStatuses[m.id] = m.status;
    $('milestones').appendChild(el);
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

$('answerBtn').onclick = async () => {
  ringtone(false);
  phone.dataset.state = 'incall';
  $('transcript').innerHTML = '';
  thinking(true);
  const r = await (await fetch(`/answer/${checkinId}`, { method: 'POST' })).json();
  thinking(false);
  bubble('agent', r.text, { recalled: r.recalled });
  play(r.turn_id);
};

$('declineBtn').onclick = () => { ringtone(false); endCall(); };
$('endBtn').onclick = endCall;

async function endCall() {
  if (checkinId) await fetch(`/hangup/${checkinId}`, { method: 'POST' });
  checkinId = null;
  phone.dataset.state = 'idle';
  refresh();
}

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
$('goalSend').onclick = async () => {
  const text = $('goalText').value.trim();
  if (!text) return;
  const fd = new FormData();
  fd.append('text', text);
  $('goalSend').disabled = true;
  await fetch('/goal', { method: 'POST', body: fd });
  $('goalSend').disabled = false;
  $('goalText').value = '';
  refresh();
};

$('resetBtn').onclick = async () => {
  ringtone(false);
  checkinId = null;
  lastStatuses = {};
  phone.dataset.state = 'idle';
  $('transcript').innerHTML = '';
  await fetch('/reset', { method: 'POST' });
  refresh();
};

$('armBtn').onclick = arm;
refresh();
setInterval(() => fetch('/board').then((r) => r.json())
  .then((b) => ($('simNow').textContent = b.sim_now.slice(11, 16))), 1000);
