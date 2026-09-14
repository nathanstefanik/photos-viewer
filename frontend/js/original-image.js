/**
 * Owns one fetched, decoded original image and its browser resources.
 */

class OriginalImage {
    constructor() {
        this._generation = 0;
        this._controller = null;
        this._objectUrl = null;
    }

    async load(url) {
        this.clear();

        const generation = this._generation;
        const controller = new AbortController();
        this._controller = controller;
        let objectUrl = null;

        try {
            const response = await fetch(url, {
                signal: controller.signal,
                priority: 'high',
            });
            if (!response.ok) {
                throw new Error(`Original image request failed: ${response.status}`);
            }

            const blob = await response.blob();
            this._throwIfStale(generation, controller);

            objectUrl = window.URL.createObjectURL(blob);
            this._objectUrl = objectUrl;

            const image = new Image();
            image.src = objectUrl;
            await image.decode();
            this._throwIfStale(generation, controller);

            this._controller = null;
            return {
                url: objectUrl,
                naturalWidth: image.naturalWidth,
                naturalHeight: image.naturalHeight,
            };
        } catch (error) {
            if (this._objectUrl === objectUrl && objectUrl) {
                window.URL.revokeObjectURL(objectUrl);
                this._objectUrl = null;
            }
            if (this._controller === controller) this._controller = null;

            if (generation !== this._generation || controller.signal.aborted) {
                throw new DOMException('The operation was aborted.', 'AbortError');
            }
            throw error;
        }
    }

    clear() {
        this._generation += 1;

        if (this._controller) {
            this._controller.abort();
            this._controller = null;
        }

        if (this._objectUrl) {
            window.URL.revokeObjectURL(this._objectUrl);
            this._objectUrl = null;
        }
    }

    _throwIfStale(generation, controller) {
        if (generation !== this._generation || controller.signal.aborted) {
            throw new DOMException('The operation was aborted.', 'AbortError');
        }
    }
}

window.OriginalImage = OriginalImage;
