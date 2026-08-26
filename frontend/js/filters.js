/**
 * Filter panel: search, dates, media type, camera, location, people chips.
 */

const Filters = {
    elements: {
        filterPanel: null,
        filterBackdrop: null,
        filterToggle: null,
        filterBadge: null,
        clearFilters: null,
        applyFilters: null,
        searchInput: null,
        searchBtn: null,
        dateFrom: null,
        dateTo: null,
        cameraMake: null,
        cameraModel: null,
        locationCountry: null,
        locationCity: null,
        peopleSearch: null,
        peopleChips: null,
        searchContainer: null,
        searchPersonChips: null,
        searchPersonSuggest: null,
    },

    searchDebounce: null,
    peopleList: [],
    // Cap unselected chips when collapsed; selected chips always stay visible
    peopleChipLimit: 18,
    peopleExpanded: false,
    personSuggestMatches: [],
    personSuggestIndex: -1,

    init() {
        this.elements.filterPanel = document.getElementById('filter-panel');
        this.elements.filterBackdrop = document.getElementById('filter-backdrop');
        this.elements.filterToggle = document.getElementById('filter-toggle');
        this.elements.filterBadge = document.getElementById('filter-badge');
        this.elements.clearFilters = document.getElementById('clear-filters');
        this.elements.applyFilters = document.getElementById('apply-filters');
        this.elements.searchContainer = document.getElementById('search-container');
        this.elements.searchInput = document.getElementById('search-input');
        this.elements.searchBtn = document.getElementById('search-btn');
        this.elements.searchPersonChips = document.getElementById('search-person-chips');
        this.elements.searchPersonSuggest = document.getElementById('search-person-suggest');
        this.elements.dateFrom = document.getElementById('date-from');
        this.elements.dateTo = document.getElementById('date-to');
        this.elements.cameraMake = document.getElementById('camera-make');
        this.elements.cameraModel = document.getElementById('camera-model');
        this.elements.locationCountry = document.getElementById('location-country');
        this.elements.locationCity = document.getElementById('location-city');
        this.elements.peopleSearch = document.getElementById('people-search');
        this.elements.peopleChips = document.getElementById('people-chips');

        this.setupEventListeners();
        this.loadPeople();
        this.loadSuggestions();
        this.initFromState();

        State.subscribe((newState, oldState) => {
            this.onStateChange(newState, oldState);
        });
    },

    setupEventListeners() {
        this.elements.filterToggle.addEventListener('click', () => this.togglePanel());

        // Backdrop click / Escape dismisses the panel (lightbox owns Escape while open)
        this.elements.filterBackdrop?.addEventListener('click', () => {
            this.hidePanel();
            window.Activity?.hide();
        });
        document.addEventListener('keydown', (e) => {
            if (e.key !== 'Escape') return;
            if (this.isPersonSuggestOpen()) {
                this.hidePersonSuggestions();
                return;
            }
            const lightbox = document.getElementById('lightbox');
            if (lightbox && !lightbox.hidden) return;
            if (!this.elements.filterPanel.hidden) {
                this.hidePanel();
                return;
            }
            window.Activity?.hide();
        });

        document.addEventListener('mousedown', (e) => {
            if (!this.elements.searchContainer?.contains(e.target)) {
                this.hidePersonSuggestions();
            }
        });

        this.elements.clearFilters.addEventListener('click', () => this.clearAll());
        this.elements.applyFilters.addEventListener('click', () => this.apply());

        this.elements.searchContainer?.addEventListener('click', (e) => {
            if (e.target.closest('.search-person-chip-remove')) return;
            if (e.target.closest('.search-person-option')) return;
            this.elements.searchInput.focus();
        });

        this.elements.searchInput.addEventListener('input', () => {
            this.onSearchInput();
        });

        this.elements.searchInput.addEventListener('keydown', (e) => {
            this.onSearchKeydown(e);
        });

        this.elements.searchBtn.addEventListener('click', () => {
            this.commitTextSearch();
        });

        this.elements.dateFrom.addEventListener('change', (e) => {
            State.set({ dateFrom: e.target.value || null, page: 1, assets: [] });
            Gallery.load();
        });

        this.elements.dateTo.addEventListener('change', (e) => {
            State.set({ dateTo: e.target.value || null, page: 1, assets: [] });
            Gallery.load();
        });

        document.querySelectorAll('input[name="media-type"]').forEach((radio) => {
            radio.addEventListener('change', (e) => {
                State.set({ mediaType: e.target.value, page: 1, assets: [] });
                Gallery.load();
            });
        });

        this.elements.cameraMake.addEventListener('change', (e) => {
            State.set({ cameraMake: e.target.value, page: 1, assets: [] });
            Gallery.load();
        });

        this.elements.cameraModel.addEventListener('change', (e) => {
            State.set({ cameraModel: e.target.value, page: 1, assets: [] });
            Gallery.load();
        });

        this.elements.locationCountry.addEventListener('change', (e) => {
            State.set({ country: e.target.value, page: 1, assets: [] });
            Gallery.load();
        });

        this.elements.locationCity.addEventListener('change', (e) => {
            State.set({ city: e.target.value, page: 1, assets: [] });
            Gallery.load();
        });

        this.elements.peopleSearch.addEventListener('input', () => {
            this.renderPeopleChips();
        });
    },

    togglePanel() {
        if (this.elements.filterPanel.hidden) {
            this.showPanel();
        } else {
            this.hidePanel();
        }
    },

    showPanel() {
        window.Activity?.hide();
        this.elements.filterPanel.hidden = false;
        if (this.elements.filterBackdrop) {
            this.elements.filterBackdrop.hidden = false;
        }
    },

    hidePanel() {
        this.elements.filterPanel.hidden = true;
        if (this.elements.filterBackdrop && !window.Activity?.isOpen()) {
            this.elements.filterBackdrop.hidden = true;
        }
    },

    apply() {
        State.set({ page: 1, assets: [], hasMore: true });
        Gallery.load();
        if (window.innerWidth < 768) {
            this.hidePanel();
        }
    },

    onSearchInput() {
        const value = this.elements.searchInput.value;
        this.renderPersonSuggestions(value);
        clearTimeout(this.searchDebounce);
        if (this.personSuggestMatches.length > 0) {
            return;
        }
        this.searchDebounce = setTimeout(() => {
            State.set({ query: value, page: 1, assets: [], hasMore: true });
            Gallery.load();
        }, 500);
    },

    onSearchKeydown(e) {
        const open = this.isPersonSuggestOpen();
        const count = this.personSuggestMatches.length;

        if (e.key === 'Escape' && open) {
            e.preventDefault();
            this.hidePersonSuggestions();
            return;
        }

        if (open && count > 0 && (e.key === 'Tab' || e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
            e.preventDefault();
            const backward = e.key === 'ArrowUp' || (e.key === 'Tab' && e.shiftKey);
            this.movePersonSuggestHighlight(backward ? -1 : 1);
            return;
        }

        if (e.key === 'Enter') {
            e.preventDefault();
            if (open && this.personSuggestIndex >= 0) {
                const person = this.personSuggestMatches[this.personSuggestIndex];
                if (person) this.selectSearchPerson(person.id);
                return;
            }
            this.commitTextSearch();
            return;
        }

        if (e.key === 'Backspace' && !this.elements.searchInput.value) {
            const personIds = State.getProperty('personIds');
            if (personIds.length === 0) return;
            e.preventDefault();
            this.removeSearchPerson(personIds[personIds.length - 1]);
        }
    },

    commitTextSearch() {
        clearTimeout(this.searchDebounce);
        this.hidePersonSuggestions();
        State.set({
            query: this.elements.searchInput.value,
            page: 1,
            assets: [],
            hasMore: true,
        });
        Gallery.load();
    },

    nameTokens(value) {
        return (value || '').trim().toLowerCase().split(/\s+/).filter(Boolean);
    },

    personNameMatchesQuery(personName, query) {
        const qTokens = this.nameTokens(query);
        const nTokens = this.nameTokens(personName);
        if (!qTokens.length || !nTokens.length) return false;
        const used = nTokens.map(() => false);
        for (const qt of qTokens) {
            if (qt.length < 2) return false;
            let found = false;
            for (let i = 0; i < nTokens.length; i++) {
                if (used[i]) continue;
                if (nTokens[i] === qt || nTokens[i].startsWith(qt)) {
                    used[i] = true;
                    found = true;
                    break;
                }
            }
            if (!found) return false;
        }
        return true;
    },

    matchingPeople(query) {
        const selected = new Set(State.getProperty('personIds'));
        return this.peopleList
            .filter((person) => {
                if (!person.id || selected.has(person.id)) return false;
                return this.personNameMatchesQuery(person.name || '', query);
            })
            .slice(0, 8);
    },

    renderPersonSuggestions(query) {
        const box = this.elements.searchPersonSuggest;
        if (!box) return;

        const matches = this.matchingPeople(query);
        this.personSuggestMatches = matches;
        this.personSuggestIndex = -1;

        if (matches.length === 0) {
            this.hidePersonSuggestions();
            return;
        }

        box.innerHTML = '';
        matches.forEach((person, index) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.id = `search-person-option-${index}`;
            btn.className = 'search-person-option';
            btn.setAttribute('role', 'option');
            btn.setAttribute('aria-selected', 'false');
            const label = person.name?.trim() || 'Unnamed person';
            const img = document.createElement('img');
            img.src = API.getPersonThumbnailUrl(person.id);
            img.alt = '';
            img.onerror = () => { img.style.display = 'none'; };
            btn.appendChild(img);

            const nameSpan = document.createElement('span');
            nameSpan.textContent = label;
            btn.appendChild(nameSpan);

            btn.addEventListener('mousedown', (e) => e.preventDefault());
            btn.addEventListener('click', () => this.selectSearchPerson(person.id));
            btn.addEventListener('mouseenter', () => this.setPersonSuggestHighlight(index));
            box.appendChild(btn);
        });

        box.hidden = false;
        this.elements.searchInput.setAttribute('aria-expanded', 'true');
        this.elements.searchInput.removeAttribute('aria-activedescendant');
    },

    isPersonSuggestOpen() {
        return Boolean(this.elements.searchPersonSuggest && !this.elements.searchPersonSuggest.hidden);
    },

    hidePersonSuggestions() {
        this.personSuggestMatches = [];
        this.personSuggestIndex = -1;
        if (this.elements.searchPersonSuggest) {
            this.elements.searchPersonSuggest.hidden = true;
            this.elements.searchPersonSuggest.innerHTML = '';
        }
        if (this.elements.searchInput) {
            this.elements.searchInput.setAttribute('aria-expanded', 'false');
            this.elements.searchInput.removeAttribute('aria-activedescendant');
        }
    },

    movePersonSuggestHighlight(delta) {
        const count = this.personSuggestMatches.length;
        if (count === 0) return;
        const next = this.personSuggestIndex < 0
            ? (delta > 0 ? 0 : count - 1)
            : (this.personSuggestIndex + delta + count) % count;
        this.setPersonSuggestHighlight(next);
    },

    setPersonSuggestHighlight(index) {
        this.personSuggestIndex = index;
        const options = this.elements.searchPersonSuggest.querySelectorAll('.search-person-option');
        options.forEach((option, i) => {
            const selected = i === index;
            option.setAttribute('aria-selected', String(selected));
            if (selected) option.scrollIntoView({ block: 'nearest' });
        });
        const active = options[index];
        if (active) {
            this.elements.searchInput.setAttribute('aria-activedescendant', active.id);
        }
    },

    selectSearchPerson(personId) {
        const currentIds = State.getProperty('personIds');
        if (currentIds.includes(personId)) {
            this.elements.searchInput.value = '';
            this.hidePersonSuggestions();
            return;
        }
        const newIds = [...currentIds, personId];
        this.elements.searchInput.value = '';
        this.hidePersonSuggestions();
        State.set({ personIds: newIds, query: '', page: 1, assets: [], hasMore: true });
        this.updatePeopleChips(newIds);
        Gallery.load();
        this.elements.searchInput.focus();
    },

    removeSearchPerson(personId) {
        const newIds = State.getProperty('personIds').filter((id) => id !== personId);
        State.set({ personIds: newIds, page: 1, assets: [], hasMore: true });
        this.updatePeopleChips(newIds);
        Gallery.load();
        this.elements.searchInput.focus();
    },

    renderSearchPersonChips() {
        const container = this.elements.searchPersonChips;
        if (!container) return;

        const selectedIds = State.getProperty('personIds');
        const peopleById = new Map(this.peopleList.map((p) => [p.id, p]));
        container.innerHTML = '';

        selectedIds.forEach((id) => {
            const person = peopleById.get(id);
            const label = person?.name?.trim() || 'Unnamed person';
            const chip = document.createElement('span');
            chip.className = 'search-person-chip';
            chip.setAttribute('data-person-id', id);

            const img = document.createElement('img');
            img.src = API.getPersonThumbnailUrl(id);
            img.alt = '';
            img.onerror = () => { img.style.display = 'none'; };
            chip.appendChild(img);

            const nameSpan = document.createElement('span');
            nameSpan.textContent = label;
            chip.appendChild(nameSpan);

            const remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'search-person-chip-remove';
            remove.setAttribute('aria-label', `Remove ${label}`);
            remove.innerHTML = `
                <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <line x1="18" y1="6" x2="6" y2="18"></line>
                    <line x1="6" y1="6" x2="18" y2="18"></line>
                </svg>
            `;
            remove.addEventListener('click', (e) => {
                e.stopPropagation();
                this.removeSearchPerson(id);
            });
            chip.appendChild(remove);
            container.appendChild(chip);
        });

        const hasPeople = selectedIds.length > 0;
        this.elements.searchInput.placeholder = hasPeople
            ? 'Add a person or search meaning…'
            : 'Search meaning, or type a name to pick a person';
    },

    clearAll() {
        this.elements.searchInput.value = '';
        this.elements.dateFrom.value = '';
        this.elements.dateTo.value = '';
        this.elements.cameraMake.value = '';
        this.elements.cameraModel.value = '';
        this.elements.locationCountry.value = '';
        this.elements.locationCity.value = '';
        document.querySelector('input[name="media-type"][value="ALL"]').checked = true;

        this.elements.peopleSearch.value = '';
        this.hidePersonSuggestions();
        State.clearFilters();
        this.renderPeopleChips();
        this.renderSearchPersonChips();
        Gallery.load();
    },

    initFromState() {
        const state = State.get();

        if (state.query) this.elements.searchInput.value = state.query;
        this.renderSearchPersonChips();
        if (state.dateFrom) this.elements.dateFrom.value = state.dateFrom;
        if (state.dateTo) this.elements.dateTo.value = state.dateTo;

        if (state.mediaType) {
            const radio = document.querySelector(
                `input[name="media-type"][value="${state.mediaType}"]`,
            );
            if (radio) radio.checked = true;
        }

        if (state.cameraMake) this.elements.cameraMake.value = state.cameraMake;
        if (state.cameraModel) this.elements.cameraModel.value = state.cameraModel;
        if (state.country) this.elements.locationCountry.value = state.country;
        if (state.city) this.elements.locationCity.value = state.city;
    },

    onStateChange(newState, oldState) {
        const count = State.getActiveFilterCount();
        this.elements.filterBadge.textContent = count;
        this.elements.filterBadge.hidden = count === 0;

        if (newState.personIds !== oldState?.personIds) {
            this.renderSearchPersonChips();
        }
        if (
            newState.query !== oldState?.query &&
            document.activeElement !== this.elements.searchInput
        ) {
            this.elements.searchInput.value = newState.query || '';
        }
    },

    async loadPeople() {
        try {
            const response = await API.getPeople();
            const people = [...(response.people || [])].sort((a, b) =>
                (a.name || '').localeCompare(b.name || '', undefined, { sensitivity: 'base' }),
            );

            this.peopleList = people;
            State.set({ people });
            this.renderPeopleChips();
            this.renderSearchPersonChips();
            if (this.elements.searchInput.value) {
                this.renderPersonSuggestions(this.elements.searchInput.value);
            }
        } catch (error) {
            console.error('Failed to load people:', error);
            this.elements.peopleChips.innerHTML =
                '<div class="loading-placeholder">Failed to load people</div>';
        }
    },

    /** Search-filtered chips; selected always included; collapsed list is capped. */
    renderPeopleChips() {
        const people = this.peopleList;
        this.elements.peopleChips.innerHTML = '';

        if (people.length === 0) {
            this.elements.peopleChips.innerHTML =
                '<div class="loading-placeholder">No people found</div>';
            return;
        }

        const selectedIds = State.getProperty('personIds');
        const query = (this.elements.peopleSearch?.value || '').trim().toLowerCase();

        const matched = people.filter((person) => {
            if (selectedIds.includes(person.id)) return true;
            if (!query) return true;
            return (person.name || '').toLowerCase().includes(query);
        });

        matched.sort((a, b) => {
            const aSel = selectedIds.includes(a.id) ? 0 : 1;
            const bSel = selectedIds.includes(b.id) ? 0 : 1;
            if (aSel !== bSel) return aSel - bSel;
            return (a.name || '').localeCompare(b.name || '', undefined, {
                sensitivity: 'base',
            });
        });

        if (matched.length === 0) {
            this.elements.peopleChips.innerHTML =
                '<div class="people-chips-empty">No matching people</div>';
            return;
        }

        let visible = matched;
        const canCollapse = !query && matched.length > this.peopleChipLimit;
        if (canCollapse && !this.peopleExpanded) {
            const selected = matched.filter((p) => selectedIds.includes(p.id));
            const rest = matched.filter((p) => !selectedIds.includes(p.id));
            const restSlots = Math.max(0, this.peopleChipLimit - selected.length);
            visible = [...selected, ...rest.slice(0, restSlots)];
        }

        visible.forEach((person) => {
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'person-chip';
            chip.setAttribute('data-person-id', person.id);

            if (selectedIds.includes(person.id)) {
                chip.classList.add('selected');
            }

            const label = person.name?.trim() || 'Unnamed person';
            const img = document.createElement('img');
            img.src = API.getPersonThumbnailUrl(person.id);
            img.alt = label;
            img.onerror = () => { img.style.display = 'none'; };
            chip.appendChild(img);

            const nameSpan = document.createElement('span');
            nameSpan.textContent = label;
            chip.appendChild(nameSpan);

            chip.addEventListener('click', () => this.togglePerson(person.id));
            this.elements.peopleChips.appendChild(chip);
        });

        if (canCollapse) {
            const hiddenCount = matched.length - visible.length;
            const toggle = document.createElement('button');
            toggle.type = 'button';
            toggle.className = 'people-chips-more';
            toggle.textContent = this.peopleExpanded
                ? 'Show less'
                : `Show all (${hiddenCount} more)`;
            toggle.addEventListener('click', () => {
                this.peopleExpanded = !this.peopleExpanded;
                this.renderPeopleChips();
            });
            this.elements.peopleChips.appendChild(toggle);
        }
    },

    togglePerson(personId) {
        const currentIds = State.getProperty('personIds');
        const newIds = currentIds.includes(personId)
            ? currentIds.filter((id) => id !== personId)
            : [...currentIds, personId];

        State.set({ personIds: newIds, page: 1, assets: [] });
        this.updatePeopleChips(newIds);
        Gallery.load();
    },

    updatePeopleChips(selectedIds) {
        this.renderPeopleChips();
        this.renderSearchPersonChips();
        this.updateUnnamedPersonBanners(selectedIds);
    },

    /** Banners for person filters that aren't in the named Immich people list. */
    updateUnnamedPersonBanners(selectedIds) {
        const activeFiltersContainer = document.getElementById('active-filters');
        if (!activeFiltersContainer) return;

        const namedPeopleIds = State.getProperty('people').map((p) => p.id);
        const unnamedSelectedIds = selectedIds.filter((id) => !namedPeopleIds.includes(id));

        activeFiltersContainer
            .querySelectorAll('.unnamed-person-banner')
            .forEach((banner) => banner.remove());

        unnamedSelectedIds.forEach((personId) => {
            const banner = document.createElement('div');
            banner.className = 'filter-banner unnamed-person-banner';
            banner.setAttribute('data-person-id', personId);

            banner.innerHTML = `
                <span>Filtering by: Unnamed Person</span>
                <button class="banner-close" aria-label="Remove filter">
                    <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <line x1="18" y1="6" x2="6" y2="18"></line>
                        <line x1="6" y1="6" x2="18" y2="18"></line>
                    </svg>
                </button>
            `;

            banner.querySelector('.banner-close').addEventListener('click', () => {
                const currentIds = State.getProperty('personIds');
                const newIds = currentIds.filter((id) => id !== personId);
                State.set({ personIds: newIds, page: 1, assets: [] });
                this.updatePeopleChips(newIds);
                Gallery.load();
            });

            activeFiltersContainer.appendChild(banner);
        });
    },

    async loadSuggestions() {
        try {
            const suggestions = await API.getSearchSuggestions();
            State.set({ suggestions });
            this.populateSuggestionDropdowns(suggestions);
        } catch (error) {
            console.error('Failed to load suggestions:', error);
        }
    },

    populateSuggestionDropdowns(suggestions) {
        if (suggestions.cameraMake) {
            this.populateSelect(this.elements.cameraMake, suggestions.cameraMake, 'Any make');
        }
        if (suggestions.cameraModel) {
            this.populateSelect(this.elements.cameraModel, suggestions.cameraModel, 'Any model');
        }
        if (suggestions.country) {
            this.populateSelect(this.elements.locationCountry, suggestions.country, 'Any country');
        }
        if (suggestions.city) {
            this.populateSelect(this.elements.locationCity, suggestions.city, 'Any city');
        }

        // Re-apply URL/state values after options exist
        const state = State.get();
        if (state.cameraMake) this.elements.cameraMake.value = state.cameraMake;
        if (state.cameraModel) this.elements.cameraModel.value = state.cameraModel;
        if (state.country) this.elements.locationCountry.value = state.country;
        if (state.city) this.elements.locationCity.value = state.city;
    },

    populateSelect(select, options, placeholder) {
        select.innerHTML = `<option value="">${placeholder}</option>`;
        options.forEach((option) => {
            if (option) {
                const opt = document.createElement('option');
                opt.value = option;
                opt.textContent = option;
                select.appendChild(opt);
            }
        });
    },
};

window.Filters = Filters;
