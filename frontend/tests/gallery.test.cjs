const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const galleryPath = path.join(__dirname, '..', 'js', 'gallery.js');

function matchesSelector(element, part) {
    const match = /^([a-z]*)(?:\.([\w-]+))?$/i.exec(part);
    if (!match) return false;

    const [, tag, className] = match;
    if (tag && element.tagName !== tag.toUpperCase()) return false;
    if (className && !String(element.className).split(/\s+/).includes(className)) return false;
    return true;
}

function makeHarness() {
    const requestedImages = [];
    const removedSources = [];
    const imageObservers = [];

    class FakeClassList {
        constructor() {
            this._classes = new Set();
        }

        add(...names) {
            names.forEach((name) => this._classes.add(name));
        }

        remove(...names) {
            names.forEach((name) => this._classes.delete(name));
        }

        toggle(name) {
            if (this._classes.has(name)) this._classes.delete(name);
            else this._classes.add(name);
        }

        contains(name) {
            return this._classes.has(name);
        }
    }

    class FakeElement {
        constructor(tagName) {
            this.tagName = String(tagName).toUpperCase();
            this.children = [];
            this.parentNode = null;
            this.attributes = new Map();
            this.dataset = {};
            this.style = {};
            this.classList = new FakeClassList();
            this.className = '';
            this.hidden = false;
            this.alt = '';
            this.decoding = '';
            this.fetchPriority = '';
            this.onload = null;
            this.onerror = null;
        }

        setAttribute(name, value) {
            this.attributes.set(name, String(value));
        }

        getAttribute(name) {
            return this.attributes.has(name) ? this.attributes.get(name) : null;
        }

        removeAttribute(name) {
            if (name === 'src') {
                removedSources.push(this);
                this.attributes.delete(name);
                return;
            }
            this.attributes.delete(name);
        }

        get src() {
            return this.getAttribute('src') || '';
        }

        set src(value) {
            if (value) {
                this.setAttribute('src', value);
                requestedImages.push(value);
                return;
            }
            this.removeAttribute('src');
        }

        appendChild(child) {
            if (child.tagName === '#FRAGMENT') {
                [...child.children].forEach((grandchild) => this.appendChild(grandchild));
                child.children = [];
                return child;
            }
            child.parentNode = this;
            this.children.push(child);
            return child;
        }

        querySelectorAll(selector) {
            const parts = String(selector).trim().split(/\s+/);
            const results = [];

            const visit = (node, ancestors) => {
                node.children.forEach((child) => {
                    const chain = [...ancestors, child];
                    if (matchesSelector(child, parts[parts.length - 1])) {
                        let partIndex = parts.length - 2;
                        for (let i = chain.length - 2; i >= 0 && partIndex >= 0; i--) {
                            if (matchesSelector(chain[i], parts[partIndex])) partIndex--;
                        }
                        if (partIndex < 0) results.push(child);
                    }
                    visit(child, chain);
                });
            };

            visit(this, []);
            return results;
        }

        addEventListener(type, handler) {
            this.listeners ??= {};
            this.listeners[type] = handler;
        }

        remove() {
            if (!this.parentNode) return;
            this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
            this.parentNode = null;
        }
    }

    class FakeIntersectionObserver {
        constructor(callback, options) {
            this.callback = callback;
            this.options = options;
            this.observed = [];
            this.disconnected = false;
            imageObservers.push(this);
        }

        observe(element) {
            this.observed.push(element);
        }

        disconnect() {
            this.disconnected = true;
            this.observed = [];
        }

        trigger(entries) {
            this.callback(entries);
        }
    }

    const state = {
        assets: [],
        total: 0,
        hasMore: false,
        query: '',
        personIds: [],
        dateFrom: null,
        dateTo: null,
        mediaType: 'ALL',
        cameraMake: '',
        cameraModel: '',
        country: '',
        city: '',
        isLoading: false,
        lightboxAssetId: null,
        lightboxIndex: -1,
    };

    const elements = {
        gallery: new FakeElement('div'),
        galleryLoading: new FakeElement('div'),
        loadMoreContainer: new FakeElement('div'),
        emptyState: new FakeElement('div'),
        errorState: new FakeElement('div'),
        activeFilters: new FakeElement('div'),
    };

    const document = {
        documentElement: { dataset: { view: 'dense' } },
        createElement: (tagName) => new FakeElement(tagName),
        createDocumentFragment: () => new FakeElement('#fragment'),
        getElementById: () => null,
        querySelectorAll: () => [],
    };

    const API = {
        getThumbnailUrl(assetId, size) {
            return `/api/assets/${assetId}/thumbnail?size=${size}`;
        },
        getOriginalUrl(assetId) {
            throw new Error(`gallery must not request originals (${assetId})`);
        },
    };

    const window = {};
    const context = vm.createContext({
        API,
        Gallery: undefined,
        IntersectionObserver: FakeIntersectionObserver,
        Lightbox: { open() {} },
        State: {
            getProperty(key) {
                return state[key];
            },
            set(updates) {
                Object.assign(state, updates);
            },
            subscribe() {
                return () => {};
            },
        },
        console: { error() {} },
        document,
        window,
    });

    vm.runInContext(fs.readFileSync(galleryPath, 'utf8'), context, {
        filename: galleryPath,
    });

    const gallery = window.Gallery;
    gallery.elements = { ...gallery.elements, ...elements };

    return {
        document,
        gallery,
        imageObservers,
        removedSources,
        requestedImages,
        state,
    };
}

const photo = (id, extras = {}) => ({
    id,
    type: 'IMAGE',
    originalFileName: `${id}.jpg`,
    localDateTime: '2026-09-14T10:00:00.000Z',
    ...extras,
});

function renderAssets(harness, ids) {
    harness.state.assets = ids.map((id) => photo(id));
    harness.gallery.render();
    return harness.gallery.elements.gallery.querySelectorAll('.gallery-item img');
}

test('dense and comfortable tiles prepare thumbnails while large tiles prepare previews', () => {
    for (const [view, size] of [
        ['dense', 'thumbnail'],
        ['comfortable', 'thumbnail'],
        ['large', 'preview'],
    ]) {
        const harness = makeHarness();
        harness.document.documentElement.dataset.view = view;

        const item = harness.gallery.createGalleryItem(photo('photo-a'), 0);
        const [img] = item.querySelectorAll('img');

        assert.equal(
            img.dataset.src,
            `/api/assets/photo-a/thumbnail?size=${size}`,
            `${view} must request the ${size} derivative`,
        );
        assert.equal(img.getAttribute('src'), null);
        assert.equal(img.decoding, 'async');
        assert.equal(img.fetchPriority, 'low');
        assert.deepEqual(harness.requestedImages, []);
    }
});

test('tiles request nothing until the image observer reports them near the viewport', () => {
    const harness = makeHarness();
    const [img] = renderAssets(harness, ['photo-a']);

    assert.equal(harness.imageObservers.length, 1);
    assert.equal(harness.imageObservers[0].options.rootMargin, '200px');
    assert.deepEqual(harness.imageObservers[0].observed, [img]);
    assert.equal(img.getAttribute('src'), null);
    assert.deepEqual(harness.requestedImages, []);

    harness.imageObservers[0].trigger([{ target: img, isIntersecting: true }]);

    assert.deepEqual(harness.requestedImages, ['/api/assets/photo-a/thumbnail?size=thumbnail']);
    assert.ok(!harness.requestedImages.some((url) => url.includes('/original')));
});

test('opening the lightbox pauses new tiles, cancels unfinished ones, and resumes on close', () => {
    const harness = makeHarness();
    const [first, second, third] = renderAssets(harness, ['photo-a', 'photo-b', 'photo-c']);
    const observer = harness.imageObservers[0];

    observer.trigger([{ target: first, isIntersecting: true }]);
    first.onload();
    observer.trigger([{ target: second, isIntersecting: true }]);
    assert.deepEqual(harness.requestedImages, [
        '/api/assets/photo-a/thumbnail?size=thumbnail',
        '/api/assets/photo-b/thumbnail?size=thumbnail',
    ]);

    harness.gallery.onStateChange(
        { ...harness.state, lightboxAssetId: 'photo-a' },
        { ...harness.state, lightboxAssetId: null },
    );

    assert.equal(first.getAttribute('src'), '/api/assets/photo-a/thumbnail?size=thumbnail');
    assert.deepEqual(harness.removedSources, [second]);

    observer.trigger([{ target: third, isIntersecting: true }]);
    assert.equal(third.getAttribute('src'), null);

    harness.gallery.onStateChange(
        { ...harness.state, lightboxAssetId: null },
        { ...harness.state, lightboxAssetId: 'photo-a' },
    );

    assert.equal(second.getAttribute('src'), '/api/assets/photo-b/thumbnail?size=thumbnail');
    assert.equal(third.getAttribute('src'), '/api/assets/photo-c/thumbnail?size=thumbnail');
    assert.deepEqual(harness.requestedImages, [
        '/api/assets/photo-a/thumbnail?size=thumbnail',
        '/api/assets/photo-b/thumbnail?size=thumbnail',
        '/api/assets/photo-b/thumbnail?size=thumbnail',
        '/api/assets/photo-c/thumbnail?size=thumbnail',
    ]);
});

test('rerendering disconnects the image observer and drops tile references', () => {
    const harness = makeHarness();
    renderAssets(harness, ['photo-a']);
    const observer = harness.imageObservers[0];

    harness.gallery.clearGallery();

    assert.equal(observer.disconnected, true);
    assert.notEqual(harness.gallery.imageObserver, observer);
    assert.equal(harness.gallery._visibleImages.size, 0);
});

test('changing view mode refreshes visible tiles without loading offscreen ones', () => {
    const harness = makeHarness();
    harness.document.documentElement.dataset.view = 'dense';
    const [visible, offscreen] = renderAssets(harness, ['photo-a', 'photo-b']);
    const observer = harness.imageObservers[0];

    observer.trigger([{ target: visible, isIntersecting: true }]);
    assert.equal(visible.getAttribute('src'), '/api/assets/photo-a/thumbnail?size=thumbnail');

    harness.document.documentElement.dataset.view = 'large';
    harness.gallery.refreshImageSources();

    assert.equal(visible.dataset.src, '/api/assets/photo-a/thumbnail?size=preview');
    assert.equal(visible.getAttribute('src'), '/api/assets/photo-a/thumbnail?size=preview');
    assert.equal(offscreen.dataset.src, '/api/assets/photo-b/thumbnail?size=preview');
    assert.equal(offscreen.getAttribute('src'), null);

    harness.document.documentElement.dataset.view = 'comfortable';
    harness.gallery.refreshImageSources();

    assert.equal(visible.getAttribute('src'), '/api/assets/photo-a/thumbnail?size=thumbnail');
    assert.equal(offscreen.dataset.src, '/api/assets/photo-b/thumbnail?size=thumbnail');

    observer.trigger([{ target: offscreen, isIntersecting: true }]);
    assert.equal(offscreen.getAttribute('src'), '/api/assets/photo-b/thumbnail?size=thumbnail');
});
