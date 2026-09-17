const CACHE_NAME = "atlas-mobile-20260917.1";
const STATIC_ASSETS = [
  "/atlas/mobile/",
  "/atlas/mobile/app.css?v=20260917.1",
  "/atlas/mobile/app.js?v=20260917.1",
  "/atlas/mobile/manifest.webmanifest?v=20260917.1"
];

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS)));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.pathname.includes("/api/")) {
    return;
  }
  event.respondWith(
    fetch(request, { cache: "no-store" }).then((response) => {
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
        }
        return response;
      }).catch(() => caches.match(request))
  );
});
