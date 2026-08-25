/**
 * Day-grouped photo grid with infinite scroll.
 */

const Gallery = {
    elements: {
        gallery: null,
        galleryLoading: null,
        loadMoreContainer: null,
        loadMoreBtn: null,
        loadingIndicator: null,
        emptyState: null,
        errorState: null,
        errorMessage: null,
        retryBtn: null,
        resultCount: null,
        activeFilters: null,
    },

    observer: null,

    init() {
        this.elements.gallery = document.getElementById('timeline');
        this.elements.galleryLoading = document.getElementById('gallery-loading');
        this.elements.loadMoreContainer = document.getElementById('load-more-container');
        this.elements.loadMoreBtn = document.getElementById('load-more-btn');
        this.elements.loadingIndicator = document.getElementById('loading-indicator');
        this.elements.emptyState = document.getElementById('empty-state');
        this.elements.errorState = document.getElementById('error-state');
        this.elements.errorMessage = document.getElementById('error-message');
        this.elements.retryBtn = document.getElementById('retry-btn');
        this.elements.resultCount = document.getElementById('result-count');
        this.elements.activeFilters = document.getElementById('active-filters');

        this.elements.loadMoreBtn?.addEventListener('click', () => this.loadMore());
        this.elements.retryBtn?.addEventListener('click', () => this.reload());

        this.setupInfiniteScroll();

        State.subscribe((newState, oldState) => {
            this.onStateChange(newState, oldState);
        });
    },

    setupInfiniteScroll() {
        this.observer = new IntersectionObserver(
            (entries) => {
                const entry = entries[0];
                if (
                    entry.isIntersecting &&
                    !State.getProperty('isLoadingMore') &&
                    State.getProperty('hasMore')
                ) {
                    this.loadMore();
                }
            },
            { rootMargin: '200px' },
        );

        if (this.elements.loadMoreContainer) {
            this.observer.observe(this.elements.loadMoreContainer);
        }
    },

    onStateChange(newState, oldState) {
        if (newState.isLoading !== oldState.isLoading) {
            this.elements.galleryLoading.hidden =
                !newState.isLoading || newState.assets.length > 0;
        }

        if (
            newState.total !== oldState.total ||
            newState.assets.length !== oldState.assets.length
        ) {
            this.updateResultCount(newState);
        }

        this.updateActiveFilters(newState);
    },

    async load() {
        State.set({ isLoading: true, error: null });
        this.showLoading();

        try {
            const payload = State.buildSearchPayload(false);
            const response = await API.search(payload);

            State.set({
                assets: response.items || [],
                // Prefer API total (including 0); fall back to page length when omitted
                total: response.total ?? (response.items ? response.items.length : 0),
                page: response.page || 1,
                hasMore: response.hasMore !== false,
                isLoading: false,
            });

            this.render();
            State.syncToURL();
        } catch (error) {
            console.error('Failed to load assets:', error);
            State.set({ isLoading: false, error: error.message });
            this.showError(error.message);
        }
    },

    async loadMore() {
        if (State.getProperty('isLoadingMore') || !State.getProperty('hasMore')) {
            return;
        }

        State.set({ isLoadingMore: true });
        this.showLoadingMore();

        try {
            const payload = State.buildSearchPayload(true);
            const response = await API.search(payload);
            const currentAssets = State.getProperty('assets');
            const newAssets = response.items || [];

            State.set({
                assets: [...currentAssets, ...newAssets],
                page: response.page || State.getProperty('page') + 1,
                hasMore: response.hasMore !== false && newAssets.length > 0,
                isLoadingMore: false,
            });

            this.appendItems(newAssets);
        } catch (error) {
            console.error('Failed to load more assets:', error);
            State.set({ isLoadingMore: false });
        } finally {
            this.hideLoadingMore();
        }
    },

    reload() {
        this.load();
    },

    render() {
        const assets = State.getProperty('assets');
        this.clearGallery();

        if (assets.length === 0) {
            this.showEmpty();
            return;
        }

        this.hideEmpty();
        this.hideError();

        const fragment = document.createDocumentFragment();
        let currentKey = null;
        let grid = null;

        assets.forEach((asset, index) => {
            const key = this.dayKey(asset.localDateTime || asset.fileCreatedAt);
            if (key !== currentKey) {
                currentKey = key;
                const group = document.createElement('section');
                group.className = 'day-group';

                const header = document.createElement('h2');
                header.className = 'day-header';
                header.textContent = this.dayLabel(asset.localDateTime || asset.fileCreatedAt);
                group.appendChild(header);

                grid = document.createElement('div');
                grid.className = 'gallery';
                group.appendChild(grid);
                fragment.appendChild(group);
            }
            grid.appendChild(this.createGalleryItem(asset, index));
        });

        this.elements.gallery.appendChild(fragment);
        this.elements.loadMoreContainer.hidden = !State.getProperty('hasMore');
    },

    /** Append a page while continuing the last day group when dates match. */
    appendItems(assets) {
        const currentLength = State.getProperty('assets').length - assets.length;
        let grid = this.elements.gallery.querySelector('.day-group:last-of-type .gallery');
        let currentKey = null;

        if (grid) {
            const lastAsset = State.getProperty('assets')[currentLength - 1];
            currentKey = lastAsset
                ? this.dayKey(lastAsset.localDateTime || lastAsset.fileCreatedAt)
                : null;
        }

        assets.forEach((asset, index) => {
            const key = this.dayKey(asset.localDateTime || asset.fileCreatedAt);
            if (key !== currentKey || !grid) {
                currentKey = key;
                const group = document.createElement('section');
                group.className = 'day-group';

                const header = document.createElement('h2');
                header.className = 'day-header';
                header.textContent = this.dayLabel(asset.localDateTime || asset.fileCreatedAt);
                group.appendChild(header);

                grid = document.createElement('div');
                grid.className = 'gallery';
                group.appendChild(grid);
                this.elements.gallery.appendChild(group);
            }
            grid.appendChild(this.createGalleryItem(asset, currentLength + index));
        });

        this.elements.loadMoreContainer.hidden = !State.getProperty('hasMore');
    },

    dayKey(dateString) {
        if (!dateString) return 'unknown';
        const d = new Date(dateString);
        if (Number.isNaN(d.getTime())) return 'unknown';
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    },

    dayLabel(dateString) {
        if (!dateString) return 'Unknown';
        try {
            const d = new Date(dateString);
            const now = new Date();
            const opts = { weekday: 'long', month: 'long', day: 'numeric' };
            if (d.getFullYear() !== now.getFullYear()) {
                opts.year = 'numeric';
            }
            return d.toLocaleDateString(undefined, opts);
        } catch {
            return 'Unknown';
        }
    },

    thumbSize() {
        return document.documentElement.dataset.view === 'large' ? 'preview' : 'thumbnail';
    },

    createGalleryItem(asset, index) {
        const item = document.createElement('div');
        item.className = 'gallery-item';
        item.setAttribute('role', 'button');
        item.setAttribute('tabindex', '0');
        item.setAttribute('data-asset-id', asset.id);
        item.setAttribute('data-index', index);

        const img = document.createElement('img');
        img.className = 'loading';
        img.alt = asset.originalFileName || 'Photo';
        img.loading = 'lazy';
        img.src = API.getThumbnailUrl(asset.id, this.thumbSize());
        img.onload = () => img.classList.remove('loading');
        img.onerror = () => {
            img.src =
                'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect fill="%23ccc" width="100" height="100"/><text x="50" y="55" text-anchor="middle" fill="%23999" font-size="12">Error</text></svg>';
        };
        item.appendChild(img);

        if (asset.type === 'VIDEO') {
            const indicator = document.createElement('div');
            indicator.className = 'video-indicator';
            indicator.innerHTML = `
                <svg viewBox="0 0 24 24" fill="currentColor">
                    <polygon points="5 3 19 12 5 21 5 3"></polygon>
                </svg>
            `;
            item.appendChild(indicator);
        }

        const overlay = document.createElement('div');
        overlay.className = 'gallery-item-overlay';
        overlay.textContent = this.formatDate(asset.localDateTime || asset.fileCreatedAt);
        item.appendChild(overlay);

        item.addEventListener('click', () => Lightbox.open(asset, index));
        item.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                Lightbox.open(asset, index);
            }
        });

        return item;
    },

    formatDate(dateString) {
        if (!dateString) return '';
        try {
            return new Date(dateString).toLocaleDateString(undefined, {
                year: 'numeric',
                month: 'short',
                day: 'numeric',
            });
        } catch {
            return '';
        }
    },

    updateResultCount(state) {
        const total = state.total;
        const showing = state.assets.length;

        if (showing === 0) {
            this.elements.resultCount.textContent = 'No results';
            return;
        }

        if (Number.isFinite(total) && total > 0 && showing < total) {
            this.elements.resultCount.textContent = `Showing ${showing} of ${total} items`;
            return;
        }

        if (state.hasMore) {
            this.elements.resultCount.textContent = `Showing ${showing}+ items`;
            return;
        }

        this.elements.resultCount.textContent = `${showing} items`;
    },

    updateActiveFilters(state) {
        const container = this.elements.activeFilters;
        container.innerHTML = '';

        const filters = [];

        if (state.query) {
            filters.push({ label: `"${state.query}"`, key: 'query' });
        }

        if (state.personIds.length > 0) {
            const people = State.getProperty('people') || [];
            const peopleById = new Map(people.map((p) => [p.id, p]));

            state.personIds.forEach((id) => {
                const person = peopleById.get(id);
                const label = person?.name?.trim() || 'Unnamed person';
                filters.push({ label, key: 'person', value: id });
            });
        }

        if (state.dateFrom || state.dateTo) {
            const label =
                state.dateFrom && state.dateTo
                    ? `${state.dateFrom} - ${state.dateTo}`
                    : state.dateFrom
                      ? `From ${state.dateFrom}`
                      : `Until ${state.dateTo}`;
            filters.push({ label, key: 'date' });
        }

        if (state.mediaType !== 'ALL') {
            filters.push({
                label: state.mediaType === 'IMAGE' ? 'Photos' : 'Videos',
                key: 'mediaType',
            });
        }

        if (state.cameraMake) filters.push({ label: state.cameraMake, key: 'cameraMake' });
        if (state.cameraModel) filters.push({ label: state.cameraModel, key: 'cameraModel' });
        if (state.country) filters.push({ label: state.country, key: 'country' });
        if (state.city) filters.push({ label: state.city, key: 'city' });

        filters.forEach((filter) => {
            const tag = document.createElement('span');
            tag.className = 'filter-tag';
            tag.appendChild(document.createTextNode(filter.label));

            const button = document.createElement('button');
            button.setAttribute('aria-label', 'Remove filter');
            button.setAttribute('data-filter', filter.key);
            button.setAttribute('data-value', filter.value || '');
            button.innerHTML = `
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <line x1="18" y1="6" x2="6" y2="18"></line>
                    <line x1="6" y1="6" x2="18" y2="18"></line>
                </svg>
            `;
            button.addEventListener('click', () => {
                this.removeFilter(filter.key, filter.value);
            });
            tag.appendChild(button);
            container.appendChild(tag);
        });
    },

    removeFilter(key, value) {
        const updates = {};

        switch (key) {
            case 'query':
                updates.query = '';
                document.getElementById('search-input').value = '';
                break;
            case 'person': {
                const personIds = State.getProperty('personIds').filter((id) => id !== value);
                updates.personIds = personIds;
                Filters.updatePeopleChips(personIds);
                break;
            }
            case 'date':
                updates.dateFrom = null;
                updates.dateTo = null;
                document.getElementById('date-from').value = '';
                document.getElementById('date-to').value = '';
                break;
            case 'mediaType':
                updates.mediaType = 'ALL';
                document.querySelector('input[name="media-type"][value="ALL"]').checked = true;
                break;
            case 'cameraMake':
                updates.cameraMake = '';
                document.getElementById('camera-make').value = '';
                break;
            case 'cameraModel':
                updates.cameraModel = '';
                document.getElementById('camera-model').value = '';
                break;
            case 'country':
                updates.country = '';
                document.getElementById('location-country').value = '';
                break;
            case 'city':
                updates.city = '';
                document.getElementById('location-city').value = '';
                break;
        }

        updates.page = 1;
        updates.assets = [];
        updates.hasMore = true;

        State.set(updates);
        this.load();
    },

    clearGallery() {
        this.elements.gallery.querySelectorAll('.day-group').forEach((el) => el.remove());
    },

    showLoading() {
        this.elements.galleryLoading.hidden = false;
        this.elements.emptyState.hidden = true;
        this.elements.errorState.hidden = true;
    },

    showLoadingMore() {
        this.elements.loadMoreBtn.hidden = true;
        this.elements.loadingIndicator.hidden = false;
    },

    hideLoadingMore() {
        this.elements.loadMoreBtn.hidden = false;
        this.elements.loadingIndicator.hidden = true;
    },

    showEmpty() {
        this.elements.galleryLoading.hidden = true;
        this.elements.emptyState.hidden = false;
        this.elements.errorState.hidden = true;
        this.elements.loadMoreContainer.hidden = true;
    },

    hideEmpty() {
        this.elements.emptyState.hidden = true;
    },

    showError(message) {
        this.elements.galleryLoading.hidden = true;
        this.elements.emptyState.hidden = true;
        this.elements.errorState.hidden = false;
        this.elements.errorMessage.textContent =
            message || 'Unable to load photos. Please try again.';
        this.elements.loadMoreContainer.hidden = true;
    },

    hideError() {
        this.elements.errorState.hidden = true;
    },
};

window.Gallery = Gallery;
