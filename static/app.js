function filterTable(query, tableId) {
  const table = document.getElementById(tableId);
  if (!table) return;

  const rows = table.querySelectorAll('tbody tr');
  const term = query.toLowerCase().trim();

  rows.forEach((row) => {
    const text = row.textContent.toLowerCase();
    row.style.display = text.includes(term) ? '' : 'none';
  });
}

function initMobileSidebar() {
  const toggle = document.getElementById('menuToggle');
  const sidebar = document.getElementById('sidebarNav');

  if (!toggle || !sidebar) return;

  toggle.addEventListener('click', () => {
    sidebar.classList.toggle('open');
  });

  sidebar.querySelectorAll('a').forEach((link) => {
    link.addEventListener('click', () => {
      if (window.innerWidth <= 920) {
        sidebar.classList.remove('open');
      }
    });
  });
}

function initThemeLamp() {
  const toggle = document.getElementById('theme-lamp-toggle');
  if (!toggle) return;

  const root = document.documentElement;
  const getStoredTheme = () => localStorage.getItem('app-theme') || 'light';
  const setTheme = (theme) => {
    root.setAttribute('data-theme', theme);
    document.body.classList.toggle('theme-dark', theme === 'dark');
    document.body.classList.toggle('theme-light', theme === 'light');
    toggle.setAttribute('data-theme', theme);
    toggle.classList.toggle('theme-on', theme === 'light');
    toggle.classList.toggle('theme-off', theme === 'dark');
    const label = toggle.querySelector('.theme-lamp-label');
    if (label) {
      label.textContent = theme === 'light' ? 'Light' : 'Dark';
    }
  };

  setTheme(getStoredTheme());

  const toggleTheme = () => {
    const nextTheme = toggle.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    localStorage.setItem('app-theme', nextTheme);
    setTheme(nextTheme);
  };

  toggle.addEventListener('click', toggleTheme);
  toggle.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      toggleTheme();
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  initMobileSidebar();
  initThemeLamp();
});
