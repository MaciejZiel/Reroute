// Runs before the stylesheet paints (a blocking script in <head>), so a light-theme visitor never
// sees a dark first frame. Same rule as initialTheme() in src/theme.ts: the stored choice, else the system.
(function () {
  var theme;
  try { theme = localStorage.getItem("reroute-theme"); } catch { theme = null; }
  if (theme !== "light" && theme !== "dark") {
    theme = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }
  document.documentElement.setAttribute("data-theme", theme);
  var meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", theme === "light" ? "#ffffff" : "#16191c");
})();
