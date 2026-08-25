/**
 * Lightbox sidebar: emoji reactions + comments.
 * Emoji input uses the system emoji keyboard (Apple on Apple devices).
 */

const Social = {
    STORAGE_KEY: 'viewer_identity',
    people: [],
    reactions: [],
    comments: [],
    assetId: null,
    busy: false,

    elements: {
        section: null,
        identityName: null,
        identitySearch: null,
        identityResults: null,
        clearIdentity: null,
        reactionList: null,
        emojiInput: null,
        addReaction: null,
        commentList: null,
        commentInput: null,
        commentSubmit: null,
    },

    init() {
        this.elements.section = document.getElementById('social-section');
        this.elements.identityName = document.getElementById('social-identity-name');
        this.elements.identitySearch = document.getElementById('social-identity-search');
        this.elements.identityResults = document.getElementById('social-identity-results');
        this.elements.clearIdentity = document.getElementById('social-identity-clear');
        this.elements.reactionList = document.getElementById('social-reactions');
        this.elements.emojiInput = document.getElementById('social-emoji-input');
        this.elements.addReaction = document.getElementById('social-add-reaction');
        this.elements.commentList = document.getElementById('social-comments');
        this.elements.commentInput = document.getElementById('social-comment-input');
        this.elements.commentSubmit = document.getElementById('social-comment-submit');

        if (!this.elements.section) return;

        this.ensureIdentity();
        this.renderIdentity();

        this.elements.clearIdentity?.addEventListener('click', () => {
            this.setIdentity({ displayName: this.randomGuestName(), personId: null });
            this.renderIdentity();
            this.elements.identitySearch.value = '';
            this.elements.identityResults.hidden = true;
        });

        this.elements.identitySearch?.addEventListener('input', () => {
            this.renderPersonSuggestions(this.elements.identitySearch.value);
        });

        this.elements.identitySearch?.addEventListener('keydown', (e) => {
            if (e.key !== 'Enter') return;
            e.preventDefault();
            const q = this.elements.identitySearch.value.trim();
            if (!q) return;

            const match = this.findPersonByName(q);
            if (match) {
                this.setIdentity({ displayName: match.name, personId: match.id });
            } else {
                this.setIdentity({ displayName: q, personId: null });
            }
            this.renderIdentity();
            this.elements.identityResults.hidden = true;
            this.elements.identitySearch.value = '';
        });

        this.elements.addReaction?.addEventListener('click', () => {
            this.elements.emojiInput.focus();
        });

        this.elements.emojiInput?.addEventListener('input', () => {
            const emoji = this.firstEmoji(this.elements.emojiInput.value);
            this.elements.emojiInput.value = '';
            if (emoji) this.toggleReaction(emoji);
        });

        this.elements.commentSubmit?.addEventListener('click', () => this.submitComment());
        this.elements.commentInput?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.submitComment();
            }
        });

        // Keep arrow keys from navigating the lightbox while typing
        for (const el of [
            this.elements.identitySearch,
            this.elements.emojiInput,
            this.elements.commentInput,
        ]) {
            el?.addEventListener('keydown', (e) => e.stopPropagation());
        }

        this.loadPeople();
    },

    randomGuestName() {
        const alphabet = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ';
        let suffix = '';
        for (let i = 0; i < 4; i++) {
            suffix += alphabet[Math.floor(Math.random() * alphabet.length)];
        }
        return `Guest-${suffix}`;
    },

    ensureIdentity() {
        const current = this.getIdentity();
        if (!current.displayName) {
            this.setIdentity({ displayName: this.randomGuestName(), personId: null });
        }
    },

    getIdentity() {
        try {
            const raw = localStorage.getItem(this.STORAGE_KEY);
            if (!raw) return { displayName: '', personId: null };
            const parsed = JSON.parse(raw);
            return {
                displayName: typeof parsed.displayName === 'string' ? parsed.displayName : '',
                personId: typeof parsed.personId === 'string' ? parsed.personId : null,
            };
        } catch {
            return { displayName: '', personId: null };
        }
    },

    setIdentity(identity) {
        localStorage.setItem(
            this.STORAGE_KEY,
            JSON.stringify({
                displayName: identity.displayName || this.randomGuestName(),
                personId: identity.personId || null,
            }),
        );
    },

    identityPayload() {
        const identity = this.getIdentity();
        const payload = { displayName: identity.displayName };
        if (identity.personId) payload.personId = identity.personId;
        return payload;
    },

    renderIdentity() {
        const identity = this.getIdentity();
        if (!this.elements.identityName) return;
        this.elements.identityName.textContent = identity.displayName || 'Guest';
        this.elements.identityName.dataset.personId = identity.personId || '';
    },

    async loadPeople() {
        try {
            const data = await API.getPeople(false);
            this.people = data.people || [];
        } catch (error) {
            console.error('Failed to load people for identity:', error);
            this.people = [];
        }
    },

    findPersonByName(name) {
        const needle = name.trim().toLowerCase();
        return this.people.find((p) => (p.name || '').toLowerCase() === needle) || null;
    },

    renderPersonSuggestions(query) {
        const box = this.elements.identityResults;
        if (!box) return;

        const q = (query || '').trim().toLowerCase();
        if (!q) {
            box.hidden = true;
            box.innerHTML = '';
            return;
        }

        const matches = this.people
            .filter((p) => (p.name || '').toLowerCase().includes(q))
            .slice(0, 8);

        box.innerHTML = '';

        matches.forEach((person) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'social-person-option';
            btn.innerHTML = `
                <img src="${API.getPersonThumbnailUrl(person.id)}" alt="">
                <span>${escapeHtml(person.name)}</span>
            `;
            btn.addEventListener('click', () => {
                this.setIdentity({ displayName: person.name, personId: person.id });
                this.renderIdentity();
                this.elements.identitySearch.value = '';
                box.hidden = true;
            });
            box.appendChild(btn);
        });

        const custom = document.createElement('button');
        custom.type = 'button';
        custom.className = 'social-person-option social-person-custom';
        custom.textContent = `Use name “${query.trim()}”`;
        custom.addEventListener('click', () => {
            this.setIdentity({ displayName: query.trim(), personId: null });
            this.renderIdentity();
            this.elements.identitySearch.value = '';
            box.hidden = true;
        });
        box.appendChild(custom);
        box.hidden = false;
    },

    /** First non-ASCII grapheme (emoji), preferring Intl.Segmenter when available. */
    firstEmoji(value) {
        if (!value) return null;

        if (typeof Intl !== 'undefined' && Intl.Segmenter) {
            const segmenter = new Intl.Segmenter(undefined, { granularity: 'grapheme' });
            for (const { segment } of segmenter.segment(value)) {
                const s = segment.trim();
                if (!s) continue;
                if (/^[\w\s]$/u.test(s)) continue;
                return s;
            }
            return null;
        }

        const match = value.match(
            /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}](?:\u{1F3FB}-\u{1F3FF})?(?:\u200D[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}])*/u,
        );
        return match ? match[0] : null;
    },

    async loadForAsset(assetId) {
        this.assetId = assetId;
        if (!this.elements.section) return;

        this.elements.section.hidden = false;
        this.reactions = [];
        this.comments = [];
        this.renderReactions();
        this.renderComments();

        try {
            const data = await API.getAssetSocial(assetId);
            if (this.assetId !== assetId) return;
            this.reactions = data.reactions || [];
            this.comments = data.comments || [];
            this.renderReactions();
            this.renderComments();
        } catch (error) {
            console.error('Failed to load social data:', error);
        }
    },

    clear() {
        this.assetId = null;
        this.reactions = [];
        this.comments = [];
        if (this.elements.reactionList) this.elements.reactionList.innerHTML = '';
        if (this.elements.commentList) this.elements.commentList.innerHTML = '';
        if (this.elements.identityResults) this.elements.identityResults.hidden = true;
    },

    renderReactions() {
        const list = this.elements.reactionList;
        if (!list) return;
        list.innerHTML = '';

        this.reactions.forEach((reaction) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'social-reaction' + (reaction.reacted ? ' is-mine' : '');
            btn.title = (reaction.names || []).join(', ') || reaction.emoji;
            btn.innerHTML =
                `<span class="social-reaction-emoji">${reaction.emoji}</span>` +
                `<span class="social-reaction-count">${reaction.count}</span>`;
            btn.addEventListener('click', () => this.toggleReaction(reaction.emoji));
            list.appendChild(btn);
        });
    },

    renderComments() {
        const list = this.elements.commentList;
        if (!list) return;
        list.innerHTML = '';

        if (!this.comments.length) {
            const empty = document.createElement('p');
            empty.className = 'social-empty';
            empty.textContent = 'No comments yet';
            list.appendChild(empty);
            return;
        }

        this.comments.forEach((comment) => {
            const item = document.createElement('div');
            item.className = 'social-comment';
            const when = comment.createdAt
                ? new Date(comment.createdAt * 1000).toLocaleString()
                : '';
            item.innerHTML = `
                <div class="social-comment-header">
                    <strong>${escapeHtml(comment.displayName)}</strong>
                    <span class="social-comment-time">${escapeHtml(when)}</span>
                </div>
                <p class="social-comment-body">${escapeHtml(comment.body)}</p>
            `;
            if (comment.mine) {
                const del = document.createElement('button');
                del.type = 'button';
                del.className = 'social-comment-delete';
                del.textContent = 'Delete';
                del.addEventListener('click', () => this.deleteComment(comment.id));
                item.querySelector('.social-comment-header').appendChild(del);
            }
            list.appendChild(item);
        });
    },

    async toggleReaction(emoji) {
        if (!this.assetId || this.busy || !emoji) return;
        this.busy = true;
        try {
            const data = await API.toggleReaction(this.assetId, {
                emoji,
                ...this.identityPayload(),
            });
            this.reactions = data.reactions || [];
            this.renderReactions();
        } catch (error) {
            console.error('Failed to react:', error);
            alert(error.message || 'Could not add reaction');
        } finally {
            this.busy = false;
        }
    },

    async submitComment() {
        if (!this.assetId || this.busy) return;
        const body = (this.elements.commentInput?.value || '').trim();
        if (!body) return;

        this.busy = true;
        try {
            const comment = await API.addComment(this.assetId, {
                body,
                ...this.identityPayload(),
            });
            this.comments = [...this.comments, comment];
            this.elements.commentInput.value = '';
            this.renderComments();
        } catch (error) {
            console.error('Failed to comment:', error);
            alert(error.message || 'Could not post comment');
        } finally {
            this.busy = false;
        }
    },

    async deleteComment(commentId) {
        if (!commentId || this.busy) return;
        this.busy = true;
        try {
            await API.deleteComment(commentId);
            this.comments = this.comments.filter((c) => c.id !== commentId);
            this.renderComments();
        } catch (error) {
            console.error('Failed to delete comment:', error);
            alert(error.message || 'Could not delete comment');
        } finally {
            this.busy = false;
        }
    },

};

window.Social = Social;
