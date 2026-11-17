/*
 * Small progressive-enhancement layer for the Patchwork dashboard.
 * No framework, no polling, no fabricated data.
 */
(function () {
  "use strict";

  var root = document.documentElement;
  var SUN_PATH = "M8 12a4 4 0 1 1 0-8 4 4 0 0 1 0 8ZM8 0a.75.75 0 0 1 .75.75v1.5a.75.75 0 0 1-1.5 0V.75A.75.75 0 0 1 8 0Zm0 13a.75.75 0 0 1 .75.75v1.5a.75.75 0 0 1-1.5 0v-1.5A.75.75 0 0 1 8 13ZM2.343 2.343a.75.75 0 0 1 1.061 0l1.06 1.061a.751.751 0 0 1-.018 1.042.751.751 0 0 1-1.042.018l-1.06-1.06a.75.75 0 0 1 0-1.061Zm8.536 8.536a.75.75 0 0 1 1.06 0l1.06 1.06a.75.75 0 0 1-1.06 1.061l-1.06-1.06a.75.75 0 0 1 0-1.06ZM0 8a.75.75 0 0 1 .75-.75h1.5a.75.75 0 0 1 0 1.5H.75A.75.75 0 0 1 0 8Zm13 0a.75.75 0 0 1 .75-.75h1.5a.75.75 0 0 1 0 1.5h-1.5A.75.75 0 0 1 13 8ZM2.343 13.657a.75.75 0 0 1 0-1.061l1.061-1.06a.751.751 0 0 1 1.042.018.751.751 0 0 1 .018 1.042l-1.06 1.06a.75.75 0 0 1-1.061 0Zm8.536-8.536a.75.75 0 0 1 0-1.06l1.06-1.06a.75.75 0 1 1 1.061 1.06l-1.06 1.06a.75.75 0 0 1-1.06 0Z";
  var MOON_PATH = "M9.598 1.591a.749.749 0 0 1 .785-.175 7.001 7.001 0 1 1-8.967 8.967.75.75 0 0 1 .961-.96 5.5 5.5 0 0 0 7.046-7.046.75.75 0 0 1 .175-.786Z";

  function iconMarkup(path) {
    return '<svg class="octicon" viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true"><path d="' + path + '"/></svg>';
  }

  function currentTheme() {
    return root.getAttribute("data-color-mode") === "dark" ? "dark" : "light";
  }

  function syncThemeButton() {
    var button = document.querySelector("[data-theme-toggle]");
    if (!button) {
      return;
    }
    var isDark = currentTheme() === "dark";
    button.innerHTML = iconMarkup(isDark ? SUN_PATH : MOON_PATH);
    button.setAttribute("aria-label", isDark ? "Switch to light color mode" : "Switch to dark color mode");
    button.setAttribute("title", isDark ? "Switch to light color mode" : "Switch to dark color mode");
  }

  var themeToggle = document.querySelector("[data-theme-toggle]");
  if (themeToggle) {
    themeToggle.addEventListener("click", function () {
      var next = currentTheme() === "dark" ? "light" : "dark";
      root.setAttribute("data-color-mode", next);
      try {
        localStorage.setItem("patchwork-theme", next);
      } catch (error) {
        /* Storage can be unavailable in private contexts. */
      }
      syncThemeButton();
    });
  }
  syncThemeButton();

  function fallbackCopy(text) {
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    document.body.removeChild(area);
  }

  document.querySelectorAll("[data-copy]").forEach(function (button) {
    button.addEventListener("click", function () {
      var text = button.getAttribute("data-copy") || "";
      var original = button.innerHTML;

      function done() {
        button.textContent = "Copied";
        window.setTimeout(function () {
          button.innerHTML = original;
        }, 1400);
      }

      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(function () {
          fallbackCopy(text);
          done();
        });
      } else {
        fallbackCopy(text);
        done();
      }
    });
  });
})();
