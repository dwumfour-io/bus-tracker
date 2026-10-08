const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

function worker(fetch) {
    const handlers = {};
    const writes = [];
    const context = vm.createContext({
        self: { location: { origin: 'https://tracker.test' },
            addEventListener: (event, handler) => { handlers[event] = handler; },
            skipWaiting() {},
        },
        console: { log() {} }, URL, Response, fetch,
        caches: {
            open: async () => ({
                addAll: async (paths) => writes.push(...paths),
                put: async (request, response) => writes.push(await response.text()),
            }),
            match: async () => new Response('old cached code'),
        },
    });
    vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'service-worker.js'), 'utf8'), context);
    return { handlers, writes };
}

test('service worker installs without requesting the nonexistent index.html route', async () => {
    const { handlers, writes } = worker();
    let installed;
    handlers.install({ waitUntil: (promise) => { installed = promise; } });
    await installed;
    assert.ok(writes.includes('/'));
    assert.ok(!writes.includes('/index.html'));
});

test('online clients receive updated scripts instead of the cached previous deployment', async () => {
    const { handlers, writes } = worker(async () => new Response('new code'));
    let response;
    let cached;
    handlers.fetch({
        request: { method: 'GET', url: 'https://tracker.test/app.js' },
        respondWith: (promise) => { response = promise; },
        waitUntil: (promise) => { cached = promise; },
    });
    assert.equal(await (await response).text(), 'new code');
    await cached;
    assert.ok(writes.includes('new code'));
});

test('offline static assets use cache but predictions never use cached arrivals', async () => {
    const { handlers } = worker(async () => { throw new Error('offline'); });
    for (const [path, status] of [['/app.js', 200], ['/predictions?route=13', 503]]) {
        let response;
        handlers.fetch({
            request: { method: 'GET', url: `https://tracker.test${path}` },
            respondWith: (promise) => { response = promise; }, waitUntil() {},
        });
        assert.equal((await response).status, status);
    }
});
