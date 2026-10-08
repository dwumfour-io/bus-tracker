// Pittsburgh Bus Tracker - Main JavaScript
const API_URL = window.location.protocol === 'file:'
    ? 'http://localhost:5001'
    : window.location.origin; // Use localhost when opened as file, relative URL for production
const REFRESH_INTERVAL = 30000; // 30 seconds
const MAX_PREDICTION_AGE_SECONDS = 90;
const DEFAULT_STOP_STORAGE_KEY = 'pixburgh-bus-tracker-default-stop';

let autoRefreshInterval = null;
let currentData = null;
let currentDataReceivedAt = null;
let predictionController = null;
let predictionRequestId = 0;
let displayedSelection = null;
let currentRoute = '13';
let currentStop = 'stop_1009';
let activeTab = 'both';

const stopMetadata = {
    stop_1009: {
        name: 'Center Ave + Chalfonte Ave',
        names: { to_west_view: 'Center Ave + Chalfonte Ave' },
        numbers: { outbound: '1009', inbound: '1009' },
        directions: ['to_west_view'],
        alternate: { direction: 'to_downtown', stopNumber: '1016' }
    },
    stop_1016: {
        name: 'Center Ave + Chalfonte Ave',
        names: { to_downtown: 'Center Ave + Chalfonte Ave' },
        numbers: { outbound: '1016', inbound: '1016' },
        directions: ['to_downtown'],
        alternate: { direction: 'to_west_view', stopNumber: '1009' }
    },
    westview: {
        name: 'West View Plaza Fire Lane + Giant Eagle',
        numbers: { outbound: '619', inbound: '619' },
        directions: ['to_west_view', 'to_downtown']
    },
    stop_620: {
        name: 'West View Plaza Fire Lane + U-Haul',
        numbers: { outbound: '620', inbound: '620' },
        directions: ['to_west_view', 'to_downtown']
    },
    stop_618: {
        name: 'West View Park Dr + West View Towers',
        names: { to_west_view: 'West View Park Dr + West View Towers' },
        numbers: { outbound: '618', inbound: '618' },
        directions: ['to_west_view'],
        alternate: { direction: 'to_downtown', stopNumber: '733' }
    },
    stop_733: {
        name: 'West View Park Dr + West View Tower',
        names: { to_downtown: 'West View Park Dr + West View Tower' },
        numbers: { outbound: '733', inbound: '733' },
        directions: ['to_downtown'],
        alternate: { direction: 'to_west_view', stopNumber: '618' }
    }
};

// Route-stop compatibility mapping
const routeStopCompatibility = {
    '8': ['stop_618', 'stop_733', 'westview', 'stop_620'],
    '13': ['stop_1009', 'stop_1016', 'stop_618', 'stop_733', 'westview', 'stop_620']
};

function formatStopNumbers(stopNumbers) {
    if (!stopNumbers) return '';
    const outbound = stopNumbers.outbound;
    const inbound = stopNumbers.inbound;

    if (outbound && inbound && outbound !== inbound) {
        return `Stops #${outbound} / #${inbound}`;
    }

    const stopNumber = outbound || inbound;
    return stopNumber ? `Stop #${stopNumber}` : '';
}

function updateStopNumberDisplay(stopNumbers = null) {
    const display = document.getElementById('stop-number-display');
    if (!display) return;

    const numbers = stopNumbers || stopMetadata[currentStop]?.numbers;
    display.textContent = formatStopNumbers(numbers);
}

function updateDirectionStopNumbers(predictions = null) {
    const fallbackNumbers = stopMetadata[currentStop]?.numbers || {};
    const stopNumbers = {
        to_west_view: predictions?.to_west_view?.stop_number || fallbackNumbers.outbound,
        to_downtown: predictions?.to_downtown?.stop_number || fallbackNumbers.inbound
    };

    Object.entries(stopNumbers).forEach(([direction, stopNumber]) => {
        document.querySelectorAll(`[data-stop-number="${direction}"]`).forEach((element) => {
            element.textContent = stopNumber ? `Stop #${stopNumber}` : '';
        });
    });
}

function updateDirectionStopNames(stops = []) {
    stops.forEach((stop) => {
        const directions = stop.direction === 'BOTH'
            ? ['to_west_view', 'to_downtown']
            : [stop.direction === 'OUTBOUND' ? 'to_west_view' : 'to_downtown'];
        directions.forEach((direction) => {
            document.querySelectorAll(`[data-stop-name="${direction}"]`).forEach((element) => {
                element.textContent = stop.name;
            });
        });
    });
}

function updateDirectionStopNamesFromSelection() {
    const selectedStop = stopMetadata[currentStop];
    if (!selectedStop) return;

    document.querySelectorAll('[data-stop-name="to_west_view"]').forEach((element) => {
        element.textContent = selectedStop.names?.to_west_view || selectedStop.name;
    });
    document.querySelectorAll('[data-stop-name="to_downtown"]').forEach((element) => {
        element.textContent = selectedStop.names?.to_downtown || selectedStop.name;
    });
}

// Register Service Worker for PWA
if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('/service-worker.js')
            .then((registration) => {
                // Service Worker registered successfully

                // Check for updates
                registration.addEventListener('updatefound', () => {
                    const newWorker = registration.installing;
                    newWorker.addEventListener('statechange', () => {
                        if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
                            // New version available - could show notification to user
                        }
                    });
                });
            })
            .catch((error) => {
                // Service Worker registration failed - app still works without it
            });
    });
}

// Initialize app
document.addEventListener('DOMContentLoaded', () => {
    restoreDefaultStop();
    initializeTabs();
    initializeControls();
    initializeRouteSelector();
    initializeStopSelector();
    checkRouteStopCompatibility();
    updateStopNumberDisplay();
    updateDirectionStopNumbers();
    updateDirectionStopNamesFromSelection();
    fetchPredictions();
    fetchServiceAlerts();
    startAutoRefresh();
    setInterval(() => {
        if (currentData && !document.hidden) renderArrivals(currentData);
    }, 1000);
});

// Check if current route serves the current stop, auto-switch if not
function checkRouteStopCompatibility() {
    const compatibleStops = routeStopCompatibility[currentRoute] || [];
    const isCompatible = compatibleStops.includes(currentStop);

    const warningEl = document.getElementById('route-stop-warning');
    const stopSelect = document.getElementById('stop-select');

    if (!isCompatible && compatibleStops.length > 0) {
        // Auto-switch to the first compatible stop
        const newStop = compatibleStops[0];
        const oldStopName = stopMetadata[currentStop]?.name || currentStop;
        const newStopName = stopMetadata[newStop]?.name || newStop;
        const routeName = currentRoute === '8' ? 'Route 8' : 'Route 13';

        // Update the stop
        currentStop = newStop;
        stopSelect.value = newStop;
        updateStopNumberDisplay();
        updateDirectionStopNumbers();

        // Show a brief notification about the switch
        warningEl.innerHTML = `ℹ️ Switched to ${newStopName} — ${routeName} doesn't serve ${oldStopName}`;
        warningEl.style.display = 'block';

        // Hide the notification after 4 seconds
        setTimeout(() => {
            warningEl.style.display = 'none';
        }, 4000);

        return true; // Now compatible after switch
    } else {
        warningEl.style.display = 'none';
        updateStopNumberDisplay();
        updateDirectionStopNumbers();
    }

    return isCompatible;
}

// Route selector functionality
function initializeRouteSelector() {
    const routeSelect = document.getElementById('route-select');

    routeSelect.addEventListener('change', (e) => {
        currentRoute = e.target.value;
        checkRouteStopCompatibility();
        updateStopNumberDisplay();
        updateDirectionStopNumbers();
        updateDirectionStopNamesFromSelection();
        fetchPredictions();
        fetchServiceAlerts(); // Fetch alerts for new route
    });
}

// Stop selector functionality
function initializeStopSelector() {
    const stopSelect = document.getElementById('stop-select');
    const routeSelect = document.getElementById('route-select');
    const defaultStopButton = document.getElementById('set-default-stop');

    defaultStopButton.addEventListener('click', () => {
        localStorage.setItem(DEFAULT_STOP_STORAGE_KEY, currentStop);
        defaultStopButton.textContent = 'Default stop saved';
        setTimeout(() => {
            defaultStopButton.textContent = 'Set stop as default';
        }, 2000);
    });

    stopSelect.addEventListener('change', (e) => {
        currentStop = e.target.value;

        // Route 8 doesn't serve the Route 13-only stops.
        if (['stop_1009', 'stop_1016'].includes(currentStop) && currentRoute !== '13') {
            currentRoute = '13';
            routeSelect.value = '13';
        }

        checkRouteStopCompatibility();
        updateStopNumberDisplay();
        updateDirectionStopNumbers();
        updateDirectionStopNamesFromSelection();
        fetchPredictions();
        fetchServiceAlerts();
    });
}

function restoreDefaultStop() {
    const savedStop = localStorage.getItem(DEFAULT_STOP_STORAGE_KEY);
    if (savedStop && stopMetadata[savedStop]) {
        currentStop = savedStop;
        document.getElementById('stop-select').value = savedStop;
    }
}

// Tab functionality
function initializeTabs() {
    const tabButtons = document.querySelectorAll('.tab-btn');
    const tabContents = document.querySelectorAll('.tab-content');

    tabButtons.forEach(button => {
        button.addEventListener('click', () => {
            const targetTab = button.dataset.tab;
            activeTab = targetTab;

            // Update active states
            tabButtons.forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');

            tabContents.forEach(content => {
                content.classList.remove('active');
                if (content.id === `tab-${targetTab}`) {
                    content.classList.add('active');
                }
            });
        });
    });
}

// Controls functionality
function initializeControls() {
    const autoRefreshToggle = document.getElementById('auto-refresh');
    const refreshBtn = document.getElementById('refresh-btn');
    const logArrivalBtn = document.getElementById('log-arrival-btn');

    autoRefreshToggle.addEventListener('change', (e) => {
        if (e.target.checked) {
            startAutoRefresh();
        } else {
            stopAutoRefresh();
        }
    });

    refreshBtn.addEventListener('click', () => {
        fetchPredictions();
    });

    if (logArrivalBtn) {
        logArrivalBtn.addEventListener('click', () => logBusArrival(logArrivalBtn));
    }
}

// Record a rider-reported "bus arrived" observation (kept separate from
// predictions, since a prediction is a forecast and this is what actually
// happened).
async function logBusArrival(button) {
    const direction = activeTab === 'westview' ? 'to_west_view'
        : activeTab === 'downtown' ? 'to_downtown'
        : null;

    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = 'Logging...';

    try {
        const response = await fetch(`${API_URL}/observations`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                route: currentRoute,
                stop: currentStop,
                direction,
                note: 'Bus arrived',
            }),
        });

        button.textContent = response.ok ? 'Logged!' : 'Could not log';
    } catch (error) {
        button.textContent = 'Could not log';
    } finally {
        setTimeout(() => {
            button.textContent = originalText;
            button.disabled = false;
        }, 2000);
    }
}

// Auto-refresh management
function startAutoRefresh() {
    stopAutoRefresh(); // Clear any existing interval
    autoRefreshInterval = setInterval(() => {
        fetchPredictions();
    }, REFRESH_INTERVAL);
}

function stopAutoRefresh() {
    if (autoRefreshInterval) {
        clearInterval(autoRefreshInterval);
        autoRefreshInterval = null;
    }
}

// Fetch predictions from API
async function fetchPredictions() {
    const requestId = ++predictionRequestId;
    if (predictionController) predictionController.abort();
    const route = currentRoute;
    const stop = currentStop;
    const selection = `${route}:${stop}`;
    if (displayedSelection !== selection) {
        currentData = null;
        displayedSelection = selection;
        document.querySelectorAll('.arrivals-list').forEach((element) => {
            element.innerHTML = '<div class="loading">Loading arrivals...</div>';
        });
        updateFreshnessDisplay(null);
        document.getElementById('data-source').textContent = '--';
        document.getElementById('last-updated').textContent = '--';
        updateLiveStatus(null);
    }
    // Validate route/stop compatibility before making request
    if (!isValidRouteStopCombo(route, stop)) {
        showInvalidComboError();
        return;
    }

    updateStatus('Fetching...', 'connecting');
    const controller = new AbortController();
    predictionController = controller;
    // Allow the backend to try both live sources before declaring a timeout.
    const timeoutId = setTimeout(() => controller.abort(), 25000);

    try {
        const endpoint = `${API_URL}/predictions?route=${encodeURIComponent(route)}&stop=${encodeURIComponent(stop)}`;
        const response = await fetch(endpoint, { signal: controller.signal, cache: 'no-store' });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new ApiError(response.status, errorData.error || `Server error (${response.status})`);
        }

        const data = await response.json();
        if (requestId !== predictionRequestId || route !== currentRoute || stop !== currentStop) return;

        // Check for API-level errors
        if (data.error) {
            throw new ApiError(0, data.error);
        }
        if (String(data.route) !== route) {
            throw new ApiError(0, 'The arrival response does not match the selected route. Please refresh.');
        }
        Object.values(data.predictions).forEach((direction) => {
            direction.arrivals = direction.arrivals.filter((arrival) =>
                arrival.route == null || String(arrival.route) === route
            ).map((arrival) => ({ ...arrival, route }));
        });

        currentData = data;
        currentDataReceivedAt = performance.now();

        updateStatus(data.is_live ? 'Live' : 'Schedule only', data.is_live ? 'live' : 'scheduled');
        updateLastUpdated(data.last_updated);
        updateDataSource(data.data_source, data.is_live);
        updateStopNumberDisplay(data.stop_numbers);
        updateDirectionStopNames(data.stops);
        renderArrivals(data);

    } catch (error) {
        if (requestId !== predictionRequestId || route !== currentRoute || stop !== currentStop) return;
        currentData = null;
        updateFreshnessDisplay(null);
        updateLiveStatus(null);
        console.error('Error fetching predictions:', error);
        handleFetchError(error);
    } finally {
        clearTimeout(timeoutId);
        if (predictionController === controller) predictionController = null;
    }
}

// Update status indicators
function updateStatus(text, statusClass) {
    const statusElement = document.getElementById('connection-status');
    statusElement.textContent = text;
    statusElement.className = `status-value ${statusClass}`;
}

function updateLastUpdated(time) {
    document.getElementById('last-updated').textContent = time ? `${time} ET` : '--';
}

function updateDataSource(source, isLive) {
    const sourceElement = document.getElementById('data-source');
    const sourceText = {
        'truetime': 'TrueTime API',
        'gtfs-rt': 'GTFS-RT Feed',
        'gtfs-static': 'PRT schedule'
    };

    sourceElement.textContent = source
        .split('+')
        .map((part) => sourceText[part] || part)
        .join(' + ');

    if (!isLive) {
        sourceElement.style.color = 'var(--warning)';
    } else {
        sourceElement.style.color = 'var(--success)';
    }
}

// Render arrivals
function renderArrivals(data) {
    const westviewData = data.predictions.to_west_view;
    const downtownData = data.predictions.to_downtown;
    const expectedHeadway = data.expected_headway || null;
    const directions = data.directions || stopMetadata[currentStop]?.directions || [];

    updateDirectionStopNumbers(data.predictions);
    updateFreshnessDisplay(data.source_age_seconds);
    updateFeedExpiryBanner(data.feed_expiry);
    updateLiveStatus(data);

    // Check if at terminus (West View Plaza)
    const isAtWestView = currentStop === 'westview';
    document.querySelectorAll('[data-destination="to_west_view"]').forEach((element) => {
        element.textContent = isAtWestView ? 'Arrivals at West View Plaza' : 'To West View Plaza, Fire Lane Plaza, Giant Eagle';
    });
    document.querySelectorAll('[data-destination="to_downtown"]').forEach((element) => {
        element.textContent = isAtWestView ? 'Departures to Downtown' : 'To Downtown';
    });

    // Render for "Both Directions" tab
    renderDirectionList('westview-arrivals', westviewData, directions.includes('to_west_view'), 'to_west_view', null, expectedHeadway);
    renderDirectionList('downtown-arrivals', downtownData, directions.includes('to_downtown'), 'to_downtown', null, expectedHeadway);

    // Render for individual tabs
    renderDirectionList('westview-arrivals-single', westviewData, directions.includes('to_west_view'), 'to_west_view', null, expectedHeadway);
    renderDirectionList('downtown-arrivals-single', downtownData, directions.includes('to_downtown'), 'to_downtown', null, expectedHeadway);
}

function updateLiveStatus(data) {
    const banner = document.getElementById('live-status-banner');
    if (!banner) return;
    if (!data) {
        banner.hidden = true;
        return;
    }
    const directions = data.directions || stopMetadata[currentStop]?.directions || [];
    const visibleDirections = activeTab === 'westview' ? ['to_west_view']
        : activeTab === 'downtown' ? ['to_downtown'] : directions;
    const arrivals = visibleDirections.filter((direction) => directions.includes(direction))
        .flatMap((direction) => data.predictions[direction]?.arrivals || []);
    const live = arrivals.filter((arrival) => arrival.is_live === true && arrival.prediction_type !== 'scheduled');
    const fresh = live.some((arrival) => {
        const timing = arrivalTiming(arrival);
        return !timing.stale && !timing.passed;
    });
    if (fresh) {
        updateStatus('Live', 'live');
        banner.hidden = true;
        return;
    }
    const scheduled = arrivals.some((arrival) => arrival.is_live === false || arrival.prediction_type === 'scheduled');
    updateStatus(live.length ? 'Live data stale' : (scheduled ? 'Schedule only' : 'No live prediction'), 'scheduled');
    const sources = data.live_sources || {};
    let reason = 'PRT has no current live prediction for this stop and direction.';
    if (sources['gtfs-rt'] === 'unavailable') {
        reason = 'The live feed could not be reached.';
    } else if (live.length || Object.values(sources).includes('stale')) {
        reason = 'The last live prediction is too old to rely on.';
    }
    banner.textContent = `Live tracking unavailable. ${reason}${scheduled ? ' Times shown are scheduled, not tracked bus arrivals.' : ''}`;
    banner.hidden = false;
}

function updateFreshnessDisplay(sourceAgeSeconds) {
    const elements = document.querySelectorAll('[data-freshness]');
    if (sourceAgeSeconds === null || sourceAgeSeconds === undefined) {
        elements.forEach((element) => { element.textContent = ''; });
        return;
    }

    const age = Math.max(0, Math.floor(sourceAgeSeconds + secondsSinceResponse()));
    const label = age < 5
        ? 'PRT updated just now'
        : `PRT updated ${age} seconds ago${age > MAX_PREDICTION_AGE_SECONDS ? ' (stale)' : ''}`;
    elements.forEach((element) => { element.textContent = label; });
}

function updateFeedExpiryBanner(feedExpiry) {
    const banner = document.getElementById('feed-expiry-banner');
    if (!banner) return;

    if (!feedExpiry || (!feedExpiry.expires_soon && !feedExpiry.expired)) {
        banner.style.display = 'none';
        return;
    }

    banner.textContent = feedExpiry.expired
        ? `⚠️ The bundled PRT schedule expired ${feedExpiry.valid_through}. Scheduled-only arrivals may be wrong until it's updated.`
        : `ℹ️ The bundled PRT schedule expires ${feedExpiry.valid_through}. It will need to be refreshed soon.`;
    banner.style.display = 'block';
}

function renderDirectionList(containerId, directionData, isServed, direction, terminus = null, expectedHeadway = null) {
    const arrivals = directionData.arrivals;

    if (!isServed) {
        const container = document.getElementById(containerId);
        const alternate = stopMetadata[currentStop]?.alternate;
        const isWestView = direction === 'to_west_view';
        const heading = isWestView ? 'Downtown-bound stop' : 'West View-bound stop';
        const destination = isWestView ? 'West View' : 'Downtown';
        const alternateText = alternate?.direction === direction
            ? ` Use Stop #${alternate.stopNumber} across the street.`
            : '';
        container.innerHTML = `
            <div class="arrival-card schedule-card">
                <div class="minutes-display schedule-icon">
                    <div class="minutes-number">↔</div>
                    <div class="minutes-label">&nbsp;</div>
                </div>
                <div class="arrival-info">
                    <h3>${heading}</h3>
                    <div class="arrival-time">${destination} service is not available here.${alternateText}</div>
                </div>
                <div class="status-badge schedule">Not served</div>
            </div>`;
        return;
    }

    renderArrivalList(containerId, arrivals, terminus, expectedHeadway, directionData.service_status);
}

function renderArrivalList(containerId, arrivals, terminus = null, expectedHeadway = null, serviceStatus = null) {
    const container = document.getElementById(containerId);

    // Show terminus message FIRST if at end of line (regardless of arrivals)
    if (terminus === 'westview') {
        container.innerHTML = `
            <div class="arrival-card terminus-card">
                <div class="minutes-display terminus-icon">
                    <div class="minutes-number">📍</div>
                    <div class="minutes-label">&nbsp;</div>
                </div>
                <div class="arrival-info">
                    <h3>End of Line</h3>
                    <div class="arrival-time">Check "To Downtown" for departures</div>
                </div>
                <div class="status-badge terminus">
                    West View
                </div>
            </div>`;
        return;
    }

    if (!arrivals || arrivals.length === 0) {
        // Explain the gap using the actual next scheduled trip, not a
        // generic time-of-day guess.
        const stateIcon = { service_ended: '🌙', scheduled_only: '🕐', unavailable: '❓' };
        const stateLabel = { service_ended: 'Ended for today', scheduled_only: 'Scheduled', unavailable: 'Unavailable' };
        const stateTitle = {
            service_ended: 'Service has ended',
            scheduled_only: 'Live tracking unavailable',
            unavailable: 'Live tracking unavailable',
        };

        if (serviceStatus) {
            const state = serviceStatus.state;
            container.innerHTML = `
                <div class="arrival-card schedule-card">
                    <div class="minutes-display schedule-icon">
                        <div class="minutes-number">${stateIcon[state] || '❓'}</div>
                        <div class="minutes-label">&nbsp;</div>
                    </div>
                    <div class="arrival-info">
                        <h3>${stateTitle[state] || 'Live tracking unavailable'}</h3>
                        <div class="arrival-time">${serviceStatus.message}</div>
                    </div>
                    <div class="status-badge schedule">
                        ${stateLabel[state] || 'Unavailable'}
                    </div>
                </div>`;
            return;
        }

        // Show expected headway if available
        if (expectedHeadway) {
            container.innerHTML = `
                <div class="arrival-card schedule-card">
                    <div class="minutes-display schedule-icon">
                        <div class="minutes-number">🕐</div>
                        <div class="minutes-label">&nbsp;</div>
                    </div>
                    <div class="arrival-info">
                        <h3>No bus in view yet</h3>
                        <div class="arrival-time">Expect a bus every ~${expectedHeadway} min</div>
                    </div>
                    <div class="status-badge schedule">
                        Scheduled
                    </div>
                </div>`;
            return;
        }

        container.innerHTML = `
            <div class="arrival-card schedule-card">
                <div class="minutes-display schedule-icon">
                    <div class="minutes-number">🌙</div>
                    <div class="minutes-label">&nbsp;</div>
                </div>
                <div class="arrival-info">
                    <h3>No buses running</h3>
                    <div class="arrival-time">Service resumes ~5:30 AM</div>
                </div>
                <div class="status-badge schedule">
                    Night
                </div>
            </div>`;
        return;
    }


    container.innerHTML = arrivals.map(arrival => createArrivalCard(arrival)).join('');
}

function secondsSinceResponse() {
    return currentDataReceivedAt === null ? 0 : Math.max(0, (performance.now() - currentDataReceivedAt) / 1000);
}

function arrivalTiming(arrival) {
    const elapsed = secondsSinceResponse();
    // Use the server clock so an incorrectly set phone clock cannot change the ETA.
    const now = Number.isFinite(currentData?.server_time) ? currentData.server_time + elapsed : Date.now() / 1000;
    const seconds = Number.isFinite(arrival.arrival_timestamp)
        ? arrival.arrival_timestamp - now
        : arrival.minutes * 60 - elapsed;
    const scheduled = arrival.prediction_type === 'scheduled' || arrival.is_live === false;
    const sourceAge = Number.isFinite(arrival.source_timestamp) ? now - arrival.source_timestamp : 0;
    const stale = !scheduled && (sourceAge > MAX_PREDICTION_AGE_SECONDS || elapsed > MAX_PREDICTION_AGE_SECONDS);
    const passed = seconds < (scheduled ? 0 : -60);
    const approaching = !scheduled && !stale && !passed && (arrival.is_due || seconds <= 120);
    const due = approaching && (arrival.is_due || seconds < 60);
    return {
        stale, passed, approaching,
        display: stale || passed ? '--' : (due ? 'Due' : Math.max(0, scheduled ? Math.ceil(seconds / 60) : Math.floor(seconds / 60))),
        label: stale ? 'Stale' : (passed ? 'Passed' : (due ? 'Estimated' : (scheduled ? 'min sched.' : 'min est.'))),
        status: stale ? 'Update needed' : (passed ? 'Time passed' : (approaching ? 'Due soon' : arrival.status)),
    };
}

function createArrivalCard(arrival) {
    const timing = arrivalTiming(arrival);
    const statusClass = timing.stale || timing.passed ? 'schedule' : getStatusClass(arrival.status);
    const isScheduled = arrival.prediction_type === 'scheduled' || arrival.is_live === false;
    const cardClass = isScheduled ? 'schedule-card' : (statusClass === 'on-time' ? '' : statusClass);

    const approachingClass = timing.approaching ? 'approaching' : '';

    // Handle both field names: 'time' and 'arrival_time'
    const predictedTime = arrival.time || arrival.arrival_time || 'N/A';
    const scheduledTime = arrival.scheduled_time;
    const timeLabel = arrival.note
        ? `${scheduledTime || predictedTime} scheduled · live tracking unavailable`
        : (isScheduled
            ? `Scheduled: ${scheduledTime || predictedTime}`
            : `Predicted ${arrival.event_type === 'departure' ? 'departure' : 'arrival'}: ${predictedTime}${scheduledTime ? ` • Scheduled: ${scheduledTime}` : ''}`);

    // Show route number if available (for multi-route at West View)
    const routeLabel = arrival.route ? `Route ${arrival.route} • ` : '';
    const tripLabel = arrival.vehicle_id ? `Bus #${arrival.vehicle_id}` : (isScheduled ? 'Scheduled trip' : 'Live estimate');

    return `
        <div class="arrival-card ${cardClass} ${approachingClass}">
            <div class="minutes-display ${approachingClass}">
                <div class="minutes-number">${timing.display}</div>
                <div class="minutes-label">${timing.label}</div>
            </div>
            <div class="arrival-info">
                <h3>${routeLabel}${tripLabel}</h3>
                <div class="arrival-time">${timeLabel}</div>
            </div>
            <div class="status-badge ${statusClass}">
                ${timing.status}
            </div>
        </div>
    `;
}

function getStatusClass(status) {
    if (status.includes('Scheduled')) return 'schedule';
    if (status.includes('On Time')) return 'on-time';
    if (status.includes('Delayed')) return 'delayed';
    if (status.includes('Early')) return 'early';
    return 'on-time';
}

// Custom error class for API errors
class ApiError extends Error {
    constructor(status, message) {
        super(message);
        this.status = status;
        this.name = 'ApiError';
    }
}

// Validate route/stop combination
function isValidRouteStopCombo(route, stop) {
    const validStops = routeStopCompatibility[route];
    return validStops && validStops.includes(stop);
}

// Handle different types of fetch errors
function handleFetchError(error) {
    if (error.name === 'AbortError') {
        updateStatus('Timeout', 'offline');
        showError('Connection timed out', 'The server took too long to respond. Please try again.');
    } else if (error instanceof ApiError) {
        updateStatus('Error', 'offline');
        showError('API Error', error.message);
    } else if (!navigator.onLine) {
        updateStatus('Offline', 'offline');
        showError('No Internet', 'You appear to be offline. Check your connection.');
    } else {
        updateStatus('Offline', 'offline');
        showError('Connection Failed', 'Unable to reach the bus tracker service.');
    }
}

// Show invalid route/stop combination error
function showInvalidComboError() {
    const routeName = currentRoute === '8' ? 'Route 8' : 'Route 13';
    updateStatus('Invalid', 'offline');
    showError('Invalid Stop', `${routeName} doesn't serve this stop. Please select a different stop.`);
}

// Error handling with customizable messages
function showError(title = 'Unable to connect', subtitle = 'Check if the service is running') {
    const errorHtml = `
        <div class="no-arrivals error-state">
            <div class="no-arrivals-icon">⚠️</div>
            <div class="no-arrivals-title">${title}</div>
            <div class="no-arrivals-subtitle">${subtitle}</div>
            <button class="retry-btn" onclick="fetchPredictions()">Try Again</button>
        </div>
    `;

    document.getElementById('westview-arrivals').innerHTML = errorHtml;
    document.getElementById('downtown-arrivals').innerHTML = errorHtml;
    document.getElementById('westview-arrivals-single').innerHTML = errorHtml;
    document.getElementById('downtown-arrivals-single').innerHTML = errorHtml;
}

// Visibility change detection (pause refresh when tab is hidden)
document.addEventListener('visibilitychange', () => {
    const autoRefreshToggle = document.getElementById('auto-refresh');

    if (document.hidden) {
        stopAutoRefresh();
    } else if (autoRefreshToggle.checked) {
        fetchPredictions(); // Immediate refresh when returning
        startAutoRefresh();
    }
});

// Service Alerts
async function fetchServiceAlerts() {
    const route = currentRoute;
    renderAlerts([]);
    try {
        const response = await fetch(`${API_URL}/alerts?route=${route}`);
        if (!response.ok) return;

        const data = await response.json();
        if (route !== currentRoute) return;
        renderAlerts(data.alerts || []);
    } catch (error) {
        // Silently fail - alerts are non-critical
    }
}

function renderAlerts(alerts) {
    const container = document.getElementById('alerts-container');
    const list = document.getElementById('alerts-list');
    const toggleBtn = document.getElementById('alerts-toggle');

    if (!alerts || alerts.length === 0) {
        container.style.display = 'none';
        return;
    }

    container.style.display = 'block';

    list.innerHTML = alerts.map(alert => `
        <div class="alert-item">
            <div class="alert-item-title">
                ${alert.title}
                <span class="alert-priority ${alert.priority.toLowerCase()}">${alert.priority}</span>
            </div>
            <div class="alert-item-brief">${alert.brief || alert.detail}</div>
        </div>
    `).join('');

    // Start collapsed by default
    list.classList.add('collapsed');
    toggleBtn.textContent = 'Show';

    // Toggle functionality
    toggleBtn.onclick = () => {
        list.classList.toggle('collapsed');
        toggleBtn.textContent = list.classList.contains('collapsed') ? 'Show' : 'Hide';
    };
}
