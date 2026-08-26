/**
 * Activity drawer: comments, reactions, and uploads. Deletions are omitted.
 */

const Activity = {
    items: [],
    loading: false,

    elements: {
        toggle: null,
        panel: null,
        close: null,
        backdrop: null,
        list: null,
        empty: null,
        loadingEl: null,
        error: null,
        retry: null,
    },

    lastFocused: null,

    init() {
        this.elements.toggle = document.getElementById('activity-toggle');
        this.elements.panel = document.getElementById('activity-panel');
        this.elements.close = document.getElementById('activity-close');
        this.elements.backdrop = document.getElementById('filter-backdrop');
        this.elements.list = document.getElementById('activity-list');
        this.elements.empty = document.getElementById('activity-empty');
        this.elements.loadingEl = document.getElementById('activity-loading');
        this.elements.error = document.getElementById('activity-error');
        this.elements.retry = document.getElementById('activity-retry');

        if (!this.elements.toggle || !this.elements.panel) return;

        this.elements.toggle.addEventListener('click', () => this.toggle());
        this.elements.close?.addEventListener('click', () => this.hide());
        this.elements.retry?.addEventListener('click', () => this.load());
    },

    isOpen() {
        return Boolean(this.elements.panel && !this.elements.panel.hidden);
    },

    toggle() {
        if (this.isOpen()) {
            this.hide();
        } else {
            this.show();
        }
    },

    show() {
        // Capture focus before closing the other drawer, which would otherwise
        // steal document.activeElement out from under us.
        this.lastFocused = document.activeElement;
        window.Filters?.hidePanel();
        this.elements.panel.hidden = false;
        this.elements.toggle?.setAttribute('aria-expanded', 'true');
        if (this.elements.backdrop) this.elements.backdrop.hidden = false;
        // Move focus into the panel so keyboard/screen-reader users land on
        // the close button rather than whatever the drawer now covers.
        (this.elements.close || this.elements.panel).focus();
        this.load();
    },

    hide() {
        if (!this.isOpen()) return;
        if (this.elements.panel) this.elements.panel.hidden = true;
        this.elements.toggle?.setAttribute('aria-expanded', 'false');
        const filtersOpen = !document.getElementById('filter-panel')?.hidden;
        if (this.elements.backdrop && !filtersOpen) {
            this.elements.backdrop.hidden = true;
        }
        const restoreFocus = this.lastFocused;
        this.lastFocused = null;
        if (restoreFocus && document.body.contains(restoreFocus)) {
            restoreFocus.focus();
        } else {
            this.elements.toggle?.focus();
        }
    },

    async load() {
        if (this.loading) return;
        this.loading = true;
        this.setStatus({ loading: true });

        try {
            const data = await API.getActivity(50);
            this.items = data.items || [];
            this.render();
            this.setStatus({ loading: false, empty: this.items.length === 0 });
        } catch (error) {
            console.error('Failed to load activity:', error);
            this.items = [];
            this.setStatus({ loading: false, error: true });
        } finally {
            this.loading = false;
        }
    },

    setStatus({ loading = false, empty = false, error = false } = {}) {
        if (this.elements.loadingEl) this.elements.loadingEl.hidden = !loading;
        if (this.elements.empty) this.elements.empty.hidden = loading || error || !empty;
        if (this.elements.error) this.elements.error.hidden = !error;
        if (this.elements.list) this.elements.list.hidden = loading || error || empty;
    },

    render() {
        const list = this.elements.list;
        if (!list) return;
        list.innerHTML = '';

        this.items.forEach((item) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'activity-item';

            const img = document.createElement('img');
            img.className = 'activity-thumb';
            img.alt = '';
            img.src = API.getThumbnailUrl(item.assetId, 'thumbnail');

            const body = document.createElement('div');
            body.className = 'activity-copy';
            body.innerHTML = `
                <p class="activity-text">${this.itemHtml(item)}</p>
                <time class="activity-when">${escapeHtml(this.relativeTime(item.createdAt))}</time>
            `;

            btn.appendChild(img);
            btn.appendChild(body);
            btn.addEventListener('click', () => this.openAsset(item.assetId));
            list.appendChild(btn);
        });
    },

    itemHtml(item) {
        const name = escapeHtml(item.displayName || 'Someone');
        if (item.type === 'comment') {
            const snippet = escapeHtml(item.body || '');
            return `<strong>${name}</strong> commented <span class="activity-snippet">${snippet}</span>`;
        }
        if (item.type === 'reaction') {
            return `<strong>${name}</strong> reacted ${escapeHtml(item.emoji || '')}`;
        }
        const kind = item.assetType === 'VIDEO' ? 'video' : 'photo';
        return `New ${kind} uploaded`;
    },

    relativeTime(unix) {
        if (!unix) return '';
        const seconds = Math.max(0, Date.now() / 1000 - unix);
        if (seconds < 60) return 'just now';
        if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
        if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
        if (seconds < 86400 * 7) return `${Math.floor(seconds / 86400)}d ago`;
        return new Date(unix * 1000).toLocaleDateString(undefined, {
            month: 'short',
            day: 'numeric',
            year: 'numeric',
        });
    },

    async openAsset(assetId) {
        const assets = State.getProperty('assets') || [];
        const index = assets.findIndex((asset) => asset.id === assetId);
        if (index >= 0) {
            Lightbox.open(assets[index], index);
            return;
        }
        try {
            const asset = await API.getAsset(assetId);
            Lightbox.open(asset, -1);
        } catch (error) {
            console.error('Failed to open activity asset:', error);
            alert(error.message || 'Could not open photo');
        }
    },
};

window.Activity = Activity;
