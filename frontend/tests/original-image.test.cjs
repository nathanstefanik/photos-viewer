const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const modulePath = path.join(__dirname, '..', 'js', 'original-image.js');

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
    });
    return { promise, resolve, reject };
}

function loadOriginalImage() {
    const requests = [];
    const images = [];
    const createdUrls = [];
    const revokedUrls = [];

    class FakeImage {
        constructor() {
            this.naturalWidth = 4032;
            this.naturalHeight = 3024;
            this.decodeResult = deferred();
            images.push(this);
        }

        decode() {
            return this.decodeResult.promise;
        }
    }

    const window = {
        URL: {
            createObjectURL(blob) {
                const url = `blob:original-${createdUrls.length + 1}`;
                createdUrls.push({ blob, url });
                return url;
            },
            revokeObjectURL(url) {
                revokedUrls.push(url);
            },
        },
    };
    const context = vm.createContext({
        AbortController,
        DOMException,
        Image: FakeImage,
        fetch(url, options) {
            const result = deferred();
            requests.push({ url, options, result });
            return result.promise;
        },
        window,
    });
    vm.runInContext(fs.readFileSync(modulePath, 'utf8'), context, {
        filename: modulePath,
    });

    return {
        OriginalImage: window.OriginalImage,
        requests,
        images,
        createdUrls,
        revokedUrls,
    };
}

async function reachDecode(harness, requestIndex, blob) {
    harness.requests[requestIndex].result.resolve({
        ok: true,
        blob: async () => blob,
    });
    await new Promise((resolve) => setImmediate(resolve));
}

test('load fetches the original and resolves only after its blob URL decodes', async () => {
    const harness = loadOriginalImage();
    const loader = new harness.OriginalImage();
    const load = loader.load('/api/assets/photo-a/original');
    let settled = false;
    load.finally(() => {
        settled = true;
    });

    assert.equal(harness.requests.length, 1);
    assert.equal(harness.requests[0].url, '/api/assets/photo-a/original');
    assert.ok(harness.requests[0].options.signal instanceof AbortSignal);
    assert.equal(harness.requests[0].options.priority, 'high');

    const blob = { bytes: 'unchanged-original' };
    await reachDecode(harness, 0, blob);

    assert.equal(settled, false);
    assert.equal(harness.images[0].src, 'blob:original-1');

    harness.images[0].decodeResult.resolve();
    const decoded = await load;

    assert.equal(decoded.url, 'blob:original-1');
    assert.equal(decoded.naturalWidth, 4032);
    assert.equal(decoded.naturalHeight, 3024);
    assert.equal(harness.createdUrls[0].blob, blob);
});

test('clear aborts pending work and releases the loader-owned object URL', async () => {
    const harness = loadOriginalImage();
    const loader = new harness.OriginalImage();
    const pendingFetch = loader.load('/api/assets/photo-a/original');

    loader.clear();

    assert.equal(harness.requests[0].options.signal.aborted, true);

    harness.requests[0].result.reject(
        new DOMException('The operation was aborted.', 'AbortError'),
    );
    await assert.rejects(pendingFetch, { name: 'AbortError' });

    const pendingDecode = loader.load('/api/assets/photo-b/original');
    await reachDecode(harness, 1, { bytes: 'photo-b' });
    loader.clear();

    assert.deepEqual(harness.revokedUrls, ['blob:original-1']);
    harness.images[0].decodeResult.resolve();
    await assert.rejects(pendingDecode, { name: 'AbortError' });
    assert.deepEqual(harness.revokedUrls, ['blob:original-1']);
});

test('a failed response does not create a blob URL', async () => {
    const harness = loadOriginalImage();
    const loader = new harness.OriginalImage();
    const load = loader.load('/api/assets/photo-a/original');

    harness.requests[0].result.resolve({
        ok: false,
        status: 503,
        statusText: 'Unavailable',
    });

    await assert.rejects(load, /503/);
    assert.deepEqual(harness.createdUrls, []);
});

test('decode failure rejects the load and releases its blob URL', async () => {
    const harness = loadOriginalImage();
    const loader = new harness.OriginalImage();
    const load = loader.load('/api/assets/photo-a/original');
    await reachDecode(harness, 0, { bytes: 'unsupported-original' });

    harness.images[0].decodeResult.reject(new Error('decode failed'));

    await assert.rejects(load, /decode failed/);
    assert.deepEqual(harness.revokedUrls, ['blob:original-1']);
});
