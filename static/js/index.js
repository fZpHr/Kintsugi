// Workspace: binary upload, live decompiler results, tabs and the first two
// pipeline stages. The AI stages live in ai.js, which listens to the
// `fusion:binary` and `fusion:progress` events dispatched here.

ace.config.set('basePath', 'https://cdnjs.cloudflare.com/ajax/libs/ace/1.4.14/');

(function () {
    const F = Fusion;
    const decompilers = JSON.parse(document.getElementById('decompilers_json').textContent);
    const names = Object.keys(decompilers);
    const workspace = document.getElementById('workspace');
    const fileInput = document.getElementById('file');

    const panes = {};
    const chips = {};
    document.querySelectorAll('.decompiler-pane').forEach((p) => { panes[p.dataset.decompiler] = p; });
    document.querySelectorAll('.dchip').forEach((c) => { chips[c.dataset.decompiler] = c; });
    names.forEach((name) => F.makeEditor(name));

    // --- Tabs ----------------------------------------------------------------
    const tabs = document.querySelectorAll('#main_tabs .tab');

    function showTab(name) {
        tabs.forEach((tab) => tab.classList.toggle('active', tab.dataset.tab === name));
        document.querySelectorAll('[data-tab-panel]').forEach((panel) => {
            panel.hidden = panel.dataset.tabPanel !== name;
            if (!panel.hidden) F.resizeEditors(panel);
        });
    }
    F.showTab = showTab;

    tabs.forEach((tab) => tab.addEventListener('click', () => showTab(tab.dataset.tab)));
    showTab(F.aiAvailable() ? 'ai' : 'decompilers');

    // Home page notice when no AI provider is set up yet.
    const aiNotice = document.getElementById('ai_notice');
    const updateNotice = () => { aiNotice.hidden = F.aiAvailable(); };
    document.addEventListener('fusion:settings', updateNotice);
    updateNotice();

    // --- Showing / hiding decompilers ----------------------------------------
    const hidden = new Set(F.store.get('hidden_decompilers', []));

    function applyVisibility() {
        names.forEach((name) => {
            panes[name].hidden = hidden.has(name);
            chips[name].classList.toggle('off', hidden.has(name));
        });
        F.resizeEditors(document.getElementById('tab_decompilers'));
    }

    names.forEach((name) => {
        chips[name].addEventListener('click', () => {
            hidden.has(name) ? hidden.delete(name) : hidden.add(name);
            F.store.set('hidden_decompilers', [...hidden]);
            applyVisibility();
        });
    });
    applyVisibility();

    // --- Results -------------------------------------------------------------
    // Latest result per decompiler name for the current binary.
    let results = {};
    let pollTimer = null;
    let waitTimer = null;
    let pollToken = 0;
    // Cursor moves caused by loading text must not be written to the URL hash.
    const loading = {};

    function setStatus(name, status, detail) {
        panes[name].dataset.status = status;
        chips[name].dataset.status = status;
        chips[name].querySelector('.dchip-time').textContent = detail || '';
    }

    function setText(name, text) {
        loading[name] = true;
        F.setText(name, text);
        loading[name] = false;
    }

    function emitProgress() {
        const done = Object.keys(results).length;
        const failed = Object.values(results).filter((r) => r.error !== null).length;
        const total = names.length;

        const succeeded = done - failed;

        // Count successes, not finished runs: "9/9" must mean all of them worked.
        const badge = document.getElementById('decompilers_badge');
        badge.textContent = `${succeeded}/${total}`;
        badge.title = `${succeeded} succeeded, ${failed} failed, ${total - done} pending`;
        document.getElementById('decompile_progress').style.width = total ? `${(100 * done) / total}%` : '0';
        let sub = done < total ? `${done}/${total} finished` : `${succeeded}/${total} succeeded`;
        if (failed) sub += ` · ${failed} failed`;
        F.setStage('decompile', done < total ? 'active' : (succeeded ? 'done' : 'error'), sub);

        F.state.progress = {done, total, failed, succeeded};
        document.dispatchEvent(new CustomEvent('fusion:progress', {detail: F.state.progress}));
    }

    // Is version string `a` older than `b`?
    function olderVersion(a, b) {
        const pa = a.split(/[.-]/);
        const pb = b.split(/[.-]/);
        for (let i = 0; i < Math.min(pa.length, pb.length); i++) {
            const na = parseInt(pa[i]);
            const nb = parseInt(pb[i]);
            const [x, y] = isNaN(na) || isNaN(nb) ? [pa[i], pb[i]] : [na, nb];
            if (x !== y) return x < y;
        }
        return pa.length < pb.length;
    }

    async function fetchAll(url) {
        const items = [];
        while (url) {
            const data = await (await fetch(url)).json();
            items.push(...data.results);
            url = data.next;
        }
        return items;
    }

    async function readResultText(url) {
        const blob = await (await fetch(url)).blob();
        // Runners upload gzip-compressed outputs; pass anything else through.
        try {
            const stream = blob.stream().pipeThrough(new DecompressionStream('gzip'));
            return await new Response(stream).text();
        } catch (e) {
            return await blob.text();
        }
    }

    function showResult(result) {
        const name = result.decompiler.name;
        const pane = panes[name];
        let version = result.decompiler.version;
        if (result.decompiler.revision) version += ` (${result.decompiler.revision.substring(0, 8)})`;
        const time = result.analysis_time == null ? '' : F.seconds(result.analysis_time);
        pane.querySelector('.decompiler-version').textContent = time ? `${version} · ${time}` : version;
        pane.querySelector('[data-rerun]').hidden = false;

        if (result.error !== null) {
            setStatus(name, 'failed', 'failed');
            setText(name, `// ${name} failed: ${result.error}`);
            return;
        }
        setStatus(name, 'done', time);
        readResultText(result.download_url)
            .then((text) => {
                setText(name, text);
                const row = new URLSearchParams(location.hash.substring(1)).get(name);
                if (row !== null) {
                    loading[name] = true;
                    F.editors[name].gotoLine(parseInt(row));
                    loading[name] = false;
                }
            })
            .catch((err) => setText(name, `// Error retrieving the result: ${err}`));
    }

    async function poll(token) {
        pollTimer = null;
        try {
            const items = await fetchAll(`${F.API}binaries/${F.state.binaryId}/decompilations/`);
            if (token !== pollToken) return;
            const latest = {};
            for (const item of items) {
                const name = item.decompiler && item.decompiler.name;
                if (!(name in decompilers)) continue;
                if (!latest[name] || olderVersion(latest[name].decompiler.version, item.decompiler.version)) {
                    latest[name] = item;
                }
            }
            for (const [name, item] of Object.entries(latest)) {
                if (name in results) continue;
                results[name] = item;
                showResult(item);
            }
        } catch (err) {
            console.error(err);
        }
        if (token !== pollToken) return;
        emitProgress();
        if (Object.keys(results).length < names.length) {
            pollTimer = setTimeout(() => poll(token), 3000);
        }
    }

    function startWaitTimer() {
        clearInterval(waitTimer);
        const started = Date.now();
        const tick = () => {
            const pending = names.filter((name) => !(name in results));
            if (!pending.length) {
                clearInterval(waitTimer);
                return;
            }
            const secs = F.seconds((Date.now() - started) / 1000);
            pending.forEach((name) => setText(name, `// Waiting for ${name}... (${secs})`));
        };
        tick();
        waitTimer = setInterval(tick, 1000);
    }

    function startPolling() {
        clearTimeout(pollTimer);
        results = {};
        names.forEach((name) => {
            setStatus(name, 'waiting', '');
            panes[name].querySelector('[data-rerun]').hidden = true;
        });
        startWaitTimer();
        poll(++pollToken);
    }

    function rerun(name) {
        const result = results[name];
        if (!result) return;
        fetch(result.url + 'rerun/', {
            method: 'POST',
            headers: {'X-CSRFToken': F.csrf()},
            mode: 'same-origin',
        })
            .then((resp) => {
                if (!resp.ok) throw Error(`Could not re-run ${name} (HTTP ${resp.status}).`);
                delete results[name];
                setStatus(name, 'waiting', '');
                panes[name].querySelector('[data-rerun]').hidden = true;
                startWaitTimer();
                emitProgress();
                if (pollTimer === null) poll(pollToken);
            })
            .catch((err) => F.toast(err.message, 'error'));
    }

    names.forEach((name) => {
        panes[name].querySelector('[data-rerun]').addEventListener('click', () => rerun(name));
        F.editors[name].session.selection.on('changeCursor', () => {
            if (loading[name]) return;
            const params = new URLSearchParams(location.hash.substring(1));
            params.set(name, F.editors[name].getCursorPosition().row + 1);
            history.replaceState(null, '', '#' + params.toString());
        });
    });

    // --- Binary --------------------------------------------------------------
    function showBinaryInfo(name, size) {
        F.state.binaryName = name || F.state.binaryId.substring(0, 8);
        document.getElementById('binary_name').textContent = F.state.binaryName;
        document.getElementById('binary_meta').textContent = size ? F.bytes(size) : '';
        F.setStage('upload', 'done', size ? F.bytes(size) : 'Uploaded');
    }

    function loadBinary(id) {
        F.state.binaryId = id;
        // What this browser remembers of its own uploads, until the server answers.
        const local = F.store.get('bin:' + id, {});
        showBinaryInfo(local.name, local.size);
        if (F.config.historyEnabled) {
            fetch(`${F.API}history/${id}`)
                .then((resp) => (resp.ok ? resp.json() : null))
                .then((item) => {
                    if (item && F.state.binaryId === id) showBinaryInfo(item.name || local.name, item.size);
                })
                .catch(() => {});
        }

        document.getElementById('hero').hidden = true;
        workspace.hidden = false;
        document.body.classList.add('has-binary');
        document.getElementById('new_binary').hidden = false;
        document.getElementById('binary_info').hidden = false;
        document.getElementById('binary_name').title = id;

        showTab(document.querySelector('#main_tabs .tab.active').dataset.tab);
        F.state.progress = {done: 0, total: names.length, failed: 0, succeeded: 0};
        startPolling();
        document.dispatchEvent(new CustomEvent('fusion:binary', {detail: {id}}));
    }

    async function upload(file) {
        if (!file) return;
        const form = new FormData();
        form.append('file', file);
        F.toast(`Uploading ${file.name}...`);
        try {
            const resp = await fetch(`${F.API}binaries/`, {
                method: 'POST',
                body: form,
                headers: {'X-CSRFToken': F.csrf()},
                mode: 'same-origin',
            });
            if (!resp.ok) {
                throw Error(resp.status === 413 ? 'File too large.' : `Upload failed (HTTP ${resp.status}).`);
            }
            const data = await resp.json();
            F.store.set('bin:' + data.id, {name: file.name, size: file.size});
            const url = new URL(location);
            url.search = `?id=${data.id}`;
            url.hash = '';
            history.pushState({}, '', url);
            loadBinary(data.id);
        } catch (err) {
            F.toast(err.message, 'error');
        }
    }

    fileInput.addEventListener('change', () => {
        upload(fileInput.files[0]);
        fileInput.value = '';
    });

    // Drag & drop a binary anywhere on the page.
    let dragDepth = 0;
    window.addEventListener('dragenter', (e) => {
        if (!e.dataTransfer.types.includes('Files')) return;
        dragDepth++;
        document.body.classList.add('dragging');
    });
    window.addEventListener('dragleave', () => {
        dragDepth = Math.max(0, dragDepth - 1);
        if (!dragDepth) document.body.classList.remove('dragging');
    });
    window.addEventListener('dragover', (e) => e.preventDefault());
    window.addEventListener('drop', (e) => {
        e.preventDefault();
        dragDepth = 0;
        document.body.classList.remove('dragging');
        upload(e.dataTransfer.files[0]);
    });

    window.addEventListener('popstate', () => location.reload());

    const id = new URLSearchParams(location.search).get('id');
    if (id !== null) loadBinary(id);
})();
