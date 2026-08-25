/**
 * Backend proxy client. Browser never talks to Immich directly.
 */

const API = {
    baseUrl: '/api',

    async request(endpoint, options = {}) {
        const url = `${this.baseUrl}${endpoint}`;
        const config = {
            headers: {
                'Content-Type': 'application/json',
                ...options.headers,
            },
            ...options,
        };

        try {
            const response = await fetch(url, config);

            if (response.status === 401) {
                window.location.href = '/gate';
                throw new APIError('Authentication required', 401);
            }

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new APIError(
                    errorData.detail || `HTTP ${response.status}`,
                    response.status,
                );
            }

            if (options.blob) {
                return response.blob();
            }

            return response.json();
        } catch (error) {
            if (error instanceof APIError) {
                throw error;
            }
            throw new APIError(`Network error: ${error.message}`, 0);
        }
    },

    async get(endpoint, params = {}) {
        const searchParams = new URLSearchParams();
        Object.entries(params).forEach(([key, value]) => {
            if (value !== null && value !== undefined && value !== '') {
                searchParams.append(key, value);
            }
        });

        const queryString = searchParams.toString();
        const url = queryString ? `${endpoint}?${queryString}` : endpoint;
        return this.request(url);
    },

    async post(endpoint, data = {}) {
        return this.request(endpoint, {
            method: 'POST',
            body: JSON.stringify(data),
        });
    },

    async delete(endpoint) {
        return this.request(endpoint, { method: 'DELETE' });
    },

    // Health
    async getHealth() {
        return this.get('/health');
    },

    // Session
    async getSession() {
        return this.get('/session');
    },

    // Assets
    async getAsset(assetId) {
        return this.get(`/assets/${assetId}`);
    },

    getThumbnailUrl(assetId, size = 'thumbnail') {
        return `${this.baseUrl}/assets/${assetId}/thumbnail?size=${size}`;
    },

    getDownloadUrl(assetId) {
        return `${this.baseUrl}/assets/${assetId}/download`;
    },

    getVideoPlaybackUrl(assetId) {
        return `${this.baseUrl}/assets/${assetId}/video/playback`;
    },

    // Social (reactions + comments)
    async getAssetSocial(assetId) {
        return this.get(`/assets/${assetId}/social`);
    },

    async toggleReaction(assetId, payload) {
        return this.post(`/assets/${assetId}/reactions`, payload);
    },

    async addComment(assetId, payload) {
        return this.post(`/assets/${assetId}/comments`, payload);
    },

    async deleteComment(commentId) {
        return this.delete(`/comments/${commentId}`);
    },

    // Search
    async search(filters) {
        return this.post('/search', filters);
    },

    async getSearchSuggestions() {
        return this.get('/search/suggestions');
    },

    // People
    async getPeople(withHidden = false) {
        return this.get('/people', { withHidden });
    },

    getPersonThumbnailUrl(personId) {
        return `${this.baseUrl}/people/${personId}/thumbnail`;
    },

    // Albums
    async getAlbums() {
        return this.get('/albums');
    },

    async getAlbum(albumId) {
        return this.get(`/albums/${albumId}`);
    },
};

class APIError extends Error {
    constructor(message, status) {
        super(message);
        this.name = 'APIError';
        this.status = status;
    }
}

window.API = API;
window.APIError = APIError;
