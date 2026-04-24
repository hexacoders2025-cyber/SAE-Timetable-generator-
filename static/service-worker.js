const CACHE_NAME = "timetable-shell-v2";
const APP_SHELL = [
    "/",
    "/dashboard",
    "/teachers?section=teachers",
    "/teachers?section=classes",
    "/timetable",
    "/teacher_portal",
    "/manifest.webmanifest",
    "/static/css/style.css",
    "/static/css/pwa.css",
    "/static/js/pwa.js",
    "/static/images/logo.png",
    "/static/images/apple-touch-icon.png",
    "/static/images/app-icon-512.png",
    "/static/images/college.jpeg",
];

self.addEventListener("install", function (event) {
    event.waitUntil(
        caches.open(CACHE_NAME).then(function (cache) {
            return cache.addAll(APP_SHELL);
        }).then(function () {
            return self.skipWaiting();
        })
    );
});

self.addEventListener("activate", function (event) {
    event.waitUntil(
        caches.keys().then(function (keys) {
            return Promise.all(
                keys.filter(function (key) {
                    return key !== CACHE_NAME;
                }).map(function (key) {
                    return caches.delete(key);
                })
            );
        }).then(function () {
            return self.clients.claim();
        })
    );
});

self.addEventListener("fetch", function (event) {
    const request = event.request;
    if (request.method !== "GET") {
        return;
    }

    if (request.mode === "navigate") {
        event.respondWith(
            fetch(request).then(function (response) {
                const copy = response.clone();
                caches.open(CACHE_NAME).then(function (cache) {
                    cache.put(request, copy);
                });
                return response;
            }).catch(function () {
                return caches.match(request).then(function (cached) {
                    return cached || caches.match("/");
                });
            })
        );
        return;
    }

    event.respondWith(
        caches.match(request).then(function (cachedResponse) {
            if (cachedResponse) {
                return cachedResponse;
            }

            return fetch(request).then(function (response) {
                if (!response || response.status !== 200 || response.type === "error") {
                    return response;
                }

                const copy = response.clone();
                caches.open(CACHE_NAME).then(function (cache) {
                    cache.put(request, copy);
                });
                return response;
            });
        })
    );
});
