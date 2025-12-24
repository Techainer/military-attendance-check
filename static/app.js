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
                    startBtn.disabled = false;
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
startBtn.addEventListener('click', () => {
    if (isProcessing) {
        console.log('Already processing');
        return;
    }

    startProcessing();
});

function startProcessing() {
    isProcessing = true;
    startBtn.disabled = true;
    processingStatus.textContent = 'Connecting...';
    processingStatus.style.color = '#667eea';
    loadingOverlay.style.display = 'flex';
    loadingOverlay.querySelector('p').textContent = 'Starting video processing...';

    // Establish WebSocket connection
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log('WebSocket connected');
        processingStatus.textContent = 'Processing video...';
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
        if (isProcessing) {
            processingStatus.textContent = 'Processing stopped';
            processingStatus.style.color = '#666';
        }
        isProcessing = false;
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

        default:
            console.log('Unknown message type:', data.type);
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
    // Update alert banner
    alertMessage.textContent = `⚠️ ${data.message} at ${new Date(data.timestamp).toLocaleTimeString()}`;
    alertBanner.style.display = 'flex';

    // Add to event log
    addEventToLog(data);

    // Auto-hide after 10 seconds
    setTimeout(() => {
        alertBanner.style.display = 'none';
    }, 10000);
}

// Add Event to Log
function addEventToLog(data) {
    // Hide "no events" message
    if (noEventsEl) {
        noEventsEl.style.display = 'none';
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
