const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function setup() {
    const elements = new Map();
    const pending = [];
    const context = vm.createContext({
        window: { location: { protocol: 'http:', origin: 'http://localhost' } },
        navigator: { onLine: true },
        document: {
            addEventListener() {},
            querySelectorAll() { return []; },
            getElementById(id) {
                if (!elements.has(id)) elements.set(id, { style: {}, textContent: '', innerHTML: '' });
                return elements.get(id);
            },
        },
        console: { error() {} },
        performance: { now: () => context.clock },
        clock: 0, Date, AbortController, setTimeout, clearTimeout, setInterval, clearInterval,
        fetch(url, options) {
            return new Promise((resolve, reject) => pending.push({ url, options, resolve, reject }));
        },
        rendered: [],
    });
    const run = (code) => vm.runInContext(code, context);
    run(source);
    run('const originalRenderArrivals = renderArrivals; renderArrivals = data => rendered.push(data);');
    return { context, run, pending, elements };
}

function payload(route = '13') {
    return {
        route, server_time: 1000, is_live: true, last_updated: '08:08:30 AM',
        data_source: 'truetime',
        predictions: {
            to_west_view: { arrivals: [] },
            to_downtown: { arrivals: [{ route, minutes: 3, arrival_timestamp: 1180, status: 'On Time' }] },
        },
    };
}

function reply(request, data) {
    request.resolve({ ok: true, json: async () => data });
}

test('West View respects Route 13 instead of requesting both routes', async () => {
    const { run, pending, context } = setup();
    run('currentStop = "westview";');
    const request = run('fetchPredictions()');
    assert.equal(pending[0].url, 'http://localhost/predictions?route=13&stop=westview');
    assert.equal(pending[0].options.cache, 'no-store');
    reply(pending[0], payload());
    await request;
    assert.equal(context.rendered[0].route, '13');
});

test('an old Route 8 response cannot overwrite a new Route 13 selection', async () => {
    const { run, pending, context } = setup();
    run('currentStop = "westview"; currentRoute = "8";');
    const old = run('fetchPredictions()');
    run('currentRoute = "13";');
    const latest = run('fetchPredictions()');
    assert.equal(pending[0].options.signal.aborted, true);
    reply(pending[1], payload('13'));
    await latest;
    reply(pending[0], payload('8'));
    await old;
    assert.equal(context.rendered.length, 1);
    assert.equal(context.rendered[0].route, '13');
});

test('switching stops clears old arrivals while the next request loads', async () => {
    const { run, pending } = setup();
    const first = run('fetchPredictions()');
    reply(pending[0], payload());
    await first;
    run('currentStop = "stop_1016";');
    const second = run('fetchPredictions()');
    assert.equal(run('currentData'), null);
    reply(pending[1], payload());
    await second;
});

test('a failed superseded request cannot erase current arrivals', async () => {
    const { run, pending, context } = setup();
    const old = run('fetchPredictions()');
    const latest = run('fetchPredictions()');
    reply(pending[1], payload());
    await latest;
    pending[0].reject(new Error('old request failed'));
    await old;
    assert.equal(run('currentData.route'), '13');
    assert.equal(context.rendered.length, 1);
});

test('mismatched response route is rejected and mixed arrival rows are filtered', async () => {
    const { run, pending, context } = setup();
    const wrong = run('fetchPredictions()');
    reply(pending[0], payload('8'));
    await wrong;
    assert.equal(context.rendered.length, 0);
    const correct = run('fetchPredictions()');
    const data = payload();
    data.predictions.to_downtown.arrivals.push({ route: '8', minutes: 1 });
    reply(pending[1], data);
    await correct;
    assert.equal(context.rendered[0].predictions.to_downtown.arrivals.length, 1);
});

test('countdown ages between refreshes using the server clock, not the phone clock', () => {
    const { run, context } = setup();
    run('currentData = {server_time: 1000}; currentDataReceivedAt = 0;');
    const expression = 'arrivalTiming({arrival_timestamp: 1190, is_live: true, status: "On Time"})';
    assert.equal(run(expression).display, 3);
    context.clock = 31000;
    assert.equal(run(expression).display, 2);
    context.clock = 75000;
    assert.equal(run(expression).display, 1);
    assert.equal(run(expression).status, 'Due soon');
});

test('a due estimate is displayed without claiming confirmed arrival', () => {
    const { run } = setup();
    run('currentData = {server_time: 1000}; currentDataReceivedAt = 0;');
    const timing = run('arrivalTiming({arrival_timestamp: 1045, is_live: true, status: "On Time"})');
    assert.equal(timing.display, 'Due');
    assert.equal(timing.status, 'Due soon');
    assert.equal(timing.label, 'Estimated');
});

test('scheduled arrivals never get a live approaching badge', () => {
    const { run } = setup();
    run('currentData = {server_time: 1000}; currentDataReceivedAt = 0;');
    const timing = run('arrivalTiming({arrival_timestamp: 1030, is_live: false, status: "Scheduled"})');
    assert.equal(timing.display, 1);
    assert.equal(timing.approaching, false);
    assert.equal(timing.status, 'Scheduled');
});

test('stale and elapsed predictions cannot sit at Due indefinitely', () => {
    const { run, context } = setup();
    run('currentData = {server_time: 1000}; currentDataReceivedAt = 0;');
    const stale = run('arrivalTiming({arrival_timestamp: 1030, source_timestamp: 800, is_live: true})');
    assert.equal(stale.display, '--');
    assert.equal(stale.status, 'Update needed');
    const passed = run('arrivalTiming({arrival_timestamp: 900, is_live: true})');
    assert.equal(passed.status, 'Time passed');
    context.clock = 91000;
    assert.equal(run('arrivalTiming({arrival_timestamp: 1060, is_live: true})').status, 'Update needed');
});

test('source freshness keeps aging and departure predictions are labeled', () => {
    const { run, elements, context } = setup();
    const element = { textContent: '' };
    context.document.querySelectorAll = () => [element];
    run('currentDataReceivedAt = 0;');
    context.clock = 35000;
    run('updateFreshnessDisplay(60)');
    assert.match(element.textContent, /95 seconds ago \(stale\)/);
    run('updateLastUpdated("08:10:00 AM")');
    assert.equal(elements.get('last-updated').textContent, '08:10:00 AM ET');
    assert.match(run('createArrivalCard({minutes: 5, status: "On Time", time: "8:10 AM", event_type: "departure"})'), /Predicted departure/);
});

test('schedule-only display explains the live outage instead of implying a tracked bus', () => {
    const { run, context, elements } = setup();
    context.sample = payload();
    context.sample.directions = ['to_west_view'];
    context.sample.predictions.to_west_view.arrivals = [{ is_live: false, prediction_type: 'scheduled', minutes: 5 }];
    context.sample.live_sources = { 'gtfs-rt': 'unavailable' };
    run('updateLiveStatus(sample)');
    assert.equal(elements.get('connection-status').textContent, 'Schedule only');
    assert.match(elements.get('live-status-banner').textContent, /live feed could not be reached/);
    assert.match(elements.get('live-status-banner').textContent, /scheduled, not tracked/);
});

test('the status indicator stops saying Live when the displayed prediction becomes stale', () => {
    const { run, context, elements } = setup();
    context.sample = payload();
    context.sample.directions = ['to_downtown'];
    context.sample.predictions.to_downtown.arrivals[0].is_live = true;
    run('currentData = sample; currentDataReceivedAt = 0; updateLiveStatus(sample);');
    assert.equal(elements.get('connection-status').textContent, 'Live');
    context.clock = 91000;
    run('updateLiveStatus(sample)');
    assert.equal(elements.get('connection-status').textContent, 'Live data stale');
    assert.equal(elements.get('live-status-banner').hidden, false);
});

test('West View displays an incoming live bus without relabeling it as a downtown departure', () => {
    const { run, context, elements } = setup();
    context.sample = payload();
    context.sample.predictions.to_west_view.arrivals = [{ minutes: 5, route: '13', is_live: true, status: 'Delay unknown' }];
    context.sample.predictions.to_downtown.arrivals = [];
    run('currentStop = "westview"; originalRenderArrivals(sample);');
    assert.match(elements.get('westview-arrivals').innerHTML, /Route 13/);
    assert.doesNotMatch(elements.get('westview-arrivals').innerHTML, /End of Line/);
    assert.doesNotMatch(elements.get('downtown-arrivals').innerHTML, /Route 13/);
});
