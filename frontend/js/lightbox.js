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
    },

    currentAsset: null,
    currentIndex: -1,
    // Bumps on every displayMedia call so stale video handlers ignore themselves
    mediaGeneration: 0,
    _videoCleanup: null,

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

        document.addEventListener('keydown', (e) => this.handleKeyboard(e));
        this.elements.lightbox.addEventListener(
            'wheel',
            (e) => e.preventDefault(),
            { passive: false },
        );
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
        this.elements.image.hidden = true;

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

    toggleSidebar() {
        this.elements.sidebar.hidden = !this.elements.sidebar.hidden;
    },

    updateNavigation() {
        const assets = State.getProperty('assets');

        this.elements.prev.disabled = this.currentIndex <= 0;
        this.elements.prev.style.opacity = this.currentIndex <= 0 ? '0.3' : '1';

        this.elements.next.disabled =
            this.currentIndex >= assets.length - 1 && !State.getProperty('hasMore');
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
