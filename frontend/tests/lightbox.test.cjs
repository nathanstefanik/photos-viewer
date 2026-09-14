const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const originalImagePath = path.join(__dirname, '..', 'js', 'original-image.js');
const lightboxPath = path.join(__dirname, '..', 'js', 'lightbox.js');

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
    });
    return { promise, resolve, reject };
}

function classList() {
    return {
        add() {},
        remove() {},
        toggle() {},
    };
}

function makeImageElement(events) {
    let hidden = true;
    let src = 'blob:old-photo';
    return {
        alt: '',
        classList: classList(),
        revealCount: 0,
        style: {},
        decodeResult: deferred(),
        decodeCalls: 0,
        decode() {
            this.decodeCalls += 1;
            return this.decodeResult.promise;
        },
        get src() {
            return src;
        },
        set src(value) {
            src = value;
            if (value.startsWith('blob:')) this.decodeResult = deferred();
        },
        get hidden() {
            return hidden;
        },
        set hidden(value) {
            if (hidden && value === false) this.revealCount += 1;
            hidden = value;
        },
        offsetWidth: 1000,
        removeAttribute(name) {
            if (name === 'src') {
                events.push('remove-src');
                this.src = '';
            }
        },
    };
}

function makeLightboxHarness({ metadata = {} } = {}) {
    const events = [];
    const requests = [];
    const decoders = [];
    const revokedUrls = [];
    const metadataRequests = new Map();
    const metadataUpdates = [];
    const state = {
        assets: [],
        hasMore: false,
        lightboxAssetId: null,
        lightboxIndex: -1,
    };

    class FakeImage {
        constructor() {
            const dimensions = decoders.length === 0
                ? { width: 4000, height: 3000 }
                : { width: 2400, height: 1600 };
            this.naturalWidth = dimensions.width;
            this.naturalHeight = dimensions.height;
            this.decodeResult = deferred();
            decoders.push(this);
        }

        decode() {
            return this.decodeResult.promise;
        }
    }

    const image = makeImageElement(events);
    const elements = {
        lightbox: { hidden: true },
        image,
        video: { hidden: true },
        loading: { hidden: true },
        error: { hidden: true, textContent: '' },
        download: { dataset: {}, hidden: true, disabled: true },
        zoomToggle: { hidden: true, classList: classList(), setAttribute() {}, querySelector() { return null; } },
        media: {
            clientWidth: 1000,
            clientHeight: 800,
            getBoundingClientRect() {
                return { left: 0, top: 0, width: 1000, height: 800 };
            },
        },
    };

    const window = {
        devicePixelRatio: 2,
        URL: {
            createObjectURL() {
                return `blob:photo-${decoders.length + 1}`;
            },
            revokeObjectURL(url) {
                events.push(`revoke:${url}`);
                revokedUrls.push(url);
            },
        },
        setTimeout,
        addEventListener() {},
    };
    const API = {
        getAsset(assetId) {
            const result = deferred();
            metadataRequests.set(assetId, result);
            return result.promise;
        },
        getOriginalUrl(assetId) {
            return `/api/assets/${assetId}/original`;
        },
        getThumbnailUrl() {
            throw new Error('photo derivatives must not be requested by the lightbox');
        },
    };
    const context = vm.createContext({
        AbortController,
        DOMException,
        Image: FakeImage,
        API,
        Gallery: { loadMore: async () => {} },
        Social: undefined,
        State: {
            set(update) {
                Object.assign(state, update);
            },
            getProperty(key) {
                return state[key];
            },
        },
        clearTimeout,
        console: { error() {} },
        document: { body: { style: {} } },
        fetch(url, options) {
            const result = deferred();
            requests.push({ url, options, result });
            return result.promise;
        },
        setTimeout,
        window,
    });

    vm.runInContext(fs.readFileSync(originalImagePath, 'utf8'), context, {
        filename: originalImagePath,
    });
    vm.runInContext(fs.readFileSync(lightboxPath, 'utf8'), context, {
        filename: lightboxPath,
    });

    const lightbox = window.Lightbox;
    lightbox.elements = { ...lightbox.elements, ...elements };
    lightbox.originalImage = new window.OriginalImage();
    lightbox.resetVideo = () => {
        elements.video.hidden = true;
    };
    lightbox.resetZoom = () => {
        lightbox.zoom = { scale: 1, x: 0, y: 0 };
    };
    lightbox.updateNavigation = () => {};
    lightbox.updateMetadata = (asset) => {
        metadataUpdates.push(asset.id);
    };
    Object.assign(metadata, { requests: metadataRequests, updates: metadataUpdates });

    return {
        decoders,
        elements,
        events,
        lightbox,
        metadataRequests,
        metadataUpdates,
        requests,
        revokedUrls,
        state,
    };
}

async function reachDecode(harness, requestIndex) {
    harness.requests[requestIndex].result.resolve({
        ok: true,
        blob: async () => ({ requestIndex }),
    });
    await new Promise((resolve) => setImmediate(resolve));
}

async function settle() {
    await new Promise((resolve) => setImmediate(resolve));
}

const photo = (id, extras = {}) => ({
    id,
    type: 'IMAGE',
    originalFileName: `${id}.jpg`,
    exifInfo: {},
    ...extras,
});

test('opening a photo immediately hides old pixels and requests only its original', async () => {
    const harness = makeLightboxHarness();

    await harness.lightbox.open(photo('photo-a'), 0);

    assert.equal(harness.elements.image.hidden, true);
    assert.equal(harness.elements.image.src, '');
    assert.deepEqual(
        harness.requests.map((request) => request.url),
        ['/api/assets/photo-a/original'],
    );

    await reachDecode(harness, 0);
    assert.equal(harness.elements.image.hidden, true);

    harness.decoders[0].decodeResult.resolve();
    await settle();

    assert.equal(harness.elements.image.src, 'blob:photo-1');
    assert.equal(harness.elements.image.hidden, true);

    harness.elements.image.decodeResult.resolve();
    await settle();

    assert.equal(harness.elements.image.hidden, false);
    assert.equal(harness.elements.image.revealCount, 1);
});

test('rapid navigation cannot reveal a stale decode and releases its blob URL', async () => {
    const harness = makeLightboxHarness();
    await harness.lightbox.open(photo('photo-a'), 0);
    await reachDecode(harness, 0);

    await harness.lightbox.open(photo('photo-b'), 1);

    assert.equal(harness.elements.image.hidden, true);
    assert.equal(harness.elements.image.src, '');
    assert.deepEqual(harness.revokedUrls, ['blob:photo-1']);

    harness.decoders[0].decodeResult.resolve();
    await settle();

    assert.equal(harness.lightbox.currentAsset.id, 'photo-b');
    assert.equal(harness.elements.image.hidden, true);

    await reachDecode(harness, 1);
    harness.decoders[1].decodeResult.resolve();
    await settle();

    assert.equal(harness.elements.image.hidden, true);
    harness.elements.image.decodeResult.resolve();
    await settle();

    assert.equal(harness.elements.image.src, 'blob:photo-2');
    assert.equal(harness.elements.image.hidden, false);
});

test('late metadata cannot replace the selected photo or update its panel', async () => {
    const harness = makeLightboxHarness();
    const openA = harness.lightbox.open(photo('photo-a', { exifInfo: null }), 0);
    await harness.lightbox.open(photo('photo-b'), 1);

    harness.metadataRequests.get('photo-a').resolve(
        photo('photo-a', { exifInfo: { make: 'Late camera' } }),
    );
    await openA;

    assert.equal(harness.lightbox.currentAsset.id, 'photo-b');
    assert.equal(harness.metadataUpdates.at(-1), 'photo-b');
});

test('late metadata after close cannot reopen or update the closed photo', async () => {
    const harness = makeLightboxHarness();
    const opening = harness.lightbox.open(photo('photo-a', { exifInfo: null }), 0);
    harness.lightbox.close();

    harness.metadataRequests.get('photo-a').resolve(
        photo('photo-a', { exifInfo: { make: 'Late camera' } }),
    );
    await opening;

    assert.equal(harness.lightbox.currentAsset, null);
    assert.deepEqual(harness.metadataUpdates, ['photo-a']);
    assert.equal(harness.elements.lightbox.hidden, true);
});

test('original failure stays hidden, reports an accessible error, and keeps download available', async () => {
    const harness = makeLightboxHarness();
    await harness.lightbox.open(photo('photo-a'), 0);

    harness.requests[0].result.resolve({
        ok: false,
        status: 415,
        statusText: 'Unsupported Media Type',
    });
    await settle();

    assert.equal(harness.elements.image.hidden, true);
    assert.equal(harness.elements.error.hidden, false);
    assert.match(harness.elements.error.textContent, /original/i);
    assert.equal(harness.elements.download.hidden, false);
    assert.equal(harness.elements.loading.hidden, true);
});

test('visible image decode failure releases its blob and reports the original error', async () => {
    const harness = makeLightboxHarness();
    await harness.lightbox.open(photo('photo-a'), 0);
    await reachDecode(harness, 0);
    harness.decoders[0].decodeResult.resolve();
    await settle();

    assert.equal(harness.elements.image.decodeCalls, 1);
    harness.elements.image.decodeResult.reject(new Error('visible decode failed'));
    await settle();

    assert.equal(harness.elements.image.hidden, true);
    assert.equal(harness.elements.image.src, '');
    assert.deepEqual(harness.revokedUrls, ['blob:photo-1']);
    assert.equal(harness.elements.error.hidden, false);
});

test('closing aborts a pending original and clears decoded pixels and errors', async () => {
    const harness = makeLightboxHarness();
    await harness.lightbox.open(photo('photo-a'), 0);
    const signal = harness.requests[0].options.signal;
    harness.elements.error.hidden = false;

    harness.lightbox.close();

    assert.equal(signal.aborted, true);
    assert.equal(harness.elements.image.hidden, true);
    assert.equal(harness.elements.image.src, '');
    assert.equal(harness.elements.error.hidden, true);
    assert.equal(harness.lightbox.currentAsset, null);
});

test('closing clears the image source before revoking its displayed blob URL', async () => {
    const harness = makeLightboxHarness();
    await harness.lightbox.open(photo('photo-a'), 0);
    await reachDecode(harness, 0);
    harness.decoders[0].decodeResult.resolve();
    await settle();
    harness.elements.image.decodeResult.resolve();
    await settle();
    harness.events.length = 0;

    harness.lightbox.close();

    assert.deepEqual(harness.events, ['remove-src', 'revoke:blob:photo-1']);
});

test('zoom ceiling uses decoded original dimensions instead of metadata dimensions', async () => {
    const harness = makeLightboxHarness();
    await harness.lightbox.open(
        photo('photo-a', { exifInfo: { exifImageWidth: 120, exifImageHeight: 90 } }),
        0,
    );
    await reachDecode(harness, 0);
    harness.decoders[0].decodeResult.resolve();
    await settle();
    harness.elements.image.decodeResult.resolve();
    await settle();

    assert.equal(harness.lightbox._getMaxZoom(), 2);
});
