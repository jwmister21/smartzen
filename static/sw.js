const CACHE_NAME = "smartzen-cache-v2";

self.addEventListener("install", event => {
  console.log("Service Worker instalado");

  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll([
        "/",
        "/static/icon-192.png",
        "/static/icon-512.png"
      ]);
    })
  );
});

self.addEventListener("fetch", event => {
  event.respondWith(
    caches.match(event.request).then(response => {
      return response || fetch(event.request);
    })
  );
});
