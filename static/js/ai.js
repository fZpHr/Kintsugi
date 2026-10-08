// Analysis tab: the two AI steps (a faithful merge of the decompiler outputs,
// then an interpretation of that merge), their pipeline stages, the summary
// card and auto-run. The analysis runs as a job on the server: this page only
// starts it and follows its progress, so leaving or reloading the page neither
// interrupts nor restarts it.

(function () {
    const F = Fusion;
    F.makeEditor('ai_best');
    F.makeEditor('ai_interpreted');

    const POLL_INTERVAL = 2500;
    const RUNNING = ['merging', 'interpreting'];

    const button = document.getElementById('ai_generate');
    const buttonLabel = document.getElementById('ai_generate_label');
    const autorun = document.getElementById('ai_autorun');
    autorun.checked = F.store.get('autorun', true);
    autorun.addEventListener('change', () => {
        F.store.set('autorun', autorun.checked);
        if (!result) showIdle();
        maybeAutorun();
    });

    // Analysis of the current binary as returned by the API: {status,
    // step_elapsed, error, best, interpreted, model, decompilers, merge_time,
    // interpret_time, updated}, or null when it was never run.
    let result = null;
    let loaded = false;
    // Whether this page already asked for a run, so auto-run fires only once.
    let requested = false;
    let token = 0;
    let pollTimer = null;
    let clock = null;
    // performance.now() at which the step in progress started.
    let stepStart = null;

    const isRunning = () => !!result && RUNNING.includes(result.status);

    // --- Overlays on top of the two editors ----------------------------------
    const ICONS = {
        waiting: 'fa-hourglass-half', running: 'fa-circle-notch', blocked: 'fa-link',
        error: 'fa-triangle-exclamation', idle: 'fa-play', settings: 'fa-key',
    };

    function overlay(id, kind, title, sub, action) {
        const el = document.getElementById(id);
        el.replaceChildren();
        if (!kind) return;
        el.dataset.kind = kind;

        const box = document.createElement('div');
        const icon = document.createElement('div');
        icon.className = 'overlay-icon';
        icon.innerHTML = `<i class="fas ${ICONS[kind]}"></i>`;
        const titleEl = document.createElement('div');
        titleEl.className = 'overlay-title';
        titleEl.textContent = title;
        const subEl = document.createElement('div');
        subEl.className = 'overlay-sub';
        subEl.textContent = sub || '';
        box.append(icon, titleEl, subEl);

        if (action) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'btn';
            btn.textContent = action.label;
            btn.addEventListener('click', action.onClick);
            box.append(btn);
        }
        el.append(box);
    }

    function setSub(id, text) {
        const sub = document.querySelector(`#${id} .overlay-sub`);
        if (sub) sub.textContent = text;
    }

    // --- State shown when there is no result (yet) ----------------------------
    function showIdle() {
        const p = F.state.progress || {done: 0, total: 0, succeeded: 0};
        overlay('overlay_interpret', 'blocked', 'Interpretation', 'Starts once the merge is ready.');
        F.setStage('interpret', 'idle', '–');

        if (!F.aiAvailable()) {
            overlay('overlay_merge', 'settings', 'No AI provider configured',
                'Add an API key (Gemini, Claude or any OpenAI-compatible API) to merge and interpret the decompiler outputs.',
                {label: 'Open settings', onClick: F.openSettings});
            F.setStage('merge', 'idle', 'No API key');
        } else if (p.done < p.total) {
            overlay('overlay_merge', 'waiting', `Waiting for the decompilers (${p.done}/${p.total})`,
                autorun.checked
                    ? 'The analysis starts automatically once they are all done.'
                    : 'Run the analysis once they are done, or now with the outputs already available.');
            F.setStage('merge', 'idle', autorun.checked ? 'Starts when decompiled' : 'Not started');
        } else if (p.succeeded === 0) {
            overlay('overlay_merge', 'error', 'Nothing to merge', 'Every decompiler failed on this binary.');
            F.setStage('merge', 'idle', 'Nothing to merge');
        } else {
            overlay('overlay_merge', 'idle', 'Ready',
                `${p.succeeded} decompiler outputs are available. Run the analysis to merge them.`);
            F.setStage('merge', 'idle', 'Ready');
        }
    }

    function updateButton() {
        const p = F.state.progress || {succeeded: 0};
        const running = isRunning();
        button.disabled = running || !loaded || !p.succeeded || !F.aiAvailable();
        button.classList.toggle('running', running);
        button.querySelector('i').className = running ? 'fas fa-circle-notch' : 'fas fa-play';
        buttonLabel.textContent = running ? 'Running…' : (result ? 'Re-run analysis' : 'Run analysis');
        button.title = F.aiAvailable() ? `AI provider: ${F.aiLabel()}` : 'Add an API key in Settings first';
    }

    // --- Summary card ------------------------------------------------------------
    function jumpTo(line) {
        F.showTab('ai');
        const editor = F.editors.ai_best;
        editor.scrollToLine(line, true, true, () => {});
        editor.gotoLine(line + 1, 0, true);
        editor.focus();
    }

    // Leading /* ... */ block of the interpretation, without the " * " gutters.
    function summaryOf(text) {
        const match = /^\s*\/\*([\s\S]*?)\*\//.exec(text || '');
        if (!match) return '';
        return match[1].split('\n')
            .map((line) => line.replace(/^\s*\*\s?/, '').replace(/\s+$/, ''))
            .join('\n').trim();
    }

    // `/* from: ... */` lines of the merge, with the function each one heads.
    function provenanceOf(text) {
        const lines = text.split('\n');
        const items = [];
        lines.forEach((line, i) => {
            const from = /^\s*\/\*\s*from:\s*(.*?)\s*\*\/\s*$/.exec(line);
            if (!from) return;
            for (let j = i + 1; j < Math.min(lines.length, i + 4); j++) {
                const fn = /([A-Za-z_$][\w$]*)\s*\(/.exec(lines[j]);
                if (fn) {
                    items.push({fn: fn[1], src: from[1], line: j});
                    break;
                }
            }
        });
        return items;
    }

    function notesOf(text) {
        const items = [];
        text.split('\n').forEach((line, i) => {
            const note = /\/\*\s*NOTE:\s*(.*?)\s*\*\//.exec(line);
            if (note) items.push({note: note[1], line: i});
        });
        return items;
    }

    function chip(className, onClick) {
        const el = document.createElement('button');
        el.type = 'button';
        el.className = className;
        el.addEventListener('click', onClick);
        return el;
    }

    function renderInsights() {
        const box = document.getElementById('ai_insights');
        if (!result || !result.best) {
            box.hidden = true;
            return;
        }
        box.hidden = false;

        const meta = [result.model];
        if (result.decompilers) meta.push(`${result.decompilers.length} sources`);
        if (result.merge_time != null) meta.push(`merge ${F.seconds(result.merge_time)}`);
        if (result.interpret_time != null) meta.push(`interpretation ${F.seconds(result.interpret_time)}`);
        if (result.updated) meta.push(F.timeAgo(result.updated));
        document.getElementById('ai_meta').textContent = meta.filter(Boolean).join(' · ');

        const summary = summaryOf(result.interpreted);
        const summaryEl = document.getElementById('ai_summary');
        const toggle = document.getElementById('ai_summary_toggle');
        summaryEl.hidden = !summary;
        summaryEl.textContent = summary;
        summaryEl.classList.add('collapsed');
        // Only offer to expand when the summary is actually cut.
        toggle.hidden = !summary || summaryEl.scrollHeight <= summaryEl.clientHeight + 4;
        toggle.textContent = 'Show full summary';

        const provenance = provenanceOf(result.best);
        document.getElementById('ai_provenance').replaceChildren(...provenance.map((item) => {
            const el = chip('pchip', () => jumpTo(item.line));
            el.title = `from: ${item.src}`;
            const fn = document.createElement('code');
            fn.textContent = item.fn;
            const src = document.createElement('span');
            src.className = 'src';
            src.textContent = item.src.split(/[,(]/)[0].trim();
            el.append(fn, src);
            return el;
        }));
        document.getElementById('ai_provenance_row').hidden = !provenance.length;

        const notes = notesOf(result.best);
        document.getElementById('ai_notes').replaceChildren(...notes.map((item) => {
            const el = chip('pchip pchip-note', () => jumpTo(item.line));
            el.title = item.note;
            el.innerHTML = '<i class="fas fa-code-compare"></i>';
            const text = document.createElement('span');
            text.textContent = item.note;
            el.append(text);
            return el;
        }));
        document.getElementById('ai_notes_row').hidden = !notes.length;
    }

    document.getElementById('ai_summary_toggle').addEventListener('click', (e) => {
        const collapsed = document.getElementById('ai_summary').classList.toggle('collapsed');
        e.currentTarget.textContent = collapsed ? 'Show full summary' : 'Collapse summary';
    });

    // --- Showing the analysis ------------------------------------------------------
    function tick() {
        if (!isRunning() || stepStart === null) return;
        const merging = result.status === 'merging';
        const secs = F.seconds((performance.now() - stepStart) / 1000);
        F.setStage(merging ? 'merge' : 'interpret', 'active', `Running · ${secs}`);
        const label = merging ? 'Merging the decompiler outputs' : 'Renaming and annotating the merge';
        setSub(merging ? 'overlay_merge' : 'overlay_interpret', `${label} · ${secs}`);
    }

    function showMergeDone() {
        overlay('overlay_merge', null);
        const sub = [];
        if (result.merge_time != null) sub.push(F.seconds(result.merge_time));
        if (result.decompilers) sub.push(`${result.decompilers.length} sources`);
        F.setStage('merge', 'done', sub.join(' · ') || 'Done');
    }

    // Render `result`; `fresh` when its status just changed.
    function render(fresh) {
        const status = result.status;
        if (fresh) {
            F.setText('ai_best', result.best || '');
            F.setText('ai_interpreted', result.interpreted || '');
            stepStart = RUNNING.includes(status)
                ? performance.now() - (result.step_elapsed || 0) * 1000
                : null;
        }

        if (status === 'merging') {
            overlay('overlay_merge', 'running', 'Merging', '');
            overlay('overlay_interpret', 'blocked', 'Interpretation', 'Starts once the merge is ready.');
            F.setStage('interpret', 'idle', 'Queued');
        } else if (result.best) {
            showMergeDone();
        }

        if (status === 'interpreting') {
            overlay('overlay_interpret', 'running', 'Interpreting', '');
        } else if (status === 'done') {
            overlay('overlay_interpret', null);
            F.setStage('interpret', 'done', result.interpret_time != null ? F.seconds(result.interpret_time) : 'Done');
        } else if (status === 'failed') {
            const merged = !!result.best;
            F.setStage(merged ? 'interpret' : 'merge', 'error', 'Failed');
            overlay(merged ? 'overlay_interpret' : 'overlay_merge', 'error',
                merged ? 'The interpretation failed' : 'The merge failed',
                result.error || 'Unknown error.', {
                    label: merged ? 'Retry the interpretation' : 'Retry',
                    onClick: () => run(merged ? 'interpret' : 'all'),
                });
            if (!merged) overlay('overlay_interpret', 'blocked', 'Interpretation', 'Needs the merge.');
        }

        renderInsights();
        tick();
        updateButton();
    }

    function apply(data, myToken) {
        if (myToken !== token) return;
        const fresh = !result || result.status !== data.status || result.updated !== data.updated;
        result = data;
        if (fresh) render(true);
        clearTimeout(pollTimer);
        if (isRunning()) pollTimer = setTimeout(() => poll(myToken), POLL_INTERVAL);
    }

    async function poll(myToken) {
        try {
            const resp = await fetch(`${F.API}binaries/${F.state.binaryId}/decompilations/ai_analysis/`);
            if (resp.ok) return apply(await resp.json(), myToken);
        } catch (err) {
            console.error(err);
        }
        // Network hiccup: keep following the job.
        if (myToken === token) pollTimer = setTimeout(() => poll(myToken), POLL_INTERVAL);
    }

    async function run(step = 'all') {
        if (isRunning() || !F.state.binaryId) return;
        if (!F.aiAvailable()) {
            F.openSettings();
            return;
        }
        requested = true;
        const myToken = token;
        button.disabled = true;
        try {
            const data = await F.postJSON(`binaries/${F.state.binaryId}/decompilations/ai_analysis/`,
                                          {step, ...F.aiPayload()});
            apply(data, myToken);
        } catch (err) {
            if (myToken !== token) return;
            F.toast(err.message, 'error');
            updateButton();
        }
    }

    function maybeAutorun() {
        const p = F.state.progress;
        if (autorun.checked && loaded && !result && !requested && F.aiAvailable()
            && p && p.total && p.done === p.total && p.succeeded > 0) {
            run();
        }
    }

    button.addEventListener('click', () => run());

    // --- Wiring to the workspace -----------------------------------------------------
    async function onBinary() {
        const myToken = ++token;
        clearTimeout(pollTimer);
        result = null;
        loaded = false;
        requested = false;
        stepStart = null;
        F.setText('ai_best', '');
        F.setText('ai_interpreted', '');
        renderInsights();
        overlay('overlay_merge', 'waiting', 'Loading…', '');
        overlay('overlay_interpret', null);
        updateButton();

        let data = null;
        try {
            const resp = await fetch(`${F.API}binaries/${F.state.binaryId}/decompilations/ai_analysis/`);
            if (resp.ok) data = await resp.json();
        } catch (err) {
            console.error(err);
        }
        if (myToken !== token) return;
        loaded = true;
        if (data) {
            apply(data, myToken);
        } else {
            showIdle();
            updateButton();
            maybeAutorun();
        }
    }

    clock = setInterval(tick, 1000);

    document.addEventListener('fusion:binary', onBinary);
    document.addEventListener('fusion:progress', () => {
        if (loaded && !result) showIdle();
        updateButton();
        maybeAutorun();
    });
    document.addEventListener('fusion:settings', () => {
        if (loaded && !result) showIdle();
        updateButton();
        maybeAutorun();
    });

    // index.js may already have loaded a binary before this script ran.
    if (F.state.binaryId) onBinary();
})();
