const CACHE_NAME = 'planner-v2.1';
const urlsToCache = [
  './',
  './index-v2.1.html',
  './manifest.json'
];

// Install event - cache essential files
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        console.log('Opened cache');
        return cache.addAll(urlsToCache);
      })
      .then(() => self.skipWaiting())
  );
});

// Activate event - clean up old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cacheName => {
          if (cacheName !== CACHE_NAME) {
            console.log('Deleting old cache:', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// Fetch event - serve from cache, fallback to network
self.addEventListener('fetch', event => {
  // Skip non-GET requests and Google API calls
  if (event.request.method !== 'GET') return;
  if (event.request.url.includes('googleapis.com')) return;
  if (event.request.url.includes('accounts.google.com')) return;
  if (event.request.url.includes('gstatic.com')) return;

  event.respondWith(
    caches.match(event.request)
      .then(response => {
        // Cache hit - return response
        if (response) {
          // Fetch in background to update cache
          fetch(event.request).then(fetchResponse => {
            if (fetchResponse && fetchResponse.status === 200) {
              caches.open(CACHE_NAME).then(cache => {
                cache.put(event.request, fetchResponse.clone());
              });
            }
          }).catch(() => {});
          return response;
        }

        // Not in cache - fetch from network
        return fetch(event.request).then(response => {
          // Don't cache if not successful
          if (!response || response.status !== 200 || response.type !== 'basic') {
            return response;
          }

          // Clone the response
          const responseToCache = response.clone();

          caches.open(CACHE_NAME).then(cache => {
            cache.put(event.request, responseToCache);
          });

          return response;
        });
      })
      .catch(() => {
        // Offline fallback
        return caches.match('./index-v2.1.html');
      })
  );
});

// Handle messages from the app
self.addEventListener('message', event => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
