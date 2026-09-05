/*!
 * Color mode toggler for Bootstrap's docs (https://getbootstrap.com/)
 * Copyright 2011-2023 The Bootstrap Authors
 * Licensed under the Creative Commons Attribution 3.0 Unported License.
 */

(() => {
  'use strict';

  const getStoredTheme = () => localStorage.getItem('theme');
  const setStoredTheme = (theme) => localStorage.setItem('theme', theme);

  // No stored choice means Auto: follow the system and show Auto as selected.
  const getPreferredTheme = () => getStoredTheme() || 'auto';

  const setTheme = (theme) => {
    const systemDark = window.matchMedia(
      '(prefers-color-scheme: dark)',
    ).matches;
    const resolved = theme === 'auto' ? (systemDark ? 'dark' : 'light') : theme;
    document.documentElement.setAttribute('data-bs-theme', resolved);
  };

  setTheme(getPreferredTheme());

  const showActiveTheme = (theme, focus = false) => {
    const themeSwitcher = document.querySelector('#bd-theme');

    if (!themeSwitcher) {
      return;
    }

    const themeSwitcherText = document.querySelector('#bd-theme-text');
    const activeThemeIcon = document.querySelector('.theme-icon-active use');
    // More than one switcher can be on the page (account drawer, mobile
    // toolbar), so mark every button for the chosen theme, not only the first.
    const buttonsToActive = document.querySelectorAll(
      `[data-bs-theme-value="${theme}"]`,
    );
    if (!buttonsToActive.length) {
      return;
    }
    const btnToActive = buttonsToActive[0];
    const svgOfActiveBtn = btnToActive
      .querySelector('svg use')
      .getAttribute('href');

    document.querySelectorAll('[data-bs-theme-value]').forEach((element) => {
      element.classList.remove('active');
      element.setAttribute('aria-pressed', 'false');
    });

    buttonsToActive.forEach((element) => {
      element.classList.add('active');
      element.setAttribute('aria-pressed', 'true');
    });
    if (activeThemeIcon) {
      activeThemeIcon.setAttribute('href', svgOfActiveBtn);
    }
    const themeSwitcherLabel = `${themeSwitcherText.textContent} (${btnToActive.dataset.bsThemeValue})`;
    themeSwitcher.setAttribute('aria-label', themeSwitcherLabel);

    if (focus) {
      themeSwitcher.focus();
    }
  };

  window
    .matchMedia('(prefers-color-scheme: dark)')
    .addEventListener('change', () => {
      const storedTheme = getStoredTheme();
      if (storedTheme !== 'light' && storedTheme !== 'dark') {
        setTheme(getPreferredTheme());
      }
    });

  window.addEventListener('DOMContentLoaded', () => {
    showActiveTheme(getPreferredTheme());

    document.querySelectorAll('[data-bs-theme-value]').forEach((toggle) => {
      toggle.addEventListener('click', () => {
        const theme = toggle.getAttribute('data-bs-theme-value');
        setStoredTheme(theme);
        setTheme(theme);
        showActiveTheme(theme, true);
      });
    });
  });
})();
