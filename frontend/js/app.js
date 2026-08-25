/**
 * Bootstraps theme, density, gallery, lightbox, social, filters, and albums.
 */

const App = {
    VIEW_MODES: {
        dense: 'Dense',
        comfortable: 'Comfortable',
        large: 'Large',
    },
    DEFAULT_VIEW: 'comfortable',

    async init() {
        this.initTheme();
        this.initViewMode();

        Gallery.init();
        Lightbox.init();
        Social.init();
        Filters.init();
        Albums.init();

        State.loadFromURL();
        await this.checkHealth();
        await Gallery.load();
    },

    /** Thumbnail density: dense | comfortable | large */
    initViewMode() {
        const root = document.documentElement;
        const buttons = [...document.querySelectorAll('.view-btn')];
        const label = document.getElementById('view-label');
        const saved = localStorage.getItem('viewMode');
        const initial = this.VIEW_MODES[saved] ? saved : this.DEFAULT_VIEW;

        const apply = (mode) => {
            const next = this.VIEW_MODES[mode] ? mode : this.DEFAULT_VIEW;
            const prev = root.dataset.view;
            root.dataset.view = next;
            buttons.forEach((btn) => {
                btn.setAttribute('aria-pressed', String(btn.dataset.view === next));
            });
            if (label) {
                label.textContent = this.VIEW_MODES[next];
            }
            localStorage.setItem('viewMode', next);

            // Large mode swaps thumbnail ↔ preview URLs; re-render when crossing that boundary
            if (prev && prev !== next && State.getProperty('assets')?.length) {
                if (prev === 'large' || next === 'large') {
                    Gallery.render();
                }
            }
        };

        buttons.forEach((btn) => {
            btn.addEventListener('click', () => apply(btn.dataset.view));
        });

        apply(initial);
    },

    initTheme() {
        const themeToggle = document.getElementById('theme-toggle');
        const root = document.documentElement;

        const apply = (theme) => {
            if (theme === 'light') {
                root.classList.remove('dark');
            } else {
                root.classList.add('dark');
            }
        };

        const savedTheme = localStorage.getItem('theme');
        const systemPrefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
        if (savedTheme === 'light' || savedTheme === 'dark') {
            apply(savedTheme);
        } else {
            apply(systemPrefersDark ? 'dark' : 'light');
        }

        themeToggle.addEventListener('click', () => {
            const next = root.classList.contains('dark') ? 'light' : 'dark';
            apply(next);
            localStorage.setItem('theme', next);
        });

        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
            if (!localStorage.getItem('theme')) {
                apply(e.matches ? 'dark' : 'light');
            }
        });
    },

    async checkHealth() {
        try {
            const health = await API.getHealth();
            if (health.immich !== 'connected') {
                console.warn('Immich connection issue:', health.immich);
            }
        } catch (error) {
            console.error('Backend health check failed:', error);
        }
    },
};

document.addEventListener('DOMContentLoaded', () => {
    App.init().catch((error) => {
        console.error('Failed to initialize application:', error);
    });
});

window.App = App;
