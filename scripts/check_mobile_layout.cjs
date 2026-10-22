// Optional browser regression check: install Playwright, then run against the local app.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const baseURL = process.env.TRACKER_URL || 'http://127.0.0.1:5003';
const output = process.env.SCREENSHOT_DIR || '/private/tmp/bus-tracker-layout';
const widths = [320, 375, 390, 430, 768, 1024, 1440];
const states = ['live', 'scheduled', 'stale', 'unavailable', 'error'];

function predictions(url, state) {
    const route = url.searchParams.get('route');
    const now = Date.now() / 1000;
    const stopName = 'West View Park Dr + West View Towers';
    const arrival = {
        route, minutes: 5, arrival_timestamp: now + 300,
        source_timestamp: now - (state === 'stale' ? 120 : 10),
        status: state === 'scheduled' ? 'Scheduled' : 'Delay unknown',
        is_live: state !== 'scheduled', prediction_type: state === 'scheduled' ? 'scheduled' : 'live',
        time: '8:10 PM', scheduled_time: '8:08 PM', vehicle_id: '1234', stop_number: '619',
    };
    return {
        route, server_time: now, last_updated: '08:05:00 PM',
        stop_name: stopName, directions: ['to_west_view', 'to_downtown'],
        stop_numbers: { outbound: '619', inbound: '619' },
        stops: [{ direction: 'OUTBOUND', id: '619', name: stopName }, { direction: 'INBOUND', id: '619', name: stopName }],
        data_source: state === 'unavailable' ? 'unavailable' : 'truetime+gtfs-rt+gtfs-static',
        is_live: state === 'live', source_age_seconds: state === 'unavailable' ? null : 10,
        live_sources: { 'gtfs-rt': state === 'unavailable' ? 'no_trip_updates' : state },
        feed_expiry: { expired: state === 'unavailable', valid_through: '2026-10-14' },
        predictions: Object.fromEntries(['to_west_view', 'to_downtown'].map(direction => [direction, {
            stop_name: stopName, stop_number: '619', arrivals: state === 'unavailable' ? [] : [arrival],
            service_status: { state: 'unavailable', message: 'Live tracking unavailable and no current schedule could be checked.' },
        }])),
    };
}

async function checkLayout(page, label) {
    const problems = await page.evaluate(() => {
        const issues = [];
        if (document.documentElement.scrollWidth > innerWidth) issues.push('page overflows');
        const visible = element => element.getBoundingClientRect().width > 0 && element.getBoundingClientRect().height > 0;
        for (const card of [...document.querySelectorAll('.arrival-card')].filter(visible)) {
            const bounds = card.getBoundingClientRect();
            const children = [...card.children].map(child => child.getBoundingClientRect());
            for (const child of children) {
                if (child.left < bounds.left || child.right > bounds.right + 1) issues.push('card child outside card');
            }
            for (let i = 0; i < children.length; i++) {
                for (let j = i + 1; j < children.length; j++) {
                    const a = children[i], b = children[j];
                    if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 &&
                        Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1) issues.push('card children overlap');
                }
            }
        }
        for (const element of [...document.querySelectorAll('.arrival-info, .status-badge, .status-value, .direction-header, button')].filter(visible)) {
            if (element.scrollWidth > element.clientWidth + 2) issues.push(`text overflows: ${element.className}`);
        }
        return issues;
    });
    assert.deepEqual(problems, [], label);
}

(async () => {
    fs.mkdirSync(output, { recursive: true });
    const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined });
    try {
        for (const width of widths) {
            const page = await browser.newPage({ viewport: { width, height: 900 }, serviceWorkers: 'block' });
            const errors = [];
            page.on('pageerror', error => errors.push(error.message));
            let state = 'live';
            await page.route('**/alerts?*', route => route.fulfill({ json: { alerts: [] } }));
            await page.route('**/predictions?*', route => route.fulfill(state === 'error'
                ? { status: 503, json: { error: 'Temporary upstream interruption' } }
                : { json: predictions(new URL(route.request().url()), state) }));
            await page.goto(baseURL);
            await page.locator('.arrival-card').first().waitFor();
            await page.locator('.toggle-switch').click();
            for (state of states) {
                await Promise.all([
                    page.waitForResponse(response => response.url().includes('/predictions?')),
                    page.locator('#refresh-btn').click(),
                ]);
                await page.waitForTimeout(100);
                await checkLayout(page, `${width}px ${state}`);
                await page.screenshot({ path: path.join(output, `${width}-${state}.png`), fullPage: true });
            }
            state = 'live';
            await page.locator('#stop-select').selectOption('westview');
            await page.locator('#route-select').selectOption('8');
            await page.waitForFunction(() => document.querySelector('#official-schedule').textContent.startsWith('Route 8'));
            assert.match(await page.locator('#official-schedule').getAttribute('href'), /\/8_EFF_/);
            await page.locator('#route-select').selectOption('13');
            await page.waitForFunction(() => document.querySelector('#official-schedule').textContent.startsWith('Route 13'));
            for (const tab of ['downtown', 'westview', 'both']) {
                await page.locator(`[data-tab="${tab}"]`).click();
                await checkLayout(page, `${width}px ${tab}`);
            }
            assert.deepEqual(errors, [], 'browser JavaScript errors');
            console.log(`PASS ${width}px: ${states.join(', ')}, route switching, direction tabs`);
            await page.close();
        }
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
