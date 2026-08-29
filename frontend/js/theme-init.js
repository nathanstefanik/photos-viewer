/**
 * Applies the saved theme (and, on pages that have one, thumbnail view mode) before
 * first paint, so there's no flash of the wrong theme. Kept tiny and synchronous;
 * loaded in <head> before any stylesheet-affecting content.
 */
try {
  const preference = localStorage.getItem('theme');
  const prefersDark = matchMedia('(prefers-color-scheme: dark)').matches;
  if (preference === 'light' || (!preference && !prefersDark)) {
    document.documentElement.classList.remove('dark');
  }
  const view = localStorage.getItem('viewMode');
  document.documentElement.dataset.view =
    view === 'dense' || view === 'comfortable' || view === 'large' ? view : 'large';
} catch {}
