// Minimal shell cache. Giving and staff pages are never cached.
const CACHE = "journey-v1";
const SHELL = ["/", "/static/css/app.css", "/static/icons/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  const skip =
    event.request.method !== "GET" ||
    url.pathname.startsWith("/staff") ||
    url.pathname.startsWith("/kiosk") ||
    url.pathname.includes("/give") ||
    url.pathname.startsWith("/account");
  if (skip) return;
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
