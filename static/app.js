// Military Attendance Check - Frontend Logic

let ws = null;
let currentCount = 0;
let isProcessing = false;

// DOM Elements
const uploadForm = document.getElementById('upload-form');
const videoFileInput = document.getElementById('video-file');
const uploadStatus = document.getElementById('upload-status');
const startBtn = document.getElementById('start-btn');
const processingStatus = document.getElementById('processing-status');
const setBaselineBtn = document.getElementById('set-baseline-btn');
const currentCountEl = document.getElementById('current-count');
const baselineCountEl = document.getElementById('baseline-count');
const statusIndicator = document.getElementById('status-indicator');
const videoCanvas = document.getElementById('video-canvas');
const loadingOverlay = document.getElementById('loading-overlay');
const alertBanner = document.getElementById('alert-banner');
const alertMessage = document.getElementById('alert-message');
const eventLogBody = document.getElementById('event-log-body');
const noEventsEl = document.getElementById('no-events');

// Upload Video
uploadForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    const file = videoFileInput.files[0];
    if (!file) {
        uploadStatus.textContent = 'Please select a video file';
        uploadStatus.style.color = '#f56565';
        return;
    }

    const CHUNK_SIZE = 1 * 1024 * 1024; // 1MB chunks
    const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
    const uploadId = Date.now().toString() + '-' + Math.random().toString(36).substr(2, 9);

    uploadStatus.textContent = 'Starting upload...';
    uploadStatus.style.color = '#667eea';

    try {
        for (let i = 0; i < totalChunks; i++) {
            const start = i * CHUNK_SIZE;
            const end = Math.min(start + CHUNK_SIZE, file.size);
            const chunk = file.slice(start, end);

            const formData = new FormData();
            formData.append('file', chunk);
            formData.append('chunk_index', i);
            formData.append('total_chunks', totalChunks);
            formData.append('upload_id', uploadId);
            formData.append('filename', file.name);

            // Update status
            const percent = Math.round(((i) / totalChunks) * 100);
            uploadStatus.textContent = `Uploading chunk ${i + 1}/${totalChunks} (${percent}%)`;

            const response = await fetch('/api/upload_chunk', {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.message || `Upload failed at chunk ${i}`);
            }

            const data = await response.json();

            if (i === totalChunks - 1) {
                // Final chunk
                if (data.status === 'success') {
                    uploadStatus.textContent = `✓ Upload complete`;
                    uploadStatus.style.color = '#48bb78';
                    // Auto-connect to stream since backend auto-starts
                    connectWebSocket();
                } else {
                    throw new Error(data.message || 'Upload completed but returned error status');
                }
            }
        }
    } catch (error) {
        console.error(error);
        uploadStatus.textContent = `✗ Upload error: ${error.message}`;
        uploadStatus.style.color = '#f56565';
    }
});

// Start Processing
startBtn.addEventListener('click', async () => {
    if (isProcessing) {
        console.log('Already processing');
        return;
    }

    // Trigger start explicitly if needed (though upload auto-starts)
    try {
        const response = await fetch('/api/start', { method: 'POST' });
        const data = await response.json();

        if (data.status === 'success') {
            processingStatus.textContent = 'Processing started...';
            processingStatus.style.color = '#48bb78';
            connectWebSocket();
        } else {
            console.log(data.message);
            // If already processing or just uploaded, connect anyway
            connectWebSocket();
        }
    } catch (e) {
        console.error("Error starting processing:", e);
        connectWebSocket();
    }
});

function connectWebSocket() {
    if (ws && ws.readyState === WebSocket.OPEN) {
        console.log("WebSocket already open");
        return;
    }

    isProcessing = true;
    startBtn.disabled = true;
    processingStatus.textContent = 'Connecting to stream...';
    processingStatus.style.color = '#667eea';
    loadingOverlay.style.display = 'flex';
    loadingOverlay.querySelector('p').textContent = 'Connecting to video stream...';

    // Establish WebSocket connection
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log('WebSocket connected');
        processingStatus.textContent = 'Connected to stream';
        processingStatus.style.color = '#48bb78';
        setBaselineBtn.disabled = false;
        loadingOverlay.style.display = 'none';
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleWebSocketMessage(data);
    };

    ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        processingStatus.textContent = '✗ Connection error';
        processingStatus.style.color = '#f56565';
        isProcessing = false;
    };

    ws.onclose = () => {
        console.log('WebSocket closed');
        // Auto-reconnect if we think it should be processing?
        // For now just show disconnected
        if (isProcessing) {
            processingStatus.textContent = 'Disconnected';
            processingStatus.style.color = '#666';
        }
        isProcessing = false;
        startBtn.disabled = false;
        setBaselineBtn.disabled = true;
    };
}


// Handle WebSocket Messages
function handleWebSocketMessage(data) {
    switch (data.type) {
        case 'frame_update':
            updateFrame(data);
            break;

        case 'alert':
            showAlert(data);
            break;

        case 'processing_complete':
            onProcessingComplete(data);
            break;

        case 'error':
            showError(data.message);
            break;

        case 'status':
            handleStatusMessage(data);
            break;

        default:
            console.log('Unknown message type:', data.type);
    }
}

function handleStatusMessage(data) {
    if (data.is_processing) {
        processingStatus.textContent = 'Processing in progress...';
        processingStatus.style.color = '#48bb78';
        startBtn.disabled = true;
        isProcessing = true;

        // Hide upload container or show it as disabled?
        // Maybe just indicate in logs
        console.log("Joined existing session");
    }
}

// Update Video Frame
function updateFrame(data) {
    const img = new Image();

    img.onload = () => {
        // Set canvas size to match image
        videoCanvas.width = img.width;
        videoCanvas.height = img.height;

        // Draw image on canvas
        const ctx = videoCanvas.getContext('2d');
        ctx.drawImage(img, 0, 0);
    };

    img.src = 'data:image/jpeg;base64,' + data.frame;

    // Update count
    currentCount = data.count;
    currentCountEl.textContent = data.count;

    // Update status indicator
    updateStatusIndicator();
}

// Update Status Indicator
function updateStatusIndicator() {
    const baseline = parseInt(baselineCountEl.textContent);

    if (isNaN(baseline) || baseline === 0) {
        statusIndicator.textContent = 'No baseline';
        statusIndicator.className = 'stat-value';
        return;
    }

    if (currentCount >= baseline) {
        statusIndicator.textContent = 'OK';
        statusIndicator.className = 'stat-value status-ok';
    } else if (currentCount >= baseline * 0.8) {
        statusIndicator.textContent = 'Warning';
        statusIndicator.className = 'stat-value status-warning';
    } else {
        statusIndicator.textContent = 'Alert';
        statusIndicator.className = 'stat-value status-alert';
    }
}

// Show Alert
function showAlert(data) {
    // Only show banner for new alerts (e.g., within last 10 seconds)
    const alertTime = new Date(data.timestamp).getTime();
    const now = Date.now();
    const isNew = (now - alertTime) < 10000;

    if (isNew) {
        // Update alert banner
        alertMessage.textContent = `⚠️ ${data.message} at ${new Date(data.timestamp).toLocaleTimeString()}`;
        alertBanner.style.display = 'flex';

        // Auto-hide after 10 seconds
        setTimeout(() => {
            alertBanner.style.display = 'none';
        }, 10000);
    }

    // Add to event log
    addEventToLog(data);
}

// Add Event to Log
function addEventToLog(data) {
    // Hide "no events" message
    if (noEventsEl) {
        noEventsEl.style.display = 'none';
    }

    // Check for duplicates
    const existingRows = eventLogBody.getElementsByTagName('tr');
    const timestampStr = new Date(data.timestamp).toLocaleString();

    for (let i = 0; i < existingRows.length; i++) {
        if (existingRows[i].cells[0].textContent === timestampStr &&
            existingRows[i].cells[1].textContent === data.message) {
            return; // Duplicate
        }
    }

    const row = eventLogBody.insertRow(0);

    // Timestamp
    const timeCell = row.insertCell(0);
    const timestamp = new Date(data.timestamp);
    timeCell.textContent = timestamp.toLocaleString();

    // Message
    const messageCell = row.insertCell(1);
    messageCell.textContent = data.message;

    // Count
    const countCell = row.insertCell(2);
    countCell.textContent = `${data.count} / ${data.baseline}`;

    // Captured frame
    const imageCell = row.insertCell(3);
    const img = document.createElement('img');
    img.src = '/' + data.image_path;
    img.alt = 'Alert frame';
    img.onclick = () => window.open(img.src, '_blank');
    imageCell.appendChild(img);

    // Highlight new row briefly
    row.style.background = '#fff3cd';
    setTimeout(() => {
        row.style.background = '';
    }, 2000);
}

// Processing Complete
function onProcessingComplete(data) {
    processingStatus.textContent = `✓ Processing complete (${data.total_frames} frames)`;
    processingStatus.style.color = '#48bb78';
    isProcessing = false;
    startBtn.disabled = false;
    setBaselineBtn.disabled = true;

    if (ws) {
        ws.close();
        ws = null;
    }
}

// Show Error
function showError(message) {
    processingStatus.textContent = `✗ Error: ${message}`;
    processingStatus.style.color = '#f56565';
    isProcessing = false;
    startBtn.disabled = false;

    loadingOverlay.style.display = 'flex';
    loadingOverlay.querySelector('p').textContent = message;
}

// Set Baseline
setBaselineBtn.addEventListener('click', async () => {
    if (currentCount === 0) {
        alert('Wait for video processing to detect people first');
        return;
    }

    try {
        const response = await fetch(`/api/set-baseline?count=${currentCount}`, {
            method: 'POST'
        });

        const data = await response.json();

        if (response.ok) {
            baselineCountEl.textContent = data.baseline;
            alert(`✓ Baseline set to ${data.baseline} persons`);
            updateStatusIndicator();
        } else {
            alert(`✗ Failed to set baseline: ${data.message}`);
        }
    } catch (error) {
        alert(`✗ Error setting baseline: ${error.message}`);
    }
});

// Close Alert Banner
function closeAlert() {
    alertBanner.style.display = 'none';
}

// Initialize
console.log('Military Attendance Check - Frontend Ready');

// Check initial status
fetch('/api/status')
    .then(r => r.json())
    .then(data => {
        if (data.is_processing) {
            console.log("Restoring session...");
            connectWebSocket();
        }
    })
    .catch(e => console.error("Error checking status:", e));

// Fetch recent alerts
fetch('/api/alerts')
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success' && data.alerts) {
            data.alerts.forEach(alert => {
                showAlert(alert);
            });
        }
    })
    .catch(e => console.error("Error fetching alerts:", e));
