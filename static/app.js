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

function initDesktopViewMode() {
  const viewport = document.querySelector('meta[name="viewport"]');
  if (!viewport) return;

  const isDesktopSite = navigator.userAgent.includes('Mobile') && window.innerWidth >= 1024;
  if (isDesktopSite) {
    viewport.setAttribute('content', 'width=1280, initial-scale=1, viewport-fit=cover, minimum-scale=1');
    document.body.classList.add('desktop-view-mode');
  }
}

function initLoginLamp() {
  const lampString = document.getElementById('lamp-string');
  const lampPanel = document.getElementById('login-lamp-panel');
  const loginCard = document.getElementById('login-card');
  const lampLine = document.querySelector('.lamp-line');
  const lampKnob = document.querySelector('.lamp-knob');
  const lampBeam = document.querySelector('.lamp-beam');

  if (!lampString || !lampPanel || !loginCard) return;

  let isOn = true;
  let dragging = false;
  let dragStartY = 0;
  let moved = false;

  const setLampState = (nextState) => {
    isOn = nextState;
    lampPanel.classList.toggle('lamp-off', !isOn);
    lampPanel.classList.toggle('lamp-on', isOn);
    loginCard.classList.toggle('card-off', !isOn);
    lampLine?.classList.toggle('lamp-off', !isOn);
    lampKnob?.classList.toggle('lamp-off', !isOn);
    lampBeam?.classList.toggle('lamp-off', !isOn);
    lampString.setAttribute('aria-checked', isOn ? 'true' : 'false');
    lampString.setAttribute('data-state', isOn ? 'on' : 'off');
  };

  const handlePointerDown = (event) => {
    dragging = true;
    moved = false;
    dragStartY = event.clientY;
    lampString.classList.add('dragging');
  };

  const handlePointerMove = (event) => {
    if (!dragging) return;
    const deltaY = event.clientY - dragStartY;
    if (Math.abs(deltaY) > 24) {
      moved = true;
      setLampState(deltaY < 0);
      dragStartY = event.clientY;
    }
  };

  const handlePointerUp = () => {
    dragging = false;
    lampString.classList.remove('dragging');
  };

  setLampState(true);

  lampString.addEventListener('pointerdown', handlePointerDown);
  window.addEventListener('pointermove', handlePointerMove);
  window.addEventListener('pointerup', handlePointerUp);
  window.addEventListener('pointercancel', handlePointerUp);

  lampString.addEventListener('click', (event) => {
    if (!moved) {
      event.preventDefault();
      setLampState(!isOn);
    }
    moved = false;
  });

  lampString.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      setLampState(!isOn);
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  initMobileSidebar();
  initThemeLamp();
  initDesktopViewMode();
  initLoginLamp();
});
