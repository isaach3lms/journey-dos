// Service worker for the member app.
//
// Two caches on purpose:
//
//   {{ cache_version }}-assets   CSS, icons, the offline page. No data about
//                                anybody, so cached hard and served fast.
//   {{ cache_version }}-pages    Pages that render a person. Deleted the
//                                moment somebody signs out.
//
// A church tablet and a family iPad are both shared devices. A cache that
// outlives a session would show the next person somebody else's giving
// history, household PIN, or conversations.

const VERSION = '{{ cache_version }}';
const ASSETS = VERSION + '-assets';
const PAGES = VERSION + '-pages';
const OFFLINE_URL = '{{ offline_url }}';
const PRECACHE = {{ assets | tojson }};

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(ASSETS).then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  // Any cache from an older VERSION is deleted, which is how a deploy reaches
  // a phone that already has the old files.
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(
        names.filter((name) => !name.startsWith(VERSION))
             .map((name) => caches.delete(name))
      ))
      .then(() => self.clients.claim())
  );
});

// Sign-out sends this. Everything personal goes immediately.
self.addEventListener('message', (event) => {
  if (event.data === 'clear-personal-cache') {
    event.waitUntil(caches.delete(PAGES));
  }
});

function isPersonal(url) {
  return url.pathname.startsWith('/me/');
}

function isAuth(url) {
  // Never cached. A cached sign-in page is confusing at best and a cached
  // reset link is a security problem.
  return url.pathname.startsWith('/auth/');
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  const url = new URL(request.url);

  // Only this origin. A tenant subdomain gets its own worker and its own
  // caches, so nothing crosses between churches.
  if (url.origin !== self.location.origin) return;

  // Anything that changes data goes straight to the network, always.
  if (request.method !== 'GET') return;
  if (isAuth(url)) return;

  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request).then((hit) => hit || fetch(request).then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(ASSETS).then((cache) => cache.put(request, copy));
        }
        return response;
      }))
    );
    return;
  }

  // Pages: network first, so a member always sees current data when they can.
  // The cache is a fallback for a tunnel or a bad signal, not the default.
  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok && isPersonal(url)) {
          const copy = response.clone();
          caches.open(PAGES).then((cache) => cache.put(request, copy));
        }
        return response;
      })
      .catch(() => caches.match(request).then((hit) => hit || caches.match(OFFLINE_URL)))
  );
});

// A push arrives with a small payload: enough to get somebody to open the app,
// never anything private. Lock screens are readable by whoever is standing
// nearby.
self.addEventListener('push', (event) => {
  let data = { title: 'Update', body: '', url: '/', tag: 'dos' };
  try { data = Object.assign(data, event.data.json()); } catch (e) {}

  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      // A tag replaces rather than stacks. Three copies of the same reminder
      // is how somebody turns notifications off for good.
      tag: data.tag,
      renotify: false,
      icon: '/static/img/icon-192.png',
      badge: '/static/img/icon-192.png',
      data: { url: data.url }
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || '/';

  // Focus a tab that is already open rather than piling up new ones.
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then((windows) => {
        for (const client of windows) {
          if (client.url.includes(target) && 'focus' in client) return client.focus();
        }
        return self.clients.openWindow(target);
      })
  );
});
