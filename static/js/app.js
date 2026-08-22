// Shared runtime for FitTrack.
//
// Page-specific logic still lives in each template's scripts block; what lives
// here is everything more than one page needs, plus the single place that turns
// a setting into behaviour. Anything reading window.FITTRACK directly in a
// template is a bug waiting to happen — go through FT.get.

/* ── Settings access ───────────────────────────────────────────── */

const FT = (function () {
    // Server-rendered by base.html, so it is already correct on first paint.
    let values = window.FITTRACK || {};

    function get(key, fallback) {
        const v = values[key];
        return (v === undefined || v === null) ? fallback : v;
    }

    /** Replace the whole settings object and re-apply anything visual. */
    function replace(next) {
        if (!next) return;
        values = next;
        window.FITTRACK = next;
        applyAppearance();
        document.dispatchEvent(new CustomEvent('fittrack:settings', { detail: next }));
    }

    /** Persist a single setting and update the live copy. */
    async function set(key, value) {
        const data = await apiRequest('/api/settings', {
            method: 'POST',
            body: JSON.stringify({ changes: [{ key: key, value: value }] })
        });
        if (data && data.values) replace(data.values);
        return data;
    }

    return { get, set, replace, all: () => values };
})();

/**
 * Push appearance settings onto <html>.
 *
 * The server already wrote these attributes, so this only matters after a live
 * change — but it keeps one definition of how a setting maps to an attribute.
 */
function applyAppearance() {
    const root = document.documentElement;
    const theme = FT.get('appearance.theme', 'system');
    if (theme === 'system') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', theme);

    root.setAttribute('data-accent', FT.get('appearance.accent', 'teal'));
    root.setAttribute('data-density', FT.get('appearance.density', 'comfortable'));
    root.setAttribute('data-font', FT.get('appearance.font_family', 'default'));
    root.setAttribute('data-corners', FT.get('appearance.corner_style', 'rounded'));
    root.setAttribute('data-bg', FT.get('appearance.background', 'plain'));
    root.setAttribute('data-nav', FT.get('ui.nav_style', 'full'));
    root.setAttribute('data-mode', FT.get('ui.mode', 'simple'));
    root.setAttribute('data-motion', FT.get('appearance.reduce_motion', false) ? 'off' : 'full');
    root.setAttribute('data-tips', FT.get('ui.show_tips', true) ? 'on' : 'off');
    root.setAttribute('data-bigtimer', FT.get('timers.big_timer', false) ? 'on' : 'off');
    root.style.setProperty('--base-font-size', FT.get('appearance.font_size', 15) + 'px');

    let styleEl = document.getElementById('user-custom-css');
    const css = FT.get('appearance.custom_css', '');
    if (css) {
        if (!styleEl) {
            styleEl = document.createElement('style');
            styleEl.id = 'user-custom-css';
            document.head.appendChild(styleEl);
        }
        // textContent, never innerHTML — this string came from a form field.
        styleEl.textContent = css;
    } else if (styleEl) {
        styleEl.remove();
    }
}

/* ── Escaping and formatting ───────────────────────────────────── */

function escapeHtml(str) {
    if (str === null || str === undefined || str === '') return '';
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
    return String(str).replace(/[&<>"']/g, c => map[c]);
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const MONTHS_LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
                     'August', 'September', 'October', 'November', 'December'];

/** Format a Date or YYYY-MM-DD string per units.date_format. */
function formatDate(value) {
    const d = (value instanceof Date) ? value : new Date(String(value).slice(0, 10) + 'T00:00:00');
    if (isNaN(d)) return String(value || '');
    const yy = d.getFullYear();
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    switch (FT.get('units.date_format', 'iso')) {
        case 'us':   return `${mm}/${dd}/${yy}`;
        case 'eu':   return `${dd}/${mm}/${yy}`;
        case 'long': return `${d.getDate()} ${MONTHS_LONG[d.getMonth()]} ${yy}`;
        default:     return `${yy}-${mm}-${dd}`;
    }
}

/** Format a time of day per units.time_format. */
function formatClock(date) {
    const d = date instanceof Date ? date : new Date(date);
    if (isNaN(d)) return '';
    if (FT.get('units.time_format', '24h') === '12h') {
        const h = d.getHours() % 12 || 12;
        const suffix = d.getHours() < 12 ? 'am' : 'pm';
        return `${h}:${String(d.getMinutes()).padStart(2, '0')}${suffix}`;
    }
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

function weightUnit() { return FT.get('units.weight', 'kg'); }

/** mm:ss, or h:mm:ss once a session passes an hour. */
function formatDuration(seconds) {
    seconds = Math.max(0, Math.round(seconds));
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    const pad = n => String(n).padStart(2, '0');
    return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

/* ── API helper ────────────────────────────────────────────────── */

function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
}

/**
 * fetch + JSON + CSRF, with the server's error message surfaced.
 *
 * Every state-changing endpoint added since the settings work validates the
 * CSRF token, so the header goes on automatically rather than being remembered
 * at each call site.
 */
async function apiRequest(url, options = {}) {
    try {
        const response = await fetch(url, {
            ...options,
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': csrfToken(),
                ...(options.headers || {})
            }
        });

        if (!response.ok) {
            let detail = '';
            try {
                const body = await response.json();
                detail = body.error || body.message || '';
            } catch (e) { /* non-JSON error body */ }
            throw new Error(detail ? `${detail} (HTTP ${response.status})` : `HTTP error! status: ${response.status}`);
        }
        return await response.json();
    } catch (error) {
        console.error('API request failed:', url, error);
        throw error;
    }
}

async function saveSession(sessionData) {
    return apiRequest('/api/complete_session', {
        method: 'POST',
        body: JSON.stringify(sessionData)
    });
}

/* ── Toasts ────────────────────────────────────────────────────── */

const TOAST_ICONS = {
    success: 'check-circle-2', error: 'x-circle', warning: 'alert-triangle',
    info: 'info', message: 'message-square'
};

function showToast(msg, category, timeout) {
    category = category || 'message';
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = 'toast toast-' + category;
    toast.setAttribute('role', category === 'error' ? 'alert' : 'status');

    const icon = document.createElement('span');
    icon.className = 'toast-icon';
    const i = document.createElement('i');
    i.setAttribute('data-lucide', TOAST_ICONS[category] || TOAST_ICONS.message);
    i.className = 'icon-md';
    icon.appendChild(i);

    const text = document.createElement('span');
    text.className = 'toast-msg';
    text.textContent = msg;              // never innerHTML: messages carry user data

    const close = document.createElement('button');
    close.className = 'toast-close';
    close.setAttribute('aria-label', 'Close');
    close.textContent = '×';
    close.addEventListener('click', () => dismissToast(toast));

    toast.append(icon, text, close);
    container.appendChild(toast);
    if (window.lucide) lucide.createIcons({ root: toast });

    setTimeout(() => dismissToast(toast), timeout || 5000);
}

function dismissToast(toast) {
    if (!toast || !toast.parentNode) return;
    toast.classList.add('removing');
    toast.addEventListener('animationend', () => toast.remove(), { once: true });
    // Animations may be disabled by appearance.reduce_motion, in which case
    // animationend never fires — remove on a timer as well.
    setTimeout(() => toast.remove(), 600);
}

/* ── Sound, speech and haptics ─────────────────────────────────── */
//
// Tones are synthesised rather than shipped as audio files: no extra requests,
// no assets to cache, and the cue style is a waveform choice rather than a
// different download.

const Cues = (function () {
    let ctx = null;

    function context() {
        if (ctx) return ctx;
        const Ctor = window.AudioContext || window.webkitAudioContext;
        if (!Ctor) return null;
        ctx = new Ctor();
        return ctx;
    }

    const STYLES = {
        beep:  { type: 'sine',     freq: 880,  length: 0.16 },
        chime: { type: 'triangle', freq: 1174, length: 0.42 },
        click: { type: 'square',   freq: 1600, length: 0.05 },
        bell:  { type: 'sine',     freq: 1568, length: 0.72 }
    };

    /** One tone. `scale` shortens/detunes it for the countdown ticks. */
    function tone(scale) {
        if (!FT.get('audio.enabled', true)) return;
        const audio = context();
        if (!audio) return;
        if (audio.state === 'suspended') audio.resume();

        const style = STYLES[FT.get('audio.cue_style', 'beep')] || STYLES.beep;
        const volume = Math.max(0, Math.min(100, FT.get('audio.volume', 60))) / 100;
        if (volume === 0) return;

        const osc = audio.createOscillator();
        const gain = audio.createGain();
        osc.type = style.type;
        osc.frequency.value = style.freq * (scale && scale.pitch ? scale.pitch : 1);

        const length = style.length * (scale && scale.length ? scale.length : 1);
        const now = audio.currentTime;
        gain.gain.setValueAtTime(0.0001, now);
        gain.gain.exponentialRampToValueAtTime(volume * 0.35, now + 0.01);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + length);

        osc.connect(gain).connect(audio.destination);
        osc.start(now);
        osc.stop(now + length + 0.02);
    }

    return {
        /** End of an exercise or rest block. */
        transition() { tone(); vibrate([120]); },
        /** One of the last few seconds before a block ends. */
        tick() {
            if (!FT.get('audio.countdown_ticks', true)) return;
            tone({ pitch: 0.6, length: 0.4 });
        },
        /** The whole workout is done. */
        finish() { tone({ pitch: 1.5, length: 1.6 }); vibrate([90, 60, 90, 60, 220]); },
        /** Unlock audio on the first user gesture — browsers require it. */
        unlock() { const audio = context(); if (audio && audio.state === 'suspended') audio.resume(); }
    };
})();

function vibrate(pattern) {
    if (!FT.get('audio.vibrate', true)) return;
    if (navigator.vibrate) {
        try { navigator.vibrate(pattern); } catch (e) { /* unsupported */ }
    }
}

function announce(text) {
    if (!FT.get('audio.voice_announce', false) || !text) return;
    if (!('speechSynthesis' in window)) return;
    try {
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(String(text));
        utterance.rate = 1;
        utterance.volume = Math.max(0, Math.min(100, FT.get('audio.volume', 60))) / 100;
        window.speechSynthesis.speak(utterance);
    } catch (e) { /* speech unavailable */ }
}

/* ── Screen wake lock ──────────────────────────────────────────── */

const WakeLock = (function () {
    let lock = null;

    async function acquire() {
        if (!FT.get('session.keep_awake', true)) return;
        if (!('wakeLock' in navigator)) return;
        try {
            lock = await navigator.wakeLock.request('screen');
            // The browser drops the lock when the tab is hidden; take it back.
            document.addEventListener('visibilitychange', reacquire);
        } catch (e) {
            console.info('Wake lock unavailable:', e.message);
        }
    }

    async function reacquire() {
        if (document.visibilityState === 'visible' && lock === null) await acquire();
    }

    function release() {
        document.removeEventListener('visibilitychange', reacquire);
        if (lock) { lock.release().catch(() => {}); lock = null; }
    }

    return { acquire, release };
})();

/* ── Offline cache ─────────────────────────────────────────────── */

if (FT.get('advanced.offline_cache', true) && 'serviceWorker' in navigator) {
    window.addEventListener('load', function () {
        navigator.serviceWorker.register('/sw.js').catch(function (e) {
            console.info('Offline cache unavailable:', e.message);
        });
    });
} else if ('serviceWorker' in navigator) {
    // The setting was turned off — drop any worker registered earlier.
    navigator.serviceWorker.getRegistrations()
        .then(rs => rs.forEach(r => r.unregister()))
        .catch(() => {});
}

/* ── Exports ───────────────────────────────────────────────────── */

window.FT = FT;
window.showToast = showToast;
window.escapeHtml = escapeHtml;
window.apiRequest = apiRequest;
window.formatDate = formatDate;
window.formatClock = formatClock;
window.formatDuration = formatDuration;
window.weightUnit = weightUnit;
window.Cues = Cues;
window.WakeLock = WakeLock;
window.announce = announce;
window.applyAppearance = applyAppearance;
window.TrainingApp = { saveSession };
