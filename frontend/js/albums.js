/**
 * Album browser: overlay grid of album covers; selecting one filters the
 * gallery to that album via State.albumId.
 */

const Albums = {
    elements: {
        toggle: null,
        modal: null,
        backdrop: null,
        close: null,
        grid: null,
    },

    list: [],
    loaded: false,

    init() {
        this.elements.toggle = document.getElementById('albums-toggle');
        this.elements.modal = document.getElementById('albums-modal');
        this.elements.backdrop = document.getElementById('albums-backdrop');
        this.elements.close = document.getElementById('albums-close');
        this.elements.grid = document.getElementById('albums-grid');

        if (!this.elements.toggle) return;

        this.elements.toggle.addEventListener('click', () => this.open());
        this.elements.close?.addEventListener('click', () => this.close());
        this.elements.backdrop?.addEventListener('click', () => this.close());
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.elements.modal && !this.elements.modal.hidden) {
                this.close();
            }
        });
    },

    async open() {
        this.elements.modal.hidden = false;
        this.elements.backdrop.hidden = false;
        if (!this.loaded) {
            await this.load();
        }
    },

    close() {
        this.elements.modal.hidden = true;
        this.elements.backdrop.hidden = true;
    },

    async load() {
        this.setStatus('Loading albums…');
        try {
            const data = await API.getAlbums();
            this.list = data.albums || [];
            State.set({ albums: this.list });
            this.loaded = true;
            this.render();
        } catch (error) {
            console.error('Failed to load albums:', error);
            this.setStatus('Failed to load albums');
        }
    },

    setStatus(message) {
        this.elements.grid.innerHTML = '';
        const status = document.createElement('div');
        status.className = 'loading-placeholder';
        status.textContent = message;
        this.elements.grid.appendChild(status);
    },

    render() {
        if (this.list.length === 0) {
            this.setStatus('No albums');
            return;
        }

        const grid = this.elements.grid;
        grid.innerHTML = '';

        this.list.forEach((album) => {
            const card = document.createElement('button');
            card.type = 'button';
            card.className = 'album-card';

            if (album.albumThumbnailAssetId) {
                const img = document.createElement('img');
                img.src = API.getThumbnailUrl(album.albumThumbnailAssetId, 'thumbnail');
                img.alt = '';
                img.loading = 'lazy';
                img.onerror = () => { img.style.display = 'none'; };
                card.appendChild(img);
            } else {
                const placeholder = document.createElement('div');
                placeholder.className = 'album-card-placeholder';
                card.appendChild(placeholder);
            }

            const info = document.createElement('div');
            info.className = 'album-card-info';

            const name = document.createElement('span');
            name.className = 'album-card-name';
            name.textContent = album.albumName?.trim() || 'Untitled album';
            info.appendChild(name);

            const count = document.createElement('span');
            count.className = 'album-card-count';
            const total = album.assetCount;
            count.textContent = Number.isFinite(total) ? `${total} item${total === 1 ? '' : 's'}` : '';
            info.appendChild(count);

            card.appendChild(info);
            card.addEventListener('click', () => this.select(album));
            grid.appendChild(card);
        });
    },

    select(album) {
        this.close();
        State.set({
            albumId: album.id,
            page: 1,
            assets: [],
            hasMore: true,
        });
        Gallery.load();
    },
};

window.Albums = Albums;
