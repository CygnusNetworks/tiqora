const lang = new URLSearchParams(location.search).get("lang");
if (lang) document.documentElement.lang = lang;
