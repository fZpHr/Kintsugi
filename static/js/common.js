// Helpers shared by every page: per-viewer storage, theme, Ace editors,
// copy / download buttons, the Settings dialog (AI provider and key), pipeline
// stages and toasts. Exposed as `Fusion`.

window.Fusion = (function () {
    const config = JSON.parse(document.getElementById('fusion_config').textContent);

    // --- Per-viewer storage (never required for the page to work) --------
    const store = {
        get(key, fallback = null) {
            try {
                const raw = localStorage.getItem('fusion:' + key);
                return raw === null ? fallback : JSON.parse(raw);
            } catch (e) {
                return fallback;
            }
        },
        set(key, value) {
            try {
                localStorage.setItem('fusion:' + key, JSON.stringify(value));
            } catch (e) {
                // Storage full or blocked: the value just won't be remembered.
            }
        },
        remove(key) {
            try {
                localStorage.removeItem('fusion:' + key);
            } catch (e) {}
        },
    };

    // API root, relative to the page so the app also works under a sub-path.
    const API = location.pathname.replace(/[^/]*$/, '') + 'api/';

    // Shared state, filled in by index.js.
    const state = {binaryId: null, binaryName: null};

    function csrf() {
        const input = document.querySelector('[name=csrfmiddlewaretoken]');
        return input ? input.value : '';
    }

    // POST JSON to the API; resolves with the parsed answer, or rejects with
    // an Error carrying the server's message.
    async function postJSON(path, payload, method = 'POST') {
        const resp = await fetch(API + path, {
            method,
            headers: {'X-CSRFToken': csrf(), 'Content-Type': 'application/json'},
            mode: 'same-origin',
            body: payload === undefined ? undefined : JSON.stringify(payload),
        });
        if (resp.status === 204) return null;
        let data;
        try {
            data = await resp.json();
        } catch (e) {
            throw Error(`Unexpected server response (HTTP ${resp.status}).`);
        }
        if (!resp.ok) throw Error(data.error || data.detail || `Request failed (HTTP ${resp.status}).`);
        return data;
    }

    // --- Theme -------------------------------------------------------------
    const editors = {};

    function theme() {
        return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
    }

    function aceTheme() {
        return theme() === 'light' ? 'ace/theme/tomorrow' : 'ace/theme/tomorrow_night';
    }

    document.getElementById('theme_toggle').addEventListener('click', () => {
        const next = theme() === 'light' ? 'dark' : 'light';
        document.documentElement.dataset.theme = next;
        store.set('theme', next);
        Object.values(editors).forEach((editor) => editor.setTheme(aceTheme()));
    });

    // --- AI settings ---------------------------------------------------------
    // {provider: 'server' | 'gemini' | 'anthropic' | 'openai', apiKey, model, baseUrl}
    function aiSettings() {
        const saved = store.get('ai_settings');
        if (saved && saved.provider !== 'server' && saved.apiKey && config.allowBrowserKeys) {
            return saved;
        }
        return {provider: 'server'};
    }

    // Is there an AI provider to run the analysis with?
    function aiAvailable() {
        return aiSettings().provider !== 'server' || !!config.aiServer;
    }

    // Fields to add to an AI request so that it uses the browser's settings.
    function aiPayload(settings = aiSettings()) {
        if (settings.provider === 'server') return {};
        return {
            provider: settings.provider,
            api_key: settings.apiKey,
            model: settings.model || '',
            base_url: settings.provider === 'openai' ? (settings.baseUrl || '') : '',
        };
    }

    function aiLabel(settings = aiSettings()) {
        if (settings.provider === 'server') {
            return config.aiServer ? `${config.aiServer.label} (server)` : 'None';
        }
        return config.providers[settings.provider];
    }

    // --- Settings dialog -------------------------------------------------------
    const dialog = document.getElementById('settings_dialog');
    const providerSelect = document.getElementById('ai_provider');
    const keyFields = document.getElementById('ai_key_fields');
    const keyInput = document.getElementById('ai_api_key');
    const modelInput = document.getElementById('ai_model');
    const baseUrlInput = document.getElementById('ai_base_url');
    const testResult = document.getElementById('ai_test_result');

    function option(value, label) {
        const el = document.createElement('option');
        el.value = value;
        el.textContent = label;
        return el;
    }

    if (config.aiServer) {
        providerSelect.append(option('server', `Server default: ${config.aiServer.label} (${config.aiServer.model})`));
    } else {
        providerSelect.append(option('server', 'Server default: not configured'));
    }
    if (config.allowBrowserKeys) {
        Object.entries(config.providers).forEach(([value, label]) => {
            providerSelect.append(option(value, `${label}, with my API key`));
        });
    }
    baseUrlInput.placeholder = config.defaultBaseUrl;
    baseUrlInput.disabled = !config.allowCustomBaseUrl;

    function formSettings() {
        return {
            provider: providerSelect.value,
            apiKey: keyInput.value.trim(),
            model: modelInput.value.trim(),
            baseUrl: baseUrlInput.value.trim(),
        };
    }

    function updateForm() {
        const provider = providerSelect.value;
        const browser = provider !== 'server';
        keyFields.hidden = !browser;
        document.getElementById('ai_base_url_field').hidden = provider !== 'openai';
        const fallback = config.defaultModels[provider];
        modelInput.placeholder = fallback ? `${fallback} (default)` : 'Model name (required)';

        const note = document.getElementById('ai_provider_note');
        if (!browser) {
            note.textContent = config.aiServer
                ? "Uses the key set in this server's .env file."
                : 'No key is set on this server: choose a provider and add your own key.';
        } else if (provider === 'openai') {
            note.textContent = 'Any API compatible with OpenAI chat completions: OpenAI, Mistral, OpenRouter, Groq, DeepSeek, Ollama...';
        } else {
            note.textContent = '';
        }
        testResult.textContent = '';
        testResult.className = 'test-result';
    }

    function openSettings() {
        const saved = store.get('ai_settings') || {};
        const current = aiSettings();
        providerSelect.value = config.allowBrowserKeys && saved.provider ? saved.provider : current.provider;
        keyInput.value = saved.apiKey || '';
        keyInput.type = 'password';
        modelInput.value = saved.model || '';
        baseUrlInput.value = saved.baseUrl || '';
        updateForm();
        dialog.showModal();
    }

    providerSelect.addEventListener('change', updateForm);
    document.getElementById('settings_open').addEventListener('click', openSettings);
    document.addEventListener('click', (e) => {
        if (e.target.closest('[data-open-settings]')) openSettings();
    });
    dialog.querySelectorAll('[data-close]').forEach((el) => el.addEventListener('click', () => dialog.close()));

    document.getElementById('ai_api_key_toggle').addEventListener('click', () => {
        keyInput.type = keyInput.type === 'password' ? 'text' : 'password';
    });

    document.getElementById('settings_form').addEventListener('submit', (e) => {
        const settings = formSettings();
        if (settings.provider !== 'server' && !settings.apiKey) {
            e.preventDefault();
            keyInput.focus();
            testResult.className = 'test-result error';
            testResult.textContent = 'Enter an API key, or choose the server default.';
            return;
        }
        if (settings.provider === 'openai' && !settings.model && !config.defaultModels.openai) {
            e.preventDefault();
            modelInput.focus();
            testResult.className = 'test-result error';
            testResult.textContent = 'Enter the model to use.';
            return;
        }
        store.set('ai_settings', settings);
        toast(`AI provider: ${aiLabel()}.`);
        document.dispatchEvent(new CustomEvent('fusion:settings'));
    });

    document.getElementById('settings_clear').addEventListener('click', () => {
        store.remove('ai_settings');
        keyInput.value = '';
        modelInput.value = '';
        baseUrlInput.value = '';
        providerSelect.value = 'server';
        updateForm();
        document.dispatchEvent(new CustomEvent('fusion:settings'));
        toast('The saved API key was removed from this browser.');
    });

    document.getElementById('ai_test').addEventListener('click', async (e) => {
        const button = e.currentTarget;
        const settings = formSettings();
        if (settings.provider !== 'server' && !settings.apiKey) {
            testResult.className = 'test-result error';
            testResult.textContent = 'Enter an API key first.';
            return;
        }
        button.disabled = true;
        testResult.className = 'test-result';
        testResult.textContent = 'Testing...';
        try {
            const data = await postJSON('ai/test', aiPayload(settings));
            testResult.className = 'test-result ok';
            testResult.textContent = `Connected: ${data.model}, ${seconds(data.seconds)}`;
        } catch (err) {
            testResult.className = 'test-result error';
            testResult.textContent = err.message;
        } finally {
            button.disabled = false;
        }
    });

    // --- Ace editors ---------------------------------------------------------
    function makeEditor(id) {
        const editor = ace.edit(id);
        editor.setReadOnly(true);
        editor.setShowPrintMargin(false);
        editor.setHighlightActiveLine(true);
        editor.setHighlightGutterLine(true);
        editor.setTheme(aceTheme());
        editor.setOptions({
            fontFamily: "'JetBrains Mono', ui-monospace, monospace",
            fontSize: '12.5px',
        });
        editor.session.setMode('ace/mode/c_cpp');
        editors[id] = editor;
        return editor;
    }

    function setText(id, text) {
        const editor = editors[id];
        editor.session.getDocument().setValue(text);
        editor.resize();
    }

    function resizeEditors(root) {
        root.querySelectorAll('.editor').forEach((el) => {
            if (editors[el.id]) editors[el.id].resize();
        });
    }

    // --- Copy / download buttons ([data-copy] / [data-download]) ------------
    function flash(button) {
        const icon = button.querySelector('i');
        const previous = icon.className;
        icon.className = 'fas fa-check';
        button.classList.add('done');
        setTimeout(() => {
            icon.className = previous;
            button.classList.remove('done');
        }, 1200);
    }

    document.addEventListener('click', (e) => {
        const copy = e.target.closest('[data-copy]');
        if (copy && editors[copy.dataset.copy]) {
            navigator.clipboard.writeText(editors[copy.dataset.copy].getValue())
                .then(() => flash(copy))
                .catch(() => toast('Copy failed: the clipboard is not available here.', 'error'));
            return;
        }
        const download = e.target.closest('[data-download]');
        if (download && editors[download.dataset.download]) {
            const text = editors[download.dataset.download].getValue();
            const name = (state.binaryName || 'binary').replace(/[^\w.-]+/g, '_');
            const link = document.createElement('a');
            link.href = URL.createObjectURL(new Blob([text], {type: 'text/x-c'}));
            link.download = `${name}.${download.dataset.suffix || 'decompiled'}.c`;
            link.click();
            URL.revokeObjectURL(link.href);
            flash(download);
        }
    });

    // --- Pipeline stages -----------------------------------------------------
    function setStage(name, status, sub) {
        const stage = document.getElementById('stage_' + name);
        if (!stage) return;
        stage.dataset.status = status;
        if (sub !== undefined) {
            document.getElementById(`stage_${name}_sub`).textContent = sub;
        }
    }

    // --- Formatting & toasts -------------------------------------------------
    function seconds(secs) {
        secs = Math.round(secs);
        return secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m ${String(secs % 60).padStart(2, '0')}s`;
    }

    function bytes(n) {
        if (n < 1024) return `${n} B`;
        if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
        return `${(n / 1024 / 1024).toFixed(2)} MB`;
    }

    function timeAgo(date) {
        const secs = (Date.now() - new Date(date).getTime()) / 1000;
        if (secs < 60) return 'just now';
        if (secs < 3600) return `${Math.floor(secs / 60)} min ago`;
        if (secs < 86400) return `${Math.floor(secs / 3600)} h ago`;
        if (secs < 7 * 86400) return `${Math.floor(secs / 86400)} d ago`;
        return new Date(date).toLocaleDateString();
    }

    function toast(message, kind) {
        const el = document.createElement('div');
        el.className = 'toast' + (kind ? ` toast-${kind}` : '');
        el.textContent = message;
        document.getElementById('toasts').append(el);
        setTimeout(() => el.remove(), 6000);
    }

    return {config, store, API, state, csrf, postJSON, aiSettings, aiAvailable, aiPayload, aiLabel,
            openSettings, makeEditor, setText, resizeEditors, setStage, seconds, bytes, timeAgo, toast,
            editors};
})();
