/**
 * Full-screen media viewer with metadata sidebar.
 */

const Lightbox = {
    elements: {
        lightbox: null,
        overlay: null,
        close: null,
        prev: null,
        next: null,
        media: null,
        image: null,
        video: null,
        loading: null,
        download: null,
        infoToggle: null,
        sidebar: null,
        sidebarClose: null,
        sidebarContent: null,
        zoomToggle: null,
    },

    currentAsset: null,
    currentIndex: -1,
    // Bumps on every displayMedia call so stale video handlers ignore themselves
    mediaGeneration: 0,
    _videoCleanup: null,

    // Zoom/pan state for the image view. Scale is clamped to [MIN_ZOOM, maxZoom],
    // where maxZoom (see _getMaxZoom) is computed per-asset so "100%" always means
    // one native image pixel per device pixel — zoom can never go past what the
    // source actually has. x/y are screen-space pixel offsets applied after scaling
    // (translate() runs after scale() in `translate(x,y) scale(s)`, so they stay in
    // unscaled px).
    zoom: { scale: 1, x: 0, y: 0 },
    MIN_ZOOM: 1,
    HARD_MAX_ZOOM: 8, // sanity ceiling if exif dimensions are missing/bogus
    FALLBACK_MAX_ZOOM: 3, // used before layout/exif are known
    EDGE_HANDOFF_PX: 70, // overdrag past the pan limit that hands off to prev/next
    SWIPE_THRESHOLD_PX: 50, // horizontal drag at Fit that counts as swipe-to-navigate
    _isPanning: false,
    _panPointer: null,
    _pinch: null,
    _lastTap: null,
    _swipePointer: null,
    _wheelZoomSnapTimer: null,

    init() {
        this.elements.lightbox = document.getElementById('lightbox');
        this.elements.overlay = document.getElementById('lightbox-overlay');
        this.elements.close = document.getElementById('lightbox-close');
        this.elements.prev = document.getElementById('lightbox-prev');
        this.elements.next = document.getElementById('lightbox-next');
        this.elements.media = document.getElementById('lightbox-media');
        this.elements.image = document.getElementById('lightbox-image');
        this.elements.video = document.getElementById('lightbox-video');
        this.elements.loading = document.getElementById('lightbox-loading');
        this.elements.download = document.getElementById('lightbox-download');
        this.elements.infoToggle = document.getElementById('lightbox-info-toggle');
        this.elements.sidebar = document.getElementById('lightbox-sidebar');
        this.elements.sidebarClose = document.getElementById('sidebar-close');
        this.elements.sidebarContent = document.getElementById('sidebar-content');
        this.elements.zoomToggle = document.getElementById('lightbox-zoom-toggle');

        this.elements.overlay.addEventListener('click', () => this.close());
        this.elements.close.addEventListener('click', () => this.close());
        this.elements.prev.addEventListener('click', () => this.previous());
        this.elements.next.addEventListener('click', () => this.next());
        this.elements.infoToggle.addEventListener('click', () => this.toggleSidebar());
        this.elements.sidebarClose?.addEventListener('click', () => this.toggleSidebar());
        this.elements.download?.addEventListener('click', (e) => {
            e.preventDefault();
            this.downloadAsset();
        });
        this.elements.zoomToggle?.addEventListener('click', () => {
            const rect = this.elements.media.getBoundingClientRect();
            this.toggleZoomAt(rect.left + rect.width / 2, rect.top + rect.height / 2);
        });

        document.addEventListener('keydown', (e) => this.handleKeyboard(e));
        this.elements.lightbox.addEventListener(
            'wheel',
            (e) => {
                // Let the metadata sidebar scroll normally.
                if (
                    this.elements.sidebar &&
                    !this.elements.sidebar.hidden &&
                    this.elements.sidebar.contains(e.target)
                ) {
                    return;
                }
                if (this.elements.image.hidden) return;

                // A trackpad pinch arrives as wheel+ctrlKey (browsers synthesize this);
                // plain two-finger scroll should pan when zoomed, not always zoom.
                if (e.ctrlKey) {
                    e.preventDefault();
                    const factor = Math.exp(-e.deltaY * 0.0015);
                    this._zoomAt(this.zoom.scale * factor, e.clientX, e.clientY);
                    // Wheel events have no discrete "gesture end" — treat a pause as
                    // the end of the pinch and snap if we landed near a stop.
                    clearTimeout(this._wheelZoomSnapTimer);
                    this._wheelZoomSnapTimer = window.setTimeout(
                        () => this._snapZoomToStop(e.clientX, e.clientY),
                        150,
                    );
                    return;
                }

                if (this.zoom.scale <= this.MIN_ZOOM + 0.001) return;
                e.preventDefault();
                this.zoom.x -= e.deltaX;
                this.zoom.y -= e.deltaY;
                this._clampPan();
                this._applyZoomTransform();
            },
            { passive: false },
        );

        this.initZoomGestures();
    },

    async open(asset, index) {
        this.currentAsset = asset;
        this.currentIndex = index;

        State.set({
            lightboxAssetId: asset.id,
            lightboxIndex: index,
        });

        this.elements.lightbox.hidden = false;
        document.body.style.overflow = 'hidden';
        this.showLoading();

        if (!asset.exifInfo) {
            try {
                this.currentAsset = await API.getAsset(asset.id);
            } catch (error) {
                console.error('Failed to load asset details:', error);
            }
        }

        this.displayMedia(this.currentAsset);
        this.updateDownloadLink(this.currentAsset);
        this.updateNavigation();
        this.updateMetadata(this.currentAsset);

        if (window.Social) {
            Social.loadForAsset(this.currentAsset.id);
        }
    },

    close() {
        this.mediaGeneration++;
        this.elements.lightbox.hidden = true;
        document.body.style.overflow = '';

        this.resetVideo();
        this.resetZoom();
        this.currentAsset = null;
        this.currentIndex = -1;

        if (window.Social) {
            Social.clear();
        }

        State.set({
            lightboxAssetId: null,
            lightboxIndex: -1,
        });
    },

    /** Stop playback, drop sources, and detach video listeners. */
    resetVideo() {
        if (typeof this._videoCleanup === 'function') {
            this._videoCleanup();
            this._videoCleanup = null;
        }

        const video = this.elements.video;
        video.pause();
        video.removeAttribute('src');
        video.innerHTML = '';
        video.removeAttribute('poster');
        delete video.dataset.src;
        delete video.dataset.loaded;
        video.load();
        video.hidden = true;
        video.onerror = null;
    },

    async previous() {
        if (this.currentIndex <= 0) return;
        const assets = State.getProperty('assets');
        const prevAsset = assets[this.currentIndex - 1];
        if (prevAsset) {
            await this.open(prevAsset, this.currentIndex - 1);
        }
    },

    async next() {
        if (this.currentIndex < 0) return;
        const assets = State.getProperty('assets');

        if (this.currentIndex >= assets.length - 1) {
            if (State.getProperty('hasMore')) {
                await Gallery.loadMore();
            }
            return;
        }

        const nextAsset = assets[this.currentIndex + 1];
        if (nextAsset) {
            await this.open(nextAsset, this.currentIndex + 1);
        }
    },

    displayMedia(asset) {
        const generation = ++this.mediaGeneration;
        const isVideo = asset.type === 'VIDEO';

        // Always tear down any prior video so controls never leak onto photos
        this.resetVideo();
        this.resetZoom();
        this.elements.image.hidden = true;
        if (this.elements.zoomToggle) this.elements.zoomToggle.hidden = isVideo;

        if (isVideo) {
            this._displayVideo(asset, generation);
        } else {
            this._displayImage(asset, generation);
        }
    },

    _displayVideo(asset, generation) {
        const video = this.elements.video;
        video.hidden = false;
        video.preload = 'metadata';
        video.poster = API.getThumbnailUrl(asset.id, 'preview');
        video.dataset.src = API.getVideoPlaybackUrl(asset.id);
        video.dataset.loaded = '0';
        this.hideLoading();

        const loadSourceOnce = async () => {
            if (generation !== this.mediaGeneration) return;
            if (video.dataset.loaded === '1') return;
            this.showLoading();

            const source = document.createElement('source');
            source.src = video.dataset.src;
            source.type = 'video/mp4';
            video.appendChild(source);

            video.load();
            video.dataset.loaded = '1';

            const hideLoadingOnReady = () => {
                if (generation !== this.mediaGeneration) return;
                this.hideLoading();
                video.removeEventListener('loadeddata', hideLoadingOnReady);
                video.removeEventListener('canplay', hideLoadingOnReady);
            };
            video.addEventListener('loadeddata', hideLoadingOnReady);
            video.addEventListener('canplay', hideLoadingOnReady);

            try {
                await video.play();
            } catch {
                // Autoplay blocked — user must click play
            } finally {
                if (generation === this.mediaGeneration) this.hideLoading();
            }
        };

        const onUserInitiatedPlay = () => {
            loadSourceOnce();
            video.removeEventListener('click', onUserInitiatedPlay);
            video.removeEventListener('play', onUserInitiatedPlay);
        };

        const onWaiting = () => {
            if (generation === this.mediaGeneration) this.showLoading();
        };
        const onPlaying = () => {
            if (generation === this.mediaGeneration) this.hideLoading();
        };

        video.addEventListener('click', onUserInitiatedPlay);
        video.addEventListener('play', onUserInitiatedPlay);
        video.addEventListener('waiting', onWaiting);
        video.addEventListener('playing', onPlaying);

        video.onerror = () => {
            if (generation !== this.mediaGeneration) return;
            this.hideLoading();
            console.error('Failed to load video');
        };

        this._videoCleanup = () => {
            video.removeEventListener('click', onUserInitiatedPlay);
            video.removeEventListener('play', onUserInitiatedPlay);
            video.removeEventListener('waiting', onWaiting);
            video.removeEventListener('playing', onPlaying);
        };
    },

    _displayImage(asset, generation) {
        const img = this.elements.image;
        img.alt = asset.originalFileName || 'Photo';
        img.hidden = false;
        // Drop any size pin left by a previous asset's full-res swap (see
        // _prefetchFullRes) so this asset's Fit size is computed fresh.
        img.style.width = '';
        img.style.height = '';
        this._fullResRequested = false;
        this._fullResLoaded = false;
        this._fullResFailed = false;

        const thumbnailUrl = API.getThumbnailUrl(asset.id, 'thumbnail');
        const previewUrl = API.getThumbnailUrl(asset.id, 'preview');

        img.src = thumbnailUrl;
        img.onload = () => {
            if (generation !== this.mediaGeneration) return;
            this.hideLoading();
            // Layout/exif are only reliably known once the image has actually
            // rendered — refresh the zoom ceiling and chip now that they are.
            this._updateZoomChip();
        };
        img.onerror = () => {
            if (generation !== this.mediaGeneration) return;
            this.hideLoading();
            console.error('Failed to load thumbnail');
        };

        // Progressive upgrade: show thumbnail first, swap to preview when ready
        const previewImg = new Image();
        previewImg.src = previewUrl;
        previewImg.onload = () => {
            if (this.currentAsset?.id === asset.id && generation === this.mediaGeneration) {
                img.src = previewUrl;
            }
        };
    },

    // ----- Zoom & pan -----

    /** Zoom to `newScale`, keeping the content under (clientX, clientY) fixed on screen. */
    _zoomAt(newScale, clientX, clientY) {
        if (this.elements.image.hidden) return;
        if (newScale > this.MIN_ZOOM + 0.001) this._prefetchFullRes();

        newScale = Math.min(this._getMaxZoom(), Math.max(this.MIN_ZOOM, newScale));

        if (newScale <= this.MIN_ZOOM + 0.001) {
            this.zoom = { scale: 1, x: 0, y: 0 };
        } else {
            const rect = this.elements.media.getBoundingClientRect();
            const cx = clientX - (rect.left + rect.width / 2);
            const cy = clientY - (rect.top + rect.height / 2);
            const ratio = newScale / this.zoom.scale;

            this.zoom.x = cx - (cx - this.zoom.x) * ratio;
            this.zoom.y = cy - (cy - this.zoom.y) * ratio;
            this.zoom.scale = newScale;
            this._clampPan();
        }

        this._applyZoomTransform();
    },

    /** Native (1:1 device-pixel) zoom ceiling for the current asset — "100%" always
     *  means one source pixel per device pixel, never a blown-up preview. Recomputed
     *  live from current layout/exif rather than cached, so it tracks resizes for free. */
    _getMaxZoom() {
        const img = this.elements.image;
        const displayedWidth = img.offsetWidth;
        if (!displayedWidth) return this.FALLBACK_MAX_ZOOM;

        let nativeWidth = this.currentAsset?.exifInfo?.exifImageWidth;

        // If the full-res original failed to decode (HEIC/RAW the browser can't
        // render, etc.), don't let exif metadata promise more detail than what's
        // actually on screen — cap to the loaded preview's own resolution instead.
        if (this._fullResFailed && img.naturalWidth) {
            nativeWidth = nativeWidth ? Math.min(nativeWidth, img.naturalWidth) : img.naturalWidth;
        }

        if (!nativeWidth) return this.FALLBACK_MAX_ZOOM;

        const dpr = window.devicePixelRatio || 1;
        const scale = nativeWidth / dpr / displayedWidth;
        return Math.max(this.MIN_ZOOM, Math.min(scale, this.HARD_MAX_ZOOM));
    },

    /** False when the asset is already displayed at/above native resolution — no
     *  point offering zoom (and it'd otherwise just magnify a soft preview). */
    _zoomAvailable() {
        return this._getMaxZoom() > this.MIN_ZOOM + 0.02;
    },

    /** Lazily fetch the full-resolution original and hot-swap it in once decoded, so
     *  zooming past the preview actually reveals detail instead of magnifying mush.
     *  Safe to call repeatedly/speculatively — only the first call per asset does
     *  anything. Silently gives up (and caps zoom to the preview) if the browser
     *  can't decode the original, e.g. HEIC/RAW sources. */
    _prefetchFullRes() {
        const asset = this.currentAsset;
        if (!asset || asset.type === 'VIDEO' || this._fullResRequested) return;
        this._fullResRequested = true;

        const img = this.elements.image;
        if (!img.decode) {
            this._fullResFailed = true;
            return;
        }

        const generation = this.mediaGeneration;
        const assetId = asset.id;
        const originalUrl = API.getOriginalUrl(assetId);
        const original = new Image();
        original.src = originalUrl;

        original
            .decode()
            .then(() => {
                if (generation !== this.mediaGeneration || this.currentAsset?.id !== assetId) return;
                // Pin the box to its current (preview-derived) Fit size before swapping.
                // Without this, a preview that already fits the viewport at native size
                // would jump when replaced by a much larger original that gets scaled
                // down differently by the max-width/max-height auto-sizing — the pin
                // makes the swap provably seamless regardless of monitor size.
                img.style.width = `${img.offsetWidth}px`;
                img.style.height = `${img.offsetHeight}px`;
                img.src = originalUrl;
                this._fullResLoaded = true;
                this._updateZoomChip();
            })
            .catch(() => {
                if (generation !== this.mediaGeneration || this.currentAsset?.id !== assetId) return;
                this._fullResFailed = true;
                // maxZoom may have just shrunk to the preview's real resolution —
                // pull the current scale back within it if the gesture already
                // zoomed past what the preview actually has.
                const rect = this.elements.media.getBoundingClientRect();
                this._zoomAt(this.zoom.scale, rect.left + rect.width / 2, rect.top + rect.height / 2);
            });

        this._updateZoomChip();
    },

    /** How far the image can pan before its edge would show empty space. */
    _panLimits() {
        const img = this.elements.image;
        const media = this.elements.media;
        return {
            maxX: Math.max(0, (img.offsetWidth * this.zoom.scale - media.clientWidth) / 2),
            maxY: Math.max(0, (img.offsetHeight * this.zoom.scale - media.clientHeight) / 2),
        };
    },

    /** Keep the zoomed image from panning past its edges. With `rubberBand`, allow a
     *  resisted overdrag instead of a hard stop — used while a touch drag is live so
     *  the edge feels soft instead of a wall. */
    _clampPan({ rubberBand = false } = {}) {
        const { maxX, maxY } = this._panLimits();
        if (rubberBand) {
            this.zoom.x = this._rubberBand(this.zoom.x, maxX);
            this.zoom.y = this._rubberBand(this.zoom.y, maxY);
        } else {
            this.zoom.x = Math.min(maxX, Math.max(-maxX, this.zoom.x));
            this.zoom.y = Math.min(maxY, Math.max(-maxY, this.zoom.y));
        }
    },

    _rubberBand(value, max) {
        if (value > max) return max + (value - max) * 0.35;
        if (value < -max) return -max - (-value - max) * 0.35;
        return value;
    },

    /** Animate the pan back within bounds after a released overdrag that didn't
     *  clear the edge-handoff threshold. */
    _springBackPan() {
        this.elements.image.classList.add('zoom-transition');
        this._clampPan();
        this._applyZoomTransform();
        window.setTimeout(() => this.elements.image.classList.remove('zoom-transition'), 220);
    },

    /** After a continuous zoom gesture (pinch, trackpad pinch) ends near a stop
     *  (within 15% of the Fit/100% range), snap to it — keeps the two named stops
     *  the primary interaction model while leaving the gesture itself continuous. */
    _snapZoomToStop(clientX, clientY) {
        const maxZoom = this._getMaxZoom();
        const distToMin = Math.abs(this.zoom.scale - this.MIN_ZOOM);
        const distToMax = Math.abs(this.zoom.scale - maxZoom);
        const nearest = distToMin < distToMax ? this.MIN_ZOOM : maxZoom;
        const threshold = (maxZoom - this.MIN_ZOOM) * 0.15;

        if (Math.min(distToMin, distToMax) > threshold) return;

        this.elements.image.classList.add('zoom-transition');
        this._zoomAt(nearest, clientX, clientY);
        window.setTimeout(() => this.elements.image.classList.remove('zoom-transition'), 220);
    },

    _applyZoomTransform() {
        const { scale, x, y } = this.zoom;
        this.elements.image.style.transform =
            scale > 1.001 ? `translate(${x}px, ${y}px) scale(${scale})` : '';

        this.elements.image.classList.toggle('zoomed', scale > 1.001);
        this._updateZoomChip();
    },

    /** Sync the toolbar zoom chip's label ("Fit" / live % while dragging / "100%")
     *  and visibility with current zoom state. */
    _updateZoomChip() {
        const btn = this.elements.zoomToggle;
        if (!btn) return;

        btn.hidden = this.elements.image.hidden || !this._zoomAvailable();

        const zoomed = this.zoom.scale > this.MIN_ZOOM + 0.001;
        btn.classList.toggle('active', zoomed);
        btn.setAttribute('aria-label', zoomed ? 'Reset zoom' : 'Zoom to 100%');

        const label = btn.querySelector('.zoom-chip-label');
        if (label) {
            const loadingOriginal = this._fullResRequested && !this._fullResLoaded && !this._fullResFailed;
            const text = zoomed ? `${Math.round((this.zoom.scale / this._getMaxZoom()) * 100)}%` : 'Fit';
            label.textContent = loadingOriginal ? `${text} …` : text;
        }
    },

    resetZoom() {
        clearTimeout(this._wheelZoomSnapTimer);
        this.zoom = { scale: 1, x: 0, y: 0 };
        this._isPanning = false;
        this._pinch = null;
        this._swipePointer = null;
        this._lastTap = null;
        this.elements.image.classList.remove('panning');
        this._applyZoomTransform();
    },

    /** Toggle between Fit and native 100%, centered on (clientX, clientY). */
    toggleZoomAt(clientX, clientY) {
        if (this.zoom.scale <= 1.001 && !this._zoomAvailable()) return;

        const img = this.elements.image;
        img.classList.add('zoom-transition');
        window.setTimeout(() => img.classList.remove('zoom-transition'), 220);

        if (this.zoom.scale > 1.001) {
            this.resetZoom();
        } else {
            this._zoomAt(this._getMaxZoom(), clientX, clientY);
        }
    },

    /** Zoom in/out around the media's center, for keyboard shortcuts. */
    _keyboardZoom(factor) {
        if (this.elements.image.hidden) return;
        const rect = this.elements.media.getBoundingClientRect();
        this._zoomAt(this.zoom.scale * factor, rect.left + rect.width / 2, rect.top + rect.height / 2);
    },

    initZoomGestures() {
        const img = this.elements.image;

        // Warm the full-res fetch as soon as a zoom-ish gesture starts (dblclick,
        // pinch, drag) rather than waiting for it to land — the request is the
        // slow part, not the decode.
        img.addEventListener('pointerdown', () => this._prefetchFullRes(), { passive: true });

        img.addEventListener('dblclick', (e) => {
            e.preventDefault();
            this.toggleZoomAt(e.clientX, e.clientY);
        });

        // Mouse drag-to-pan while zoomed in.
        img.addEventListener('mousedown', (e) => {
            if (this.zoom.scale <= 1.001) return;
            e.preventDefault();
            this._isPanning = true;
            this._panPointer = { startX: e.clientX, startY: e.clientY, originX: this.zoom.x, originY: this.zoom.y };
            img.classList.add('panning');
        });
        window.addEventListener('mousemove', (e) => {
            if (!this._isPanning || !this._panPointer) return;
            this.zoom.x = this._panPointer.originX + (e.clientX - this._panPointer.startX);
            this.zoom.y = this._panPointer.originY + (e.clientY - this._panPointer.startY);
            this._clampPan();
            this._applyZoomTransform();
        });
        window.addEventListener('mouseup', () => {
            if (!this._isPanning) return;
            this._isPanning = false;
            img.classList.remove('panning');
        });

        // Touch: pinch-to-zoom (snaps to Fit/100% near release), single-finger pan
        // while zoomed (rubber-bands at the edge and hands off to prev/next on a
        // sustained overdrag), swipe-to-navigate at Fit, and double-tap to toggle.
        img.addEventListener(
            'touchstart',
            (e) => {
                if (e.touches.length === 2) {
                    const [t1, t2] = e.touches;
                    this._pinch = {
                        startDist: Math.hypot(t2.clientX - t1.clientX, t2.clientY - t1.clientY),
                        startScale: this.zoom.scale,
                        lastX: (t1.clientX + t2.clientX) / 2,
                        lastY: (t1.clientY + t2.clientY) / 2,
                    };
                    this._isPanning = false;
                    this._swipePointer = null;
                    return;
                }

                if (e.touches.length !== 1) return;
                const t = e.touches[0];

                const now = Date.now();
                if (
                    this._lastTap &&
                    now - this._lastTap.time < 300 &&
                    Math.hypot(t.clientX - this._lastTap.x, t.clientY - this._lastTap.y) < 30
                ) {
                    this.toggleZoomAt(t.clientX, t.clientY);
                    this._lastTap = null;
                    return;
                }
                this._lastTap = { time: now, x: t.clientX, y: t.clientY };

                if (this.zoom.scale > 1.001) {
                    this._isPanning = true;
                    this._panPointer = {
                        startX: t.clientX,
                        startY: t.clientY,
                        originX: this.zoom.x,
                        originY: this.zoom.y,
                        overdragDir: 0,
                        overdragAmount: 0,
                    };
                } else {
                    this._swipePointer = { startX: t.clientX, startY: t.clientY };
                }
            },
            { passive: true },
        );

        img.addEventListener(
            'touchmove',
            (e) => {
                if (e.touches.length === 2 && this._pinch) {
                    e.preventDefault();
                    const [t1, t2] = e.touches;
                    const dist = Math.hypot(t2.clientX - t1.clientX, t2.clientY - t1.clientY);
                    const midX = (t1.clientX + t2.clientX) / 2;
                    const midY = (t1.clientY + t2.clientY) / 2;
                    this._pinch.lastX = midX;
                    this._pinch.lastY = midY;
                    this._zoomAt(this._pinch.startScale * (dist / this._pinch.startDist), midX, midY);
                    return;
                }

                if (e.touches.length === 1 && this._isPanning && this._panPointer) {
                    e.preventDefault();
                    const t = e.touches[0];
                    const rawX = this._panPointer.originX + (t.clientX - this._panPointer.startX);
                    const rawY = this._panPointer.originY + (t.clientY - this._panPointer.startY);
                    const { maxX } = this._panLimits();

                    // Track how far past the hard limit the finger has dragged, in the
                    // outward direction, so touchend can decide whether to hand off to
                    // prev/next instead of just springing back.
                    this._panPointer.overdragAmount = Math.max(0, Math.abs(rawX) - maxX);
                    this._panPointer.overdragDir =
                        this._panPointer.overdragAmount > 0 ? Math.sign(rawX) : 0;

                    this.zoom.x = rawX;
                    this.zoom.y = rawY;
                    this._clampPan({ rubberBand: true });
                    this._applyZoomTransform();
                    return;
                }

                if (e.touches.length === 1 && this._swipePointer) {
                    const t = e.touches[0];
                    const dx = t.clientX - this._swipePointer.startX;
                    const dy = t.clientY - this._swipePointer.startY;
                    if (Math.abs(dx) > Math.abs(dy)) e.preventDefault();
                }
            },
            { passive: false },
        );

        img.addEventListener('touchend', (e) => {
            if (e.touches.length < 2 && this._pinch) {
                const { lastX, lastY } = this._pinch;
                this._pinch = null;
                this._snapZoomToStop(lastX, lastY);
            }
            if (e.touches.length > 0) return;

            if (this._isPanning && this._panPointer) {
                const { overdragDir, overdragAmount } = this._panPointer;
                this._isPanning = false;
                // Positive x clamp = image's left edge is fully visible = "beginning";
                // dragging further right past that hands off to the previous photo.
                // Symmetric on the other edge/direction for next.
                if (overdragAmount > this.EDGE_HANDOFF_PX) {
                    if (overdragDir > 0) this.previous();
                    else this.next();
                } else {
                    this._springBackPan();
                }
            }

            if (this._swipePointer) {
                const t = e.changedTouches[0];
                const dx = t.clientX - this._swipePointer.startX;
                const dy = t.clientY - this._swipePointer.startY;
                this._swipePointer = null;
                if (Math.abs(dx) > this.SWIPE_THRESHOLD_PX && Math.abs(dx) > Math.abs(dy)) {
                    if (dx > 0) this.previous();
                    else this.next();
                }
            }
        });
    },

    toggleSidebar() {
        this.elements.sidebar.hidden = !this.elements.sidebar.hidden;
    },

    updateNavigation() {
        const assets = State.getProperty('assets');
        const detached = this.currentIndex < 0;

        this.elements.prev.disabled = detached || this.currentIndex <= 0;
        this.elements.prev.style.opacity = this.elements.prev.disabled ? '0.3' : '1';

        this.elements.next.disabled =
            detached || (this.currentIndex >= assets.length - 1 && !State.getProperty('hasMore'));
        this.elements.next.style.opacity = this.elements.next.disabled ? '0.3' : '1';
    },

    updateMetadata(asset) {
        const datetime = asset.localDateTime || asset.fileCreatedAt;
        document.getElementById('meta-datetime').textContent = datetime
            ? new Date(datetime).toLocaleString()
            : '-';

        const exif = asset.exifInfo || {};
        const camera = [exif.make, exif.model].filter(Boolean).join(' ');
        document.getElementById('meta-camera').textContent = camera || '-';
        document.getElementById('meta-lens').textContent = exif.lensModel || '-';

        document.getElementById('meta-focal').textContent = exif.focalLength
            ? `${exif.focalLength}mm`
            : '-';
        document.getElementById('meta-aperture').textContent = exif.fNumber
            ? `f/${exif.fNumber}`
            : '-';
        document.getElementById('meta-shutter').textContent = exif.exposureTime
            ? this.formatShutterSpeed(exif.exposureTime)
            : '-';
        document.getElementById('meta-iso').textContent = exif.iso || '-';

        const locationSection = document.getElementById('meta-location-section');
        const location = [exif.city, exif.state, exif.country].filter(Boolean).join(', ');
        if (location) {
            document.getElementById('meta-location').textContent = location;
            locationSection.hidden = false;
        } else {
            locationSection.hidden = true;
        }

        const peopleSection = document.getElementById('meta-people-section');
        const peopleContainer = document.getElementById('meta-people');
        peopleContainer.innerHTML = '';

        if (asset.people && asset.people.length > 0) {
            asset.people.forEach((person) => {
                const chip = document.createElement('span');
                chip.className = 'person-chip';

                const img = document.createElement('img');
                img.src = API.getPersonThumbnailUrl(person.id);
                img.alt = person.name || '';
                chip.appendChild(img);

                const nameSpan = document.createElement('span');
                nameSpan.textContent = person.name || '';
                chip.appendChild(nameSpan);

                chip.addEventListener('click', () => {
                    this.close();
                    const currentPersonIds = State.getProperty('personIds');
                    if (!currentPersonIds.includes(person.id)) {
                        State.set({
                            personIds: [...currentPersonIds, person.id],
                            page: 1,
                            assets: [],
                        });
                        Filters.updatePeopleChips([...currentPersonIds, person.id]);
                        Filters.showPanel();
                        Gallery.load();
                    }
                });
                peopleContainer.appendChild(chip);
            });
            peopleSection.hidden = false;
        } else {
            peopleSection.hidden = true;
        }

        document.getElementById('meta-filename').textContent = asset.originalFileName || '-';

        const dimensions =
            asset.exifInfo?.exifImageWidth && asset.exifInfo?.exifImageHeight
                ? `${asset.exifInfo.exifImageWidth} × ${asset.exifInfo.exifImageHeight}`
                : '-';
        document.getElementById('meta-dimensions').textContent = dimensions;

        document.getElementById('meta-filesize').textContent = asset.exifInfo?.fileSizeInByte
            ? this.formatFileSize(asset.exifInfo.fileSizeInByte)
            : '-';
    },

    formatShutterSpeed(seconds) {
        let value = seconds;

        if (typeof seconds === 'string') {
            if (seconds.includes('/')) {
                const [num, den] = seconds.split('/').map(parseFloat);
                if (Number.isFinite(num) && Number.isFinite(den) && den !== 0) {
                    value = num / den;
                }
            } else {
                const parsed = parseFloat(seconds);
                if (Number.isFinite(parsed)) {
                    value = parsed;
                }
            }
        }

        if (!Number.isFinite(value) || value <= 0) {
            return '-';
        }

        if (value >= 1) {
            const rounded = value >= 10 ? value.toFixed(0) : value.toFixed(1);
            return `${rounded}s`;
        }
        return `1/${Math.round(1 / value)}s`;
    },

    formatFileSize(bytes) {
        const units = ['B', 'KB', 'MB', 'GB'];
        let size = bytes;
        let unitIndex = 0;

        while (size >= 1024 && unitIndex < units.length - 1) {
            size /= 1024;
            unitIndex++;
        }

        return `${size.toFixed(1)} ${units[unitIndex]}`;
    },

    updateDownloadLink(asset) {
        if (!this.elements.download) return;
        const isVideo = asset.type === 'VIDEO';
        this.elements.download.hidden = isVideo;
        this.elements.download.disabled = isVideo;

        if (isVideo) return;

        this.elements.download.hidden = false;
        this.elements.download.disabled = false;
        this.elements.download.dataset.assetId = asset.id;
        this.elements.download.dataset.fileName = asset.originalFileName || 'asset';
    },

    async downloadAsset() {
        if (!this.currentAsset) return;

        try {
            const response = await fetch(API.getDownloadUrl(this.currentAsset.id));
            if (!response.ok) {
                throw new Error(`Download failed: ${response.statusText}`);
            }

            const blob = await response.blob();
            const blobUrl = window.URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = blobUrl;
            link.download = this.currentAsset.originalFileName || 'asset';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            window.URL.revokeObjectURL(blobUrl);
        } catch (error) {
            console.error('Failed to download asset:', error);
            alert('Download failed. Please try again.');
        }
    },

    handleKeyboard(e) {
        if (this.elements.lightbox.hidden) return;

        const tag = (e.target && e.target.tagName) || '';
        if (tag === 'INPUT' || tag === 'TEXTAREA' || e.target?.isContentEditable) {
            if (e.key === 'Escape') {
                this.close();
            }
            return;
        }

        switch (e.key) {
            case 'Escape':
                this.close();
                break;
            case 'ArrowLeft':
                e.preventDefault();
                this.previous();
                break;
            case 'ArrowRight':
                e.preventDefault();
                this.next();
                break;
            case 'i':
                this.toggleSidebar();
                break;
            case '+':
            case '=':
                e.preventDefault();
                this._keyboardZoom(1.3);
                break;
            case '-':
            case '_':
                e.preventDefault();
                this._keyboardZoom(1 / 1.3);
                break;
            case '0':
                e.preventDefault();
                this.resetZoom();
                break;
        }
    },

    showLoading() {
        this.elements.loading.hidden = false;
    },

    hideLoading() {
        this.elements.loading.hidden = true;
    },
};

window.Lightbox = Lightbox;
