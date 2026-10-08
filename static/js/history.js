// History of the analysed binaries: the "Recent analyses" list of the home
// page and the History page. Renders into every [data-history] element
// (data-limit: how many, data-deletable: show delete buttons, data-filter: id
// of a search input filtering the rows).

(function () {
    const F = Fusion;
    if (!F.config.historyEnabled) return;

    const appRoot = F.API.replace(/api\/$/, '');

    function binaryName(item) {
        if (item.name) return item.name;
        // Binaries uploaded before names were recorded: this browser may know it.
        const local = F.store.get('bin:' + item.id);
        return local && local.name ? local.name : '';
    }

    function badge(text, kind, title) {
        const el = document.createElement('span');
        el.className = 'badge' + (kind ? ` badge-${kind}` : '');
        el.textContent = text;
        if (title) el.title = title;
        return el;
    }

    function decompilersBadge(item, total) {
        const skipped = item.skipped || 0;
        const done = item.decompiled + item.failed + skipped;
        if (!done) return badge('Pending');
        // Skipped decompilers can't handle this binary: they don't count.
        const expected = total - skipped;
        const title = skipped ? `${skipped} skipped (binary not supported)` : '';
        if (item.failed) return badge(`${item.decompiled}/${expected} · ${item.failed} failed`, 'warn', title);
        return badge(`${item.decompiled}/${expected}`, done >= total ? 'ok' : null, title);
    }

    function analysisBadge(item) {
        const analysis = item.analysis;
        if (!analysis) return badge('Not run');
        if (analysis.status === 'merging') return badge('Merging…', null, analysis.model);
        if (analysis.status === 'interpreting') return badge('Interpreting…', null, analysis.model);
        if (analysis.status === 'done') return badge('Done', 'ok', analysis.model);
        return badge(analysis.merged ? 'Interpretation failed' : 'Failed', 'err', analysis.model);
    }

    function cell(className, ...children) {
        const td = document.createElement('td');
        if (className) td.className = className;
        td.append(...children);
        return td;
    }

    async function remove(item, row) {
        const name = binaryName(item) || item.id;
        if (!confirm(`Delete "${name}", its decompilations and its analysis? This cannot be undone.`)) return;
        try {
            await F.postJSON(`history/${item.id}`, undefined, 'DELETE');
            row.remove();
            F.toast(`Deleted ${name}.`);
        } catch (err) {
            F.toast(err.message, 'error');
        }
    }

    function render(container, data) {
        const deletable = 'deletable' in container.dataset;
        if (!data.results.length) {
            const empty = document.createElement('p');
            empty.className = 'empty';
            empty.textContent = 'No analyses yet. Upload a binary to start.';
            container.replaceChildren(empty);
            return;
        }

        const table = document.createElement('table');
        table.className = 'history-table';
        const head = table.createTHead().insertRow();
        ['File', 'Uploaded', 'Size', 'Decompilers', 'Analysis', ''].forEach((label) => {
            const th = document.createElement('th');
            th.textContent = label;
            head.append(th);
        });

        const body = table.createTBody();
        for (const item of data.results) {
            const url = `${appRoot}?id=${item.id}`;
            const row = body.insertRow();
            row.dataset.search = `${binaryName(item)} ${item.id}`.toLowerCase();
            row.addEventListener('click', (e) => {
                if (!e.target.closest('a, button')) location.href = url;
            });

            const name = binaryName(item);
            const link = document.createElement('a');
            link.href = url;
            link.textContent = name || 'Unnamed binary';
            if (!name) link.className = 'unnamed';
            const id = document.createElement('small');
            id.textContent = item.id.substring(0, 8);
            const nameBox = document.createElement('div');
            nameBox.className = 'history-name';
            nameBox.append(link, id);

            const uploaded = document.createElement('span');
            uploaded.textContent = F.timeAgo(item.created);
            uploaded.title = new Date(item.created).toLocaleString();

            const actions = [];
            const open = document.createElement('a');
            open.className = 'icon-btn';
            open.href = url;
            open.title = 'Open';
            open.innerHTML = '<i class="fas fa-arrow-right"></i>';
            actions.push(open);
            if (deletable) {
                const del = document.createElement('button');
                del.type = 'button';
                del.className = 'icon-btn danger';
                del.title = 'Delete';
                del.innerHTML = '<i class="fas fa-trash-can"></i>';
                del.addEventListener('click', () => remove(item, row));
                actions.push(del);
            }

            row.append(
                cell('history-col-name', nameBox),
                cell('history-muted', uploaded),
                cell('history-muted history-col-size', item.size == null ? '–' : F.bytes(item.size)),
                cell('', decompilersBadge(item, data.decompilers)),
                cell('', analysisBadge(item)),
                cell('history-actions', ...actions),
            );
        }
        container.replaceChildren(table);

        if (container.dataset.filter) {
            const input = document.getElementById(container.dataset.filter);
            const apply = () => {
                const query = input.value.trim().toLowerCase();
                body.querySelectorAll('tr').forEach((row) => {
                    row.hidden = !!query && !row.dataset.search.includes(query);
                });
            };
            input.addEventListener('input', apply);
            apply();
        }
    }

    document.querySelectorAll('[data-history]').forEach(async (container) => {
        try {
            const resp = await fetch(`${F.API}history?limit=${container.dataset.limit || 50}`);
            if (!resp.ok) throw Error(`HTTP ${resp.status}`);
            render(container, await resp.json());
        } catch (err) {
            const error = document.createElement('p');
            error.className = 'empty';
            error.textContent = `Could not load the history (${err.message}).`;
            container.replaceChildren(error);
        }
    });
})();
