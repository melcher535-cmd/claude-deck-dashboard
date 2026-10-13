// Service Worker fuer Web-Push: zeigt Benachrichtigungen an und oeffnet beim Antippen das Dashboard.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));

self.addEventListener("push", event => {
  let daten = {};
  try { daten = event.data.json(); } catch (e) { daten = { title: "Claude Deck", body: event.data ? event.data.text() : "" }; }
  event.waitUntil(self.registration.showNotification(daten.title || "Claude Deck", {
    body: daten.body || "", icon: "/icon-192.png", badge: "/icon-192.png", data: { url: daten.url || "/" },
  }));
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(fenster => {
    for (const f of fenster) if ("focus" in f) { f.navigate(url); return f.focus(); }
    return self.clients.openWindow(url);
  }));
});
