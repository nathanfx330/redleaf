// --- File: ./static/js/utils.js ---

/**
 * Formats a duration in seconds into a human-readable HH:MM:SS or MM:SS string.
 * @param {number|null|undefined} seconds The duration in seconds.
 * @returns {string} The formatted time string or 'N/A' if input is invalid.
 */
function formatDuration(seconds) {
    if (seconds === null || seconds === undefined || isNaN(seconds) || seconds < 0) {
        return 'N/A';
    }
    
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    
    if (h > 0) {
        return [h, m, s].map(v => v.toString().padStart(2, '0')).join(':');
    }
    return [m, s].map(v => v.toString().padStart(2, '0')).join(':');
}

/**
 * Formats a size in bytes into a human-readable string (KB, MB, GB, etc.).
 * @param {number|null|undefined} bytes The number of bytes.
 * @returns {string} The formatted size string or 'N/A' if input is invalid.
 */
function formatFileSize(bytes) {
    if (bytes === null || bytes === undefined || isNaN(bytes)) return 'N/A';
    if (bytes === 0) return '0 B';
    const k = 1024;
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${['B','KB','MB','GB','TB'][i]}`;
}