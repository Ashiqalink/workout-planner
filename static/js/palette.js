// Command palette — Ctrl+K / Cmd+K.
//
// This is how the app stays simple on the surface while still exposing ~80
// settings: someone who wants depth gets a single keystroke to everything,
// and someone who does not never encounters it. It searches pages, settings
// and presets at once, and can flip a switch without leaving the page you are on.

(function () {
    const overlay = document.getElementById('palette');
    if (!overlay) return;

    const input = document.getElementById('palette-input');
    const results = document.getElementById('palette-results');
    const openButton = document.getElementById('palette-open');

    let items = [];
    let cursor = 0;
    let searchToken = 0;

    const PAGES = [
        { title: 'Dashboard', sub: 'Streak, goal and today\'s session', href: '/dashboard', icon: 'gauge' },
        { title: 'Planner', sub: 'Build a workout', href: '/planner', icon: 'calendar' },
        { title: 'Exercise library', sub: 'Browse and filter every exercise', href: '/library', icon: 'library' },
        { title: 'Progress', sub: 'Charts, history and records', href: '/progress', icon: 'trending-up' },
        { title: 'Session', sub: 'Run the workout you have queued', href: '/session', icon: 'activity' },
        { title: 'Settings', sub: 'Every option in the app', href: '/settings', icon: 'settings' },
        { title: 'Export my data', sub: 'Download settings, sessions and logs', href: '/api/data/export', icon: 'download' }
    ];

    /* ── Open / close ────────────────────────────────────────────── */

    function open(prefill) {
        overlay.hidden = false;
        input.value = prefill || '';
        input.focus();
        input.select();
        render(buildDefault());
        if (window.Cues) Cues.unlock();   // first gesture — lets later tones play
    }

    function close() {
        overlay.hidden = true;
        input.value = '';
        results.innerHTML = '';
        items = [];
    }

    document.addEventListener('keydown', event => {
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
            event.preventDefault();
            overlay.hidden ? open() : close();
            return;
        }
        if (event.key === 'Escape' && !overlay.hidden) {
            event.preventDefault();
            close();
        }
    });

    if (openButton) openButton.addEventListener('click', () => open());
    overlay.addEventListener('mousedown', event => {
        if (event.target === overlay) close();
    });

    /* ── Result building ─────────────────────────────────────────── */

    function buildDefault() {
        return PAGES.map(page => ({
            title: page.title, sub: page.sub, icon: page.icon,
            run: () => { window.location.href = page.href; }
        }));
    }

    function pageMatches(query) {
        return PAGES
            .filter(p => (p.title + ' ' + p.sub).toLowerCase().includes(query))
            .map(p => ({
                title: p.title, sub: p.sub, icon: p.icon,
                run: () => { window.location.href = p.href; }
            }));
    }

    /**
     * A setting result. Switches flip in place — that is the whole point of
     * having the palette — while anything with a value to choose sends you to
     * the settings page with the row highlighted.
     */
    function settingItem(result) {
        const isSwitch = result.value_label === 'on' || result.value_label === 'off';
        return {
            title: result.label,
            sub: `${result.group}${result.help ? ' — ' + result.help : ''}`,
            value: result.value_label,
            icon: isSwitch ? 'toggle-right' : 'sliders-horizontal',
            run: async () => {
                if (isSwitch) {
                    try {
                        const data = await FT.set(result.key, result.value_label !== 'on');
                        close();
                        showToast(data.summary, 'success', 2600);
                    } catch (e) { showToast(e.message, 'error'); }
                    return;
                }
                window.location.href = '/settings#focus=' + encodeURIComponent(result.key);
            }
        };
    }

    async function search(query) {
        const token = ++searchToken;
        const trimmed = query.trim();
        if (!trimmed) { render(buildDefault()); return; }

        const local = pageMatches(trimmed.toLowerCase());
        render(local.concat([{
            title: 'Searching settings…', sub: '', icon: 'loader', disabled: true
        }]));

        let found = [];
        try {
            const data = await apiRequest('/api/settings/search?q=' + encodeURIComponent(trimmed));
            found = (data.results || []).map(settingItem);
        } catch (e) {
            found = [];
        }
        if (token !== searchToken) return;   // a newer keystroke already won

        const askItem = {
            title: `Ask: "${trimmed}"`,
            sub: 'Let the assistant work out which settings this means',
            icon: 'sparkles',
            run: () => { window.location.href = '/settings#ask=' + encodeURIComponent(trimmed); }
        };

        render(local.concat(found, [askItem]));
    }

    function render(next) {
        items = next.filter(Boolean);
        cursor = items.findIndex(i => !i.disabled);
        if (cursor < 0) cursor = 0;
        results.innerHTML = '';

        if (!items.length) {
            const empty = document.createElement('div');
            empty.className = 'palette-empty';
            empty.textContent = 'Nothing matched. Try describing what you want instead.';
            results.appendChild(empty);
            return;
        }

        items.forEach((item, index) => {
            const row = document.createElement('div');
            row.className = 'palette-item';
            row.setAttribute('role', 'option');
            row.setAttribute('aria-selected', index === cursor ? 'true' : 'false');

            const icon = document.createElement('i');
            icon.className = 'icon-lg icon-muted';
            icon.setAttribute('data-lucide', item.icon || 'circle');

            const main = document.createElement('div');
            main.className = 'pi-main';
            const title = document.createElement('div');
            title.className = 'pi-title';
            title.textContent = item.title;
            main.appendChild(title);
            if (item.sub) {
                const sub = document.createElement('div');
                sub.className = 'pi-sub';
                sub.textContent = item.sub;
                main.appendChild(sub);
            }

            row.append(icon, main);
            if (item.value) {
                const value = document.createElement('span');
                value.className = 'pi-value';
                value.textContent = item.value;
                row.appendChild(value);
            }

            if (!item.disabled) {
                row.addEventListener('click', () => choose(index));
                row.addEventListener('mousemove', () => { cursor = index; highlight(); });
            } else {
                row.style.opacity = '0.55';
            }
            results.appendChild(row);
        });

        if (window.lucide) lucide.createIcons({ root: results });
        highlight();
    }

    function highlight() {
        Array.from(results.children).forEach((row, index) =>
            row.setAttribute('aria-selected', index === cursor ? 'true' : 'false'));
        const active = results.children[cursor];
        if (active && active.scrollIntoView) active.scrollIntoView({ block: 'nearest' });
    }

    function move(step) {
        if (!items.length) return;
        let next = cursor;
        for (let i = 0; i < items.length; i++) {
            next = (next + step + items.length) % items.length;
            if (!items[next].disabled) break;
        }
        cursor = next;
        highlight();
    }

    function choose(index) {
        const item = items[index === undefined ? cursor : index];
        if (item && item.run && !item.disabled) item.run();
    }

    /* ── Input handling ──────────────────────────────────────────── */

    let debounce = null;
    input.addEventListener('input', () => {
        clearTimeout(debounce);
        const value = input.value;
        // Page matches are local and instant; the settings query is debounced
        // so typing a sentence is one request, not one per character.
        debounce = setTimeout(() => search(value), 140);
    });

    input.addEventListener('keydown', event => {
        if (event.key === 'ArrowDown') { event.preventDefault(); move(1); }
        else if (event.key === 'ArrowUp') { event.preventDefault(); move(-1); }
        else if (event.key === 'Enter') { event.preventDefault(); choose(); }
    });
})();
