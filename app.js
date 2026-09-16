// Pittsburgh Bus Tracker - Main JavaScript
const API_URL = window.location.protocol === 'file:'
    ? 'http://localhost:5001'
    : window.location.origin; // Use localhost when opened as file, relative URL for production
const REFRESH_INTERVAL = 30000; // 30 seconds
const DEFAULT_STOP_STORAGE_KEY = 'pixburgh-bus-tracker-default-stop';

let autoRefreshInterval = null;
let currentData = null;
let currentRoute = '13';
let currentStop = 'stop_618';

const stopMetadata = {
    chalfonte: {
        name: 'Center Ave + Chalfonte Ave',
        numbers: { outbound: '1009', inbound: '1016' }
    },
    westview: {
        name: 'West View Plaza + Giant Eagle',
        numbers: { outbound: '619', inbound: '619' }
    },
    stop_620: {
        name: 'Stop 620',
        numbers: { outbound: '620', inbound: '620' }
    },
    stop_618: {
        name: 'Stop 618',
        numbers: { outbound: '618', inbound: '618' }
    }
};

// Route-stop compatibility mapping
// Route 8 only serves West View Plaza
// Route 13 serves all stops
const routeStopCompatibility = {
    '8': ['westview'],  // Route 8 only serves West View Plaza
    '13': ['chalfonte', 'westview', 'stop_620', 'stop_618']  // Route 13: Center Ave, West View, stops 620/618
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
                element.textContent = `${stop.name} (#${stop.id})`;
            });
        });
    });
}

function updateDirectionStopNamesFromSelection() {
    const selectedStop = stopMetadata[currentStop];
    if (!selectedStop) return;

    document.querySelectorAll('[data-stop-name="to_west_view"]').forEach((element) => {
        element.textContent = `${selectedStop.name} (#${selectedStop.numbers.outbound})`;
    });
    document.querySelectorAll('[data-stop-name="to_downtown"]').forEach((element) => {
        element.textContent = `${selectedStop.name} (#${selectedStop.numbers.inbound})`;
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

        // Auto-switch to Route 13 when Center Ave + Chalfonte Ave is selected
        // (Route 8 doesn't serve this stop)
        if (currentStop === 'chalfonte' && currentRoute !== '13') {
            currentRoute = '13';
            routeSelect.value = '13';
        }

        checkRouteStopCompatibility();
        updateStopNumberDisplay();
        updateDirectionStopNumbers();
        updateDirectionStopNamesFromSelection();
        fetchPredictions();
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
    // Validate route/stop compatibility before making request
    if (!isValidRouteStopCombo(currentRoute, currentStop)) {
        showInvalidComboError();
        return;
    }

    updateStatus('Fetching...', 'connecting');

    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 10000); // 10s timeout

        // Use multi-route endpoint at West View to show both Route 8 and 13
        const endpoint = currentStop === 'westview'
            ? `${API_URL}/predictions/multi?stop=westview`
            : `${API_URL}/predictions?route=${currentRoute}&stop=${currentStop}`;

        const response = await fetch(endpoint, { signal: controller.signal });
        clearTimeout(timeoutId);

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new ApiError(response.status, errorData.error || `Server error (${response.status})`);
        }

        const data = await response.json();

        // Check for API-level errors
        if (data.error) {
            throw new ApiError(0, data.error);
        }

        currentData = data;

        updateStatus('Connected', 'live');
        updateLastUpdated(data.last_updated);
        updateDataSource(data.data_source, data.is_live);
        updateStopNumberDisplay(data.stop_numbers);
        updateDirectionStopNames(data.stops);
        renderArrivals(data);

    } catch (error) {
        console.error('Error fetching predictions:', error);
        handleFetchError(error);
    }
}

// Update status indicators
function updateStatus(text, statusClass) {
    const statusElement = document.getElementById('connection-status');
    statusElement.textContent = text;
    statusElement.className = `status-value ${statusClass}`;
}

function updateLastUpdated(time) {
    const now = new Date();
    const estTime = new Intl.DateTimeFormat('en-US', {
        timeZone: 'America/New_York',
        hour: 'numeric',
        minute: '2-digit',
        second: '2-digit',
        hour12: true
    }).format(now);

    document.getElementById('last-updated').textContent = estTime + ' EST';
}

function updateDataSource(source, isLive) {
    const sourceElement = document.getElementById('data-source');
    const sourceText = {
        'truetime': 'TrueTime API',
        'gtfs-rt': 'GTFS-RT Feed'
    };

    sourceElement.textContent = sourceText[source] || source;

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

    updateDirectionStopNumbers(data.predictions);

    // Check if at terminus (West View Plaza)
    const isAtWestView = currentStop === 'westview';

    // Render for "Both Directions" tab
    renderArrivalList('westview-arrivals', westviewData.arrivals, isAtWestView ? 'westview' : null, expectedHeadway);
    renderArrivalList('downtown-arrivals', downtownData.arrivals, null, expectedHeadway);

    // Render for individual tabs
    renderArrivalList('westview-arrivals-single', westviewData.arrivals, isAtWestView ? 'westview' : null, expectedHeadway);
    renderArrivalList('downtown-arrivals-single', downtownData.arrivals, null, expectedHeadway);
}

function renderArrivalList(containerId, arrivals, terminus = null, expectedHeadway = null) {
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
                    <div class="arrival-time">Check "To Dahntahn" for departures</div>
                </div>
                <div class="status-badge terminus">
                    West View
                </div>
            </div>`;
        return;
    }

    if (!arrivals || arrivals.length === 0) {
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

function createArrivalCard(arrival) {
    const statusClass = getStatusClass(arrival.status);
    const cardClass = statusClass === 'on-time' ? '' : statusClass;

    // Show "Arriving Now" for buses less than 1 minute away
    const isApproaching = arrival.minutes < 1;
    const minutesDisplay = isApproaching ? 'Now' : arrival.minutes;
    const minutesLabel = isApproaching ? 'Arriving' : 'min';
    const approachingClass = isApproaching ? 'approaching' : '';

    // Handle both field names: 'time' and 'arrival_time'
    const scheduledTime = arrival.time || arrival.arrival_time || 'N/A';

    // Show route number if available (for multi-route at West View)
    const routeLabel = arrival.route ? `Route ${arrival.route} • ` : '';

    return `
        <div class="arrival-card ${cardClass} ${approachingClass}">
            <div class="minutes-display ${approachingClass}">
                <div class="minutes-number">${minutesDisplay}</div>
                <div class="minutes-label">${minutesLabel}</div>
            </div>
            <div class="arrival-info">
                <h3>${routeLabel}Bus #${arrival.vehicle_id || 'N/A'}</h3>
                <div class="arrival-time">Scheduled: ${scheduledTime}</div>
            </div>
            <div class="status-badge ${statusClass}">
                ${isApproaching ? 'Arriving Now' : arrival.status}
            </div>
        </div>
    `;
}

function getStatusClass(status) {
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
    try {
        const response = await fetch(`${API_URL}/alerts?route=${currentRoute}`);
        if (!response.ok) return;

        const data = await response.json();
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
