/* Tema: sayfa çizilmeden önce uygulanır (yanıp sönmeyi önler). Varsayılan koyu; ?theme=light|dark ile seçilebilir. */
(function () {
  var t = "dark";
  try {
    var q = (location.search.match(/[?&]theme=(light|dark)/) || [])[1];
    if (q) localStorage.setItem("unixai.theme", q);
    t = q || localStorage.getItem("unixai.theme") || "dark";
  } catch (e) {}
  document.documentElement.setAttribute("data-theme", t);
})();
