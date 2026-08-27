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

    // Zoom/pan state for the image view. Scale is clamped to [MIN_ZOOM, MAX_ZOOM];
    // x/y are screen-space pixel offsets applied after scaling (translate() runs
    // after scale() in `translate(x,y) scale(s)`, so they stay in unscaled px).
    zoom: { scale: 1, x: 0, y: 0 },
    MIN_ZOOM: 1,
    MAX_ZOOM: 4,
    DOUBLE_CLICK_ZOOM: 2.5,
    _isPanning: false,
    _panPointer: null,
    _pinch: null,
    _lastTap: null,

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
                e.preventDefault();
                if (this.elements.image.hidden) return;
                const factor = Math.exp(-e.deltaY * 0.0015);
                this._zoomAt(this.zoom.scale * factor, e.clientX, e.clientY);
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

        const thumbnailUrl = API.getThumbnailUrl(asset.id, 'thumbnail');
        const previewUrl = API.getThumbnailUrl(asset.id, 'preview');

        img.src = thumbnailUrl;
        img.onload = () => {
            if (generation !== this.mediaGeneration) return;
            this.hideLoading();
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

        newScale = Math.min(this.MAX_ZOOM, Math.max(this.MIN_ZOOM, newScale));

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

    /** Keep the zoomed image from panning past its edges. */
    _clampPan() {
        const img = this.elements.image;
        const media = this.elements.media;
        const maxX = Math.max(0, (img.offsetWidth * this.zoom.scale - media.clientWidth) / 2);
        const maxY = Math.max(0, (img.offsetHeight * this.zoom.scale - media.clientHeight) / 2);
        this.zoom.x = Math.min(maxX, Math.max(-maxX, this.zoom.x));
        this.zoom.y = Math.min(maxY, Math.max(-maxY, this.zoom.y));
    },

    _applyZoomTransform() {
        const { scale, x, y } = this.zoom;
        this.elements.image.style.transform =
            scale > 1.001 ? `translate(${x}px, ${y}px) scale(${scale})` : '';

        const zoomed = scale > 1.001;
        this.elements.image.classList.toggle('zoomed', zoomed);
        if (this.elements.zoomToggle) {
            this.elements.zoomToggle.classList.toggle('active', zoomed);
            this.elements.zoomToggle.setAttribute('aria-label', zoomed ? 'Reset zoom' : 'Zoom in');
        }
    },

    resetZoom() {
        this.zoom = { scale: 1, x: 0, y: 0 };
        this._isPanning = false;
        this._pinch = null;
        this.elements.image.classList.remove('panning');
        this._applyZoomTransform();
    },

    /** Toggle between fit and a fixed zoomed-in level, centered on (clientX, clientY). */
    toggleZoomAt(clientX, clientY) {
        const img = this.elements.image;
        img.classList.add('zoom-transition');
        window.setTimeout(() => img.classList.remove('zoom-transition'), 220);

        if (this.zoom.scale > 1.001) {
            this.resetZoom();
        } else {
            this._zoomAt(this.DOUBLE_CLICK_ZOOM, clientX, clientY);
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

        // Touch: pinch-to-zoom, single-finger pan while zoomed, double-tap to toggle.
        img.addEventListener(
            'touchstart',
            (e) => {
                if (e.touches.length === 2) {
                    const [t1, t2] = e.touches;
                    this._pinch = {
                        startDist: Math.hypot(t2.clientX - t1.clientX, t2.clientY - t1.clientY),
                        startScale: this.zoom.scale,
                    };
                    this._isPanning = false;
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
                    };
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
                    this._zoomAt(this._pinch.startScale * (dist / this._pinch.startDist), midX, midY);
                    return;
                }

                if (e.touches.length === 1 && this._isPanning && this._panPointer) {
                    e.preventDefault();
                    const t = e.touches[0];
                    this.zoom.x = this._panPointer.originX + (t.clientX - this._panPointer.startX);
                    this.zoom.y = this._panPointer.originY + (t.clientY - this._panPointer.startY);
                    this._clampPan();
                    this._applyZoomTransform();
                }
            },
            { passive: false },
        );

        img.addEventListener('touchend', (e) => {
            if (e.touches.length < 2) this._pinch = null;
            if (e.touches.length === 0) this._isPanning = false;
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
