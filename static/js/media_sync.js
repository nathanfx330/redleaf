// --- File: static/js/media_sync.js ---

document.addEventListener('DOMContentLoaded', () => {
    const workbenchContainer = document.querySelector('.workbench-container');
    if (!workbenchContainer) return;

    const docId = workbenchContainer.dataset.docId;
    const fileType = workbenchContainer.dataset.fileType;
    const CSRF_TOKEN = document.querySelector('#csrf-form-container input[name="csrf_token"]').value;
    const docViewerIframe = document.getElementById('doc-viewer');
    const metadataTab = document.getElementById('tab-metadata');

    // Only run this script if the document is an SRT file.
    if (fileType !== 'SRT') {
        return;
    }

    const mediaSyncPanel = document.getElementById('media-sync-panel');
    const xmlSyncPanel = document.getElementById('xml-sync-panel');

    const renderLinkedState = (type, path, source, offset) => {
        const mediaType = type.charAt(0).toUpperCase() + type.slice(1);
        const sourceIndicator = (source === 'web') ? `<span class="chip" style="background-color: #2196f3;">Web</span>` : `<span class="chip">Local</span>`;
        const statusCheckHtml = (source === 'web') ? `<div class="mt-2"><button id="check-status-btn" class="button button-small">Check Link Status</button><p id="link-status-message" class="text-muted mt-2"></p></div>` : '';
        const offsetDisplay = `
            <div class="mt-2">
                <p class="text-muted mb-1" style="color: #4ADE80;">
                    <strong>Sync Offset:</strong> <span id="current-offset-display">${(offset || 0).toFixed(3)}</span> seconds
                </p>
                <div class="nudge-controls">
                    <button id="nudge-backward-btn" class="button button-small">«</button>
                    <input type="number" id="nudge-amount" class="form-control form-control-small" value="0.25" step="0.05" style="width: 70px;">
                    <span>s</span>
                    <button id="nudge-forward-btn" class="button button-small">»</button>
                    <button id="clear-offset-btn" class="button button-small" style="margin-left: auto;">Clear</button>
                </div>
            </div>`;
        const prerollCalculator = `
            <details class="match-details-dropdown" style="margin-top: 1rem;">
                <summary>Set Pre-Roll Offset</summary>
                <div class="match-feedback" style="background-color: var(--background-medium);">
                    <div class="form-group">
                        <label for="transcript-preroll">Transcript Pre-Roll (seconds)</label>
                        <div class="input-group"><input type="number" id="transcript-preroll" class="form-control" placeholder="e.g., 50.5"><button class="button button-small" id="get-transcript-time-btn" title="Activate Sync Mode in Viewer">🎯</button></div>
                    </div>
                    <div class="form-group">
                        <label for="streaming-preroll">Streaming Pre-Roll (seconds)</label>
                        <div class="input-group"><input type="number" id="streaming-preroll" class="form-control" placeholder="e.g., 30.0"><button class="button button-small" id="get-streaming-time-btn" title="Get Current Player Time">▶️</button></div>
                    </div>
                    <button id="calculate-offset-btn" class="button button-small button-primary">Calculate & Save Offset</button>
                </div>
            </details>`;
        mediaSyncPanel.innerHTML = `<p class="text-muted" style="color: #4ADE80;">✅ ${mediaType} is linked.</p><div>${sourceIndicator} <code style="font-size: 0.9em; word-break: break-all;">${path}</code></div><button id="unlink-media-btn" class="button button-danger button-small mt-2">Unlink Media</button>${statusCheckHtml}${offsetDisplay}${prerollCalculator}`;
        if (xmlSyncPanel) { const scanBtn = xmlSyncPanel.querySelector('#scan-xml-btn'); if (scanBtn) scanBtn.disabled = false; const statusEl = xmlSyncPanel.querySelector('#xml-link-status'); if(statusEl && statusEl.dataset.wasDisabled) { statusEl.innerHTML = ''; delete statusEl.dataset.wasDisabled; } }
    };

    const renderUnlinkedState = () => {
        mediaSyncPanel.innerHTML = `<p class="text-muted">Link an audio or video file to enable interactive playback.</p><div class="toolbar" style="gap: 0.5rem; justify-content: flex-start;"><button id="scan-audio-btn" class="button">Scan for Local Audio (.mp3)</button><button id="scan-video-btn" class="button">Scan for Local Video (.mp4)</button></div><p id="media-link-status" class="text-muted mt-2" style="transition: opacity 0.3s; opacity: 0;"></p>`;
        if (xmlSyncPanel) { const scanBtn = xmlSyncPanel.querySelector('#scan-xml-btn'); if (scanBtn) scanBtn.disabled = false; const statusEl = xmlSyncPanel.querySelector('#xml-link-status'); if(statusEl) { statusEl.innerHTML = '<p class="text-muted">Scan for podcast metadata in local XML files.</p>'; statusEl.dataset.wasDisabled = "false"; } }
    };

    const checkMediaStatus = async () => {
        try {
            const response = await fetch(`/api/document/${docId}/media_status`);
            if (!response.ok) throw new Error(`Server responded with status: ${response.status}`);
            const result = await response.json();
            if (result.linked) {
                renderLinkedState(result.type, result.path, result.source, result.offset);
            } else {
                renderUnlinkedState();
            }
        } catch (error) {
            console.error("Failed to check media status:", error);
            mediaSyncPanel.innerHTML = `<p class="text-danger">Error checking media status. Check console for details.</p>`;
        }
    };

    mediaSyncPanel.addEventListener('click', async (event) => {
        const target = event.target;
        const statusEl = document.getElementById('media-link-status');
        const handleScan = async (mediaType) => { const scanBtn = document.getElementById(`scan-${mediaType}-btn`); if (scanBtn) scanBtn.disabled = true; if (statusEl) { statusEl.textContent = `Scanning for ${mediaType}...`; statusEl.style.opacity = '1'; } try { const response = await fetch(`/api/document/${docId}/find_${mediaType}`, { method: 'POST', headers: { 'X-CSRFToken': CSRF_TOKEN } }); const result = await response.json(); if (result.success) { if(statusEl) statusEl.textContent = `✅ ${mediaType.charAt(0).toUpperCase() + mediaType.slice(1)} found! Viewer will reload.`; docViewerIframe.contentWindow.location.reload(true); await checkMediaStatus(); } else if(statusEl) { statusEl.textContent = `❌ ${result.message}`; } } catch (error) { if(statusEl) statusEl.textContent = `Error during ${mediaType} scan.`; } finally { setTimeout(() => { if (statusEl) statusEl.style.opacity = '0'; if (scanBtn) scanBtn.disabled = false; }, 4000); } };
        if (target.id === 'scan-audio-btn') await handleScan('audio');
        if (target.id === 'scan-video-btn') await handleScan('video');
        if (target.id === 'unlink-media-btn') { if (!confirm('Are you sure you want to unlink this media file?')) return; target.disabled = true; target.textContent = 'Unlinking...'; try { const response = await fetch(`/api/document/${docId}/unlink_media`, { method: 'POST', headers: { 'X-CSRFToken': CSRF_TOKEN } }); const result = await response.json(); if (!result.success) throw new Error(result.message); docViewerIframe.contentWindow.location.reload(true); await checkMediaStatus(); } catch (error) { alert(`Error: ${error.message}`); target.disabled = false; target.textContent = 'Unlink Media'; } }
        
        async function saveOffset(newOffset, reload = true) {
             try {
                const response = await fetch(`/api/document/${docId}/save_audio_offset`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ offset: newOffset }) });
                if (!response.ok) throw new Error('Failed to save offset');
                if (docViewerIframe.contentWindow) {
                    docViewerIframe.contentWindow.postMessage({ type: 'offsetUpdated', docId: docId, newOffset: newOffset }, '*');
                }
                await checkMediaStatus();
            } catch (err) { console.error("Failed to save offset", err); alert('Could not save the new sync offset.'); }
        }

        if (target.id === 'clear-offset-btn') { if (confirm('Are you sure you want to clear the audio sync offset?')) await saveOffset(0.0, false); }
        if (target.id === 'calculate-offset-btn') { const transcriptPreroll = parseFloat(document.getElementById('transcript-preroll').value); const streamingPreroll = parseFloat(document.getElementById('streaming-preroll').value); if (isNaN(transcriptPreroll) || isNaN(streamingPreroll)) { alert('Please enter valid numbers for both pre-roll fields.'); return; } const newOffset = streamingPreroll - transcriptPreroll; await saveOffset(newOffset, false); }
        if (target.id === 'get-transcript-time-btn') { if (docViewerIframe.contentWindow) docViewerIframe.contentWindow.postMessage({ type: 'setSyncMode', active: true }, '*'); }
        if (target.id === 'get-streaming-time-btn') { if (docViewerIframe.contentWindow) docViewerIframe.contentWindow.postMessage({ type: 'getCurrentAudioTime' }, '*'); }
        if (target.id === 'nudge-backward-btn' || target.id === 'nudge-forward-btn') { const currentOffsetDisplay = document.getElementById('current-offset-display'); const nudgeAmountEl = document.getElementById('nudge-amount'); if (!currentOffsetDisplay || !nudgeAmountEl) return; const currentOffset = parseFloat(currentOffsetDisplay.textContent); const nudgeAmount = parseFloat(nudgeAmountEl.value); if (isNaN(currentOffset) || isNaN(nudgeAmount)) return; const newOffset = target.id === 'nudge-backward-btn' ? currentOffset - nudgeAmount : currentOffset + nudgeAmount; await saveOffset(newOffset, false); }
        if (event.target.id === 'check-status-btn') { const btn = event.target; const statusMsg = document.getElementById('link-status-message'); const url = mediaSyncPanel.querySelector('code').textContent; btn.disabled = true; btn.textContent = 'Checking...'; statusMsg.textContent = ''; try { const response = await fetch(`/api/document/${docId}/check_url_status`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ url: url }) }); const result = await response.json(); if (result.status === 'online') statusMsg.style.color = '#4ADE80'; else if (result.status === 'warning') statusMsg.style.color = '#e0843a'; else statusMsg.style.color = 'var(--red-danger)'; statusMsg.textContent = result.message; } catch (error) { statusMsg.style.color = 'var(--red-danger)'; statusMsg.textContent = 'An error occurred while checking the link.'; } finally { btn.disabled = false; btn.textContent = 'Check Link Status'; } }
    });

    if (xmlSyncPanel) {
        const statusContainer = document.getElementById('xml-link-status');
        const renderXmlMatchList = (result) => {
            const { matches, xml_files_scanned } = result;
            const docPath = workbenchContainer.dataset.docPath;
            const targetSrtBasename = docPath.includes('/') ? docPath.substring(docPath.lastIndexOf('/') + 1).replace(/\.srt$/, '') : docPath.replace(/\.srt$/, '');
            if (matches.length === 0) { statusContainer.innerHTML = xml_files_scanned > 0 ? `<p class="text-muted">Scanned ${xml_files_scanned} XML file(s), but no potential episode entries were found for <code>${targetSrtBasename}.srt</code>.</p>` : '<p class="text-muted">No .xml files were found in your documents directory to scan.</p>'; return; }
            let listHtml = `<p>Found ${matches.length} potential match(es) from ${xml_files_scanned} XML file(s).</p><ul class="simple-list">`;
            matches.forEach(match => {
                const { preview, enclosure_url } = match;
                const title = preview.title || '<em>No Title</em>';
                const author = preview.author ? (preview.author[0].literal || '') : '<em>No Author</em>';
                const date = preview.issued ? preview.issued['date-parts'][0].join('-') : '<em>No Date</em>';
                const enclosureFilename = enclosure_url ? enclosure_url.split('/').pop().split('?')[0] : '';
                const enclosureBasename = enclosureFilename.substring(0, enclosureFilename.lastIndexOf('.')) || '';
                const enclosureMatch = enclosureBasename === targetSrtBasename;
                const titleMatch = preview.title && preview.title.includes(targetSrtBasename);
                const feedbackHtml = `<div class="match-feedback"><strong class="feedback-title">Matching Confidence Report:</strong><ul><li class="${enclosureMatch ? 'match-success' : 'match-neutral'}"><span class="match-icon">${enclosureMatch ? '✅' : 'ℹ️'}</span><div><strong>Enclosure Filename Match (High Confidence):</strong>${ enclosureMatch ? `<span>SRT and Enclosure filenames match.</span><code>${enclosureFilename}</code>` : `<span>SRT filename (<em>${targetSrtBasename}</em>) does not match enclosure.</span><code>${enclosureFilename || 'Not Found'}</code>` }</div></li><li class="${titleMatch ? 'match-success' : 'match-neutral'}"><span class="match-icon">${titleMatch ? '✅' : 'ℹ️'}</span><div><strong>Title Substring Match (Low Confidence):</strong>${ titleMatch ? `<span>SRT filename (<em>${targetSrtBasename}</em>) found within XML title.</span><code>${preview.title}</code>` : `<span>SRT filename not in XML title.</span><code>${preview.title}</code>` }</div></li></ul></div>`;
                const linkButtonHtml = enclosure_url ? `<button class="button button-small link-from-web-btn" data-url="${enclosure_url}" title="Link directly from: ${enclosure_url}">Link from Web</button>` : '';
                listHtml += `<li style="padding: 0.75rem; border: 1px solid var(--border-color); border-radius: 4px; margin-bottom: 0.5rem;"><div style="display: flex; justify-content: space-between; align-items: center;"><div><strong>${title}</strong><p class="text-muted mb-0" style="font-size: 0.9em;">By ${author} on ${date} <br><code style="font-size: 0.8em;">Found in: ${match.xml_path}</code></p></div><div class="toolbar" style="gap: 0.5rem;">${linkButtonHtml}<button class="button button-small import-xml-btn" data-xml-path="${match.xml_path}" data-item-hash="${match.item_hash}">Import Metadata</button></div></div><details class="match-details-dropdown"><summary>Show Confidence Report</summary>${feedbackHtml}</details></li>`;
            });
            listHtml += '</ul>';
            statusContainer.innerHTML = listHtml;
        };
        const renderStatusUpdate = (message, isError = false) => { statusContainer.innerHTML = `<p class="${isError ? 'text-danger' : 'text-muted'}">${message}</p>`; };
        xmlSyncPanel.addEventListener('click', async (event) => { const target = event.target; if (target.id === 'scan-xml-btn') { target.disabled = true; target.textContent = 'Scanning...'; renderStatusUpdate('Scanning filesystem...'); try { const response = await fetch(`/api/document/${docId}/find_metadata_xml`, { method: 'POST', headers: { 'X-CSRFToken': CSRF_TOKEN } }); const result = await response.json(); if (result.success) renderXmlMatchList(result); else renderStatusUpdate(`❌ ${result.message}`, true); } catch (error) { console.error("Scan failed:", error); renderStatusUpdate('A network error occurred during the scan.', true); } finally { target.disabled = false; target.textContent = 'Scan for XML File'; } } if (target.classList.contains('import-xml-btn')) { const xmlPath = target.dataset.xmlPath, itemHash = target.dataset.itemHash; if (!confirm(`Import this metadata? This will overwrite existing bibliographic data.`)) return; target.disabled = true; target.textContent = 'Importing...'; try { const response = await fetch(`/api/document/${docId}/import_from_xml`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ xml_path: xmlPath, item_hash: itemHash }) }); const result = await response.json(); if (result.success && result.csl_json) { renderStatusUpdate('✅ Success! Metadata imported.', false); if (metadataTab && metadataTab.populateForm) { metadataTab.populateForm(JSON.parse(result.csl_json)); } const metadataTabLink = document.querySelector('.tab-link[data-tab="metadata"]'); if (metadataTabLink) metadataTabLink.click(); } else { renderStatusUpdate(`❌ ${result.message || 'Import failed.'}`, true); target.disabled = false; target.textContent = 'Import'; } } catch (error) { console.error("Import failed:", error); renderStatusUpdate('A network error occurred.', true); target.disabled = false; target.textContent = 'Import'; } } if (target.classList.contains('link-from-web-btn')) { const url = target.dataset.url; if (!confirm(`Link this document to audio from the web?\n\nURL: ${url}`)) return; target.disabled = true; target.textContent = 'Linking...'; try { const response = await fetch(`/api/document/${docId}/link_audio_from_url`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ url: url }) }); const result = await response.json(); if (!result.success) throw new Error(result.message); docViewerIframe.contentWindow.location.reload(true); await checkMediaStatus(); } catch (error) { alert(`Error: ${error.message}`); target.disabled = false; target.textContent = 'Link from Web'; } } });
    }
    
    // Initial load of media status when the page loads
    checkMediaStatus();

    // The message listener for SRT specific preroll calculations
    window.addEventListener('message', (event) => {
        if (event.source !== docViewerIframe.contentWindow) return;
        if (event.data.type === 'setTranscriptTime') {
            const input = document.getElementById('transcript-preroll');
            if (input) input.value = event.data.time.toFixed(3);
            if (docViewerIframe.contentWindow) {
                docViewerIframe.contentWindow.postMessage({ type: 'setSyncMode', active: false }, '*');
            }
        }
        if (event.data.type === 'returnCurrentAudioTime') {
            const input = document.getElementById('streaming-preroll');
            if (input) input.value = event.data.time.toFixed(3);
        }
    });
});