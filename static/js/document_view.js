// --- File: static/js/document_view.js ---

document.addEventListener('DOMContentLoaded', () => {
    // === GLOBAL ELEMENT SELECTORS ===
    const workbenchContainer = document.querySelector('.workbench-container');
    const docId = workbenchContainer.dataset.docId;
    const fileType = workbenchContainer.dataset.fileType;
    const toggleHtmlViewBtn = document.getElementById('toggle-html-view-btn');
    const docViewerIframe = document.getElementById('doc-viewer');
    const docPageCount = parseInt(workbenchContainer.dataset.docPageCount, 10);
    const CSRF_TOKEN = document.querySelector('#csrf-form-container input[name="csrf_token"]').value;

    // --- Tab Switching Logic ---
    const sidebar = document.querySelector('.workbench-sidebar');
    if (sidebar) {
        const tabLinks = sidebar.querySelectorAll('.tab-link');
        const tabContents = sidebar.querySelectorAll('.tab-content');
        tabLinks.forEach(link => {
            link.addEventListener('click', (event) => {
                event.preventDefault();
                const tabId = link.dataset.tab;
                tabLinks.forEach(l => l.classList.remove('active'));
                link.classList.add('active');
                tabContents.forEach(content => {
                    content.classList.toggle('active', content.id === `tab-${tabId}`);
                });
                if (tabId === 'discovery') {
                    loadDiscoveryData();
                }
            });
        });

        sidebar.addEventListener('click', (event) => {
            const panelHeader = event.target.closest('.panel-header');
            if (panelHeader) {
                const panel = panelHeader.closest('.panel');
                if (panel) {
                    panel.classList.toggle('collapsed');
                }
            }
        });
    }

    if (toggleHtmlViewBtn && docViewerIframe) {
        const docPath = workbenchContainer.dataset.docPath;
        const rawViewUrl = `/serve_doc/${docPath}`;
        const strippedViewUrl = `/view_html/${docId}`;
        toggleHtmlViewBtn.addEventListener('click', () => {
            const currentSrc = docViewerIframe.getAttribute('src');
            if (currentSrc.includes('/serve_doc/')) {
                docViewerIframe.setAttribute('src', strippedViewUrl);
                toggleHtmlViewBtn.textContent = 'Show Original HTML';
            } else {
                docViewerIframe.setAttribute('src', rawViewUrl);
                toggleHtmlViewBtn.textContent = 'Show Stripped Text';
            }
        });
    }

    // --- DISCOVERY TAB LOGIC ---
    const discoveryContentContainer = document.getElementById('doc-discovery-content');
    let discoveryTabLoaded = false;
    async function loadDiscoveryData() {
        if (discoveryTabLoaded || !discoveryContentContainer) return;
        discoveryContentContainer.innerHTML = '<p class="text-muted">Loading entities...</p>';
        try {
            const response = await fetch(`/api/document/${docId}/entities`);
            if (!response.ok) throw new Error('Network error');
            const data = await response.json();
            discoveryTabLoaded = true;
            renderDiscoveryData(data);
        } catch (error) {
            console.error("Failed to load document entities:", error);
            discoveryContentContainer.innerHTML = '<p class="text-danger">Could not load entities.</p>';
        }
    }
    function renderDiscoveryData({ entities_by_label, sorted_labels }) {
        if (!discoveryContentContainer) return;
        discoveryContentContainer.innerHTML = '';
        if (sorted_labels.every(label => !entities_by_label[label] || entities_by_label[label].length === 0)) {
            discoveryContentContainer.innerHTML = '<p class="text-muted">No entities were extracted from this document.</p>';
            return;
        }
        const accordion = document.createElement('div');
        accordion.className = 'accordion';
        sorted_labels.forEach(label => {
            const entities = entities_by_label[label];
            if (entities && entities.length > 0) {
                const details = document.createElement('details'); details.className = 'accordion-item';
                const summary = document.createElement('summary'); summary.className = 'accordion-header';
                summary.innerHTML = `<span>${label} <span class="chip">${entities.length} unique</span></span>`;
                const body = document.createElement('div'); body.className = 'accordion-body';
                const list = document.createElement('ul'); list.className = 'simple-list';
                entities.forEach(entity => {
                    const li = document.createElement('li');
                    const pageLinks = entity.pages.split(',').map(page => `<a href="#page=${page}" class="page-link" data-page="${page}">${page}</a>`).join(', ');
                    li.innerHTML = `<a href="/discover/entity/${encodeURIComponent(entity.label)}/${encodeURIComponent(entity.text)}">${entity.text}</a><span class="text-muted ms-2" style="font-size: 0.9em;">(${entity.appearance_count} on pages: ${pageLinks})</span>`;
                    list.appendChild(li);
                });
                body.appendChild(list); details.appendChild(summary); details.appendChild(body); accordion.appendChild(details);
            }
        });
        discoveryContentContainer.appendChild(accordion);
    }
    if (discoveryContentContainer) {
        discoveryContentContainer.addEventListener('click', (e) => {
            if (e.target.classList.contains('page-link')) {
                e.preventDefault();
                const pageNum = e.target.dataset.page;
                if (docViewerIframe && docViewerIframe.contentWindow) docViewerIframe.contentWindow.postMessage({ type: 'scrollToPage', page: pageNum }, '*');
            }
        });
    }

    // --- CURATION TAB LOGIC ---
    const curationTab = document.getElementById('tab-curation');
    if (curationTab) {
        const noteTextarea = document.getElementById('note-content');
        const saveNoteBtn = document.getElementById('save-curation-btn');
        const commentList = document.getElementById('comment-list');
        const newCommentText = document.getElementById('new-comment-text');
        const postCommentBtn = document.getElementById('post-comment-btn');
        const tagContainer = document.getElementById('tag-container');
        const tagInput = document.getElementById('tag-input');
        const catalogListContainer = document.getElementById('catalog-list-container');
        const favoriteToggle = document.getElementById('is-favorite-toggle');
        const createCatalogBtn = document.getElementById('create-catalog-btn');
        const newCatalogNameInput = document.getElementById('new-catalog-name');
        const colorPalette = document.getElementById('color-palette');
        const currentPageDisplay = document.getElementById('current-page-display');
        const pageNumSingleInputForPlaceholder = document.getElementById('page-num-single');
        let favoritesCatalogId = null;

        window.addEventListener('message', (event) => {
            if (event.source !== docViewerIframe.contentWindow) return;
            if (event.data.type === 'insertIntoComment' && event.data.text) {
                const currentText = newCommentText.value;
                if (currentText && !currentText.endsWith('\n\n')) newCommentText.value += '\n\n';
                newCommentText.value += event.data.text + '\n\n';
                newCommentText.focus();
                const curationTabLink = document.querySelector('.tab-link[data-tab="curation"]');
                if (curationTabLink && !curationTabLink.classList.contains('active')) curationTabLink.click();
            }
            if (event.data.type === 'pageChanged' && event.data.currentPage) {
                const pageNum = event.data.currentPage;
                if (currentPageDisplay) { const label = (fileType === 'SRT') ? 'Cue' : 'Page'; currentPageDisplay.textContent = `Current View: ${label} ${pageNum}`; }
                if (pageNumSingleInputForPlaceholder) pageNumSingleInputForPlaceholder.placeholder = `e.g., ${pageNum}`;
            }
            if (event.data.type === 'offsetUpdated' && event.data.docId === docId) {
                if (fileType === 'SRT') {
                    checkMediaStatus();
                }
            }
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
        async function loadCurationData() {
            try {
                const response = await fetch(`/api/document/${docId}/curation`);
                const data = await response.json();
                noteTextarea.value = data.note;
                renderComments(data.comments, data.current_user);
                renderCatalogs(data.all_catalogs, data.member_of_catalogs, data.is_favorite);
                const tagResponse = await fetch(`/api/document/${docId}/tags`);
                const tagData = await tagResponse.json();
                renderTags(tagData.tags);
            } catch (error) { console.error("Failed to load curation data:", error); }
        }
        function renderComments(comments, currentUser) {
            commentList.innerHTML = '';
            if (comments.length === 0) { commentList.innerHTML = '<p class="text-muted">No comments yet.</p>'; return; }
            const timestampRegex = /\[(\d{2}:\d{2}:\d{2},\d{3}\s*→\s*\d{2}:\d{2}:\d{2},\d{3})\]\s*([\s\S]*?)(?=\n\[\d{2}:\d{2}:\d{2},\d{3}|\n\n|$)/g;
            comments.forEach(comment => {
                const canDelete = currentUser.role === 'admin' || currentUser.id === comment.user_id;
                const deleteBtnHtml = canDelete ? `<button class="button-delete" data-comment-id="${comment.id}" title="Delete comment">×</button>` : '';
                const commentDate = new Date(comment.created_at).toLocaleString();
                const commentEl = document.createElement('div');
                commentEl.className = 'comment-item';
                const escapeHtml = (unsafe) => unsafe.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
                let processedCommentText = escapeHtml(comment.comment_text);
                processedCommentText = processedCommentText.replace(timestampRegex, (match, timestamp, dialogue) => {
                    const startTime = timestamp.split('→')[0].trim();
                    return `</div><div class="comment-quote-timestamp" data-timestamp="${startTime}" title="Click to seek audio">${timestamp.replace('→', ' → ')}</div><p class="comment-quote-dialogue">${dialogue.trim()}</p><div class="comment-body">`;
                });
                commentEl.innerHTML = `<div class="comment-header"><strong>${comment.username}</strong>${deleteBtnHtml}</div><div class="comment-body">${processedCommentText}</div><small class="text-muted">${commentDate}</small>`;
                commentList.appendChild(commentEl);
            });
        }
        function renderTags(tags) {
            tagContainer.innerHTML = '';
            tags.forEach(tag => {
                const tagEl = document.createElement('span'); tagEl.className = 'tag-item'; tagEl.textContent = tag;
                const removeBtn = document.createElement('button'); removeBtn.className = 'tag-remove-btn'; removeBtn.innerHTML = '×'; removeBtn.dataset.tag = tag;
                tagEl.appendChild(removeBtn); tagContainer.appendChild(tagEl);
            });
        }
        function renderCatalogs(allCatalogs, memberOf, isFavorite) {
            favoritesCatalogId = allCatalogs.find(c => c.name === '⭐ Favorites')?.id;
            favoriteToggle.checked = isFavorite;
            catalogListContainer.innerHTML = '';
            allCatalogs.filter(c => c.id !== favoritesCatalogId).forEach(catalog => {
                const isMember = memberOf.includes(catalog.id);
                const catalogEl = document.createElement('div'); catalogEl.className = 'form-check';
                catalogEl.innerHTML = `<input type="checkbox" class="form-check-input catalog-checkbox" id="cat-${catalog.id}" data-cat-id="${catalog.id}" ${isMember ? 'checked' : ''}><label class="form-check-label" for="cat-${catalog.id}">${catalog.name}</label>`;
                catalogListContainer.appendChild(catalogEl);
            });
        }
        async function saveTags(tags) { await fetch(`/api/document/${docId}/tags`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ tags: tags }) }); await loadCurationData(); }
        async function updateCatalogMembership() {
            const memberOf = Array.from(catalogListContainer.querySelectorAll('.catalog-checkbox:checked')).map(cb => parseInt(cb.dataset.catId));
            if (favoriteToggle.checked && favoritesCatalogId && !memberOf.includes(favoritesCatalogId)) memberOf.push(favoritesCatalogId);
            await fetch(`/api/document/${docId}/catalogs`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ catalog_ids: memberOf }) });
        }
        if (colorPalette) {
            const initialColor = workbenchContainer.dataset.docColor;
            if(initialColor) { const selected = colorPalette.querySelector(`[data-color="${initialColor}"]`); if(selected) selected.classList.add('selected'); }
            colorPalette.addEventListener('click', (e) => {
                const colorSpan = e.target.closest('.palette-color'); if (!colorSpan) return;
                const color = colorSpan.dataset.color;
                fetch(`/api/document/${docId}/color`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ color: color }) })
                .then(() => { colorPalette.querySelectorAll('.palette-color').forEach(el => el.classList.remove('selected')); colorSpan.classList.add('selected'); });
            });
        }
        if (saveNoteBtn) { saveNoteBtn.addEventListener('click', () => { fetch(`/api/document/${docId}/curation`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ note: noteTextarea.value }) }).then(() => { saveNoteBtn.textContent = 'Saved!'; setTimeout(() => { saveNoteBtn.textContent = 'Save Note'; }, 2000); }); }); }
        if (postCommentBtn) { postCommentBtn.addEventListener('click', async () => { const text = newCommentText.value.trim(); if (!text) return; const response = await fetch(`/api/document/${docId}/comments`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ comment_text: text }) }); if (response.ok) { newCommentText.value = ''; await loadCurationData(); } }); }
        if (commentList) {
            commentList.addEventListener('click', async (e) => {
                const quoteTimestamp = e.target.closest('.comment-quote-timestamp');
                if (quoteTimestamp && fileType === 'SRT') {
                    const timestamp = quoteTimestamp.dataset.timestamp;
                    if (timestamp && docViewerIframe.contentWindow) docViewerIframe.contentWindow.postMessage({ type: 'seekToTimestamp', timestamp: timestamp }, '*');
                    return; 
                }
                if (e.target.classList.contains('button-delete')) {
                    const commentId = e.target.dataset.commentId;
                    if (!confirm('Are you sure you want to delete this comment?')) return;
                    const response = await fetch(`/api/comments/${commentId}`, { method: 'DELETE', headers: { 'X-CSRFToken': CSRF_TOKEN } });
                    if (response.ok) await loadCurationData();
                }
            });
        }
        if (tagInput) {
            tagInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault(); const newTag = tagInput.value.trim().toLowerCase(); if (!newTag) return;
                    const currentTags = Array.from(tagContainer.querySelectorAll('.tag-item')).map(el => el.textContent.slice(0, -1));
                    if (!currentTags.includes(newTag)) { saveTags([...currentTags, newTag]); tagInput.value = ''; }
                }
            });
        }
        if (tagContainer) {
            tagContainer.addEventListener('click', (e) => {
                if (e.target.classList.contains('tag-remove-btn')) {
                    const tagToRemove = e.target.dataset.tag;
                    let currentTags = Array.from(tagContainer.querySelectorAll('.tag-item')).map(el => el.textContent.slice(0, -1));
                    currentTags = currentTags.filter(t => t !== tagToRemove);
                    saveTags(currentTags);
                }
            });
        }
        if (catalogListContainer) { catalogListContainer.addEventListener('change', updateCatalogMembership); }
        if (favoriteToggle) { favoriteToggle.addEventListener('change', updateCatalogMembership); }
        if (createCatalogBtn) {
            createCatalogBtn.addEventListener('click', async () => {
                const catalogName = newCatalogNameInput.value.trim(); if (!catalogName) { alert('Please enter a name for the new catalog.'); return; }
                try {
                    const response = await fetch('/api/catalogs', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ name: catalogName }) });
                    const result = await response.json(); if (!response.ok) throw new Error(result.message || 'Failed to create catalog.');
                    newCatalogNameInput.value = ''; await loadCurationData();
                    setTimeout(async () => {
                        const newCatalogCheckbox = catalogListContainer.querySelector(`.catalog-checkbox[id="cat-${result.catalog.id}"]`);
                        if (newCatalogCheckbox) { newCatalogCheckbox.checked = true; await updateCatalogMembership(); }
                    }, 100);
                } catch (error) { alert(`Error: ${error.message}`); }
            });
        }
        loadCurationData();
    }

    // --- METADATA PANEL LOGIC ---
    const metadataTab = document.getElementById('tab-metadata');
    if (metadataTab) {
        const typeSelect = document.getElementById('csl-type');
        const saveBtn = document.getElementById('save-metadata-btn');
        const saveStatus = document.getElementById('metadata-save-status');
        
        function populateYearSelect() { const yearSelect = document.getElementById('csl-date-year'); if(!yearSelect) return; const currentYear = new Date().getFullYear(); yearSelect.innerHTML = '<option value="">Year</option>'; for (let y = currentYear + 1; y >= 1600; y--) yearSelect.add(new Option(y, y)); }
        function populateMonthSelect() { const monthSelect = document.getElementById('csl-date-month'); if(!monthSelect) return; const months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]; monthSelect.innerHTML = '<option value="">Month</option>'; months.forEach((month, i) => monthSelect.add(new Option(month, i + 1))); }
        function populateDaySelect() { const daySelect = document.getElementById('csl-date-day'); if(!daySelect) return; daySelect.innerHTML = '<option value="">Day</option>'; for (let d = 1; d <= 31; d++) daySelect.add(new Option(d, d)); }
        function populateDocTypeSelect() {
            if(!typeSelect) return;
            const types = { 'report': 'Report', 'article-journal': 'Journal Article', 'book': 'Book', 'webpage': 'Webpage', 'broadcast': 'TV/Radio Broadcast', 'interview': 'Interview/Podcast', 'motion_picture': 'Movie / Short Film','entry-encyclopedia': 'Encyclopedia Entry', 'chapter': 'Book Chapter', 'manuscript': 'Manuscript', 'bill': 'Bill / Legislation' };
            typeSelect.innerHTML = ''; for (const [value, text] of Object.entries(types)) typeSelect.add(new Option(text, value)); 
        }
        function updateFormFields() { const selectedType = typeSelect.value; document.querySelectorAll('.csl-field-specific').forEach(field => { const visibleFor = field.dataset.cslType.split(' '); field.style.display = visibleFor.includes(selectedType) ? 'block' : 'none'; }); const containerTitleLabel = document.querySelector('label[for="csl-container-title"]'); const publisherLabel = document.querySelector('label[for="csl-publisher"]'); if (selectedType === 'article-journal') containerTitleLabel.textContent = 'Journal Title'; else if (selectedType === 'broadcast') { containerTitleLabel.textContent = 'Program / Series Title'; publisherLabel.textContent = 'Network / Station'; } else if (selectedType === 'interview') containerTitleLabel.textContent = 'Podcast / Publication Series'; else if (selectedType === 'motion_picture') publisherLabel.textContent = 'Studio / Distributor'; else publisherLabel.textContent = 'Publisher / Agency'; }
        function populateForm(cslData) { const data = cslData || {}; typeSelect.value = data.type || 'report'; const docTitleElement = document.querySelector('.document-file-name'); document.getElementById('csl-title').value = data.title || (docTitleElement ? docTitleElement.textContent : ''); document.getElementById('csl-author').value = (data.author || []).map(a => a.literal ? a.literal : `${a.family || ''}, ${a.given || ''}`.trim().replace(/^,|,$/g, '')).join('; '); const yearSelect = document.getElementById('csl-date-year'); const monthSelect = document.getElementById('csl-date-month'); const daySelect = document.getElementById('csl-date-day'); if (data.issued && data.issued['date-parts'] && data.issued['date-parts'][0]) { const [year, month, day] = data.issued['date-parts'][0]; yearSelect.value = year || ''; monthSelect.value = month || ''; daySelect.value = day || ''; } else { yearSelect.value = ''; monthSelect.value = ''; daySelect.value = ''; } document.getElementById('csl-publisher').value = data.publisher || ''; document.getElementById('csl-container-title').value = data['container-title'] || ''; document.getElementById('csl-url').value = data.URL || ''; updateFormFields(); }
        function buildCslFromForm() { const csl = { id: `doc-${docId}`, type: typeSelect.value }; const title = document.getElementById('csl-title').value.trim(); if (title) csl.title = title; const authorString = document.getElementById('csl-author').value.trim(); if (authorString) csl.author = authorString.split(';').map(name => ({ 'literal': name.trim() })).filter(a => a.literal); const dateParts = [document.getElementById('csl-date-year').value, document.getElementById('csl-date-month').value, document.getElementById('csl-date-day').value].filter(Boolean).map(p => parseInt(p, 10)); if (dateParts.length > 0) csl.issued = { 'date-parts': [dateParts] }; const publisher = document.getElementById('csl-publisher').value.trim(); if (['report', 'broadcast', 'interview', 'motion_picture'].includes(csl.type) && publisher) csl.publisher = publisher; const containerTitle = document.getElementById('csl-container-title').value.trim(); if (['article-journal', 'broadcast', 'interview'].includes(csl.type) && containerTitle) csl['container-title'] = containerTitle; const url = document.getElementById('csl-url').value.trim(); if (['webpage', 'broadcast', 'interview', 'motion_picture'].includes(csl.type) && url) csl.URL = url; return csl; }
        async function loadMetadata() { try { const response = await fetch(`/api/document/${docId}/metadata`); const data = await response.json(); populateForm(data.csl_json ? JSON.parse(data.csl_json) : null); } catch (error) { saveStatus.textContent = "Error loading data."; saveStatus.style.opacity = '1'; } }
        saveBtn.addEventListener('click', async () => { const newCslData = buildCslFromForm(); const cslJsonString = JSON.stringify(newCslData, null, 2); saveBtn.disabled = true; saveStatus.textContent = "Saving..."; saveStatus.style.opacity = '1'; try { const response = await fetch(`/api/document/${docId}/metadata`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ csl_json: cslJsonString }) }); const result = await response.json(); if (!response.ok) throw new Error(result.message); saveStatus.textContent = "Saved successfully."; setTimeout(() => { window.location.reload(); }, 1000); } catch (error) { saveStatus.textContent = `Error: ${error.message}`; saveBtn.disabled = false; } });
        typeSelect.addEventListener('change', updateFormFields);
        populateDocTypeSelect(); populateYearSelect(); populateMonthSelect(); populateDaySelect(); loadMetadata();
        metadataTab.populateForm = populateForm;
    }
    
    // --- MEDIA & XML SYNC LOGIC ---
    if (fileType === 'SRT') {
        const mediaSyncPanel = document.getElementById('media-sync-panel');
        const xmlSyncPanel = document.getElementById('xml-sync-panel');

        const renderLinkedState = (type, path, source, offset) => {
            const mediaType = type.charAt(0).toUpperCase() + type.slice(1);
            const sourceIndicator = (source === 'web') ? `<span class="chip" style="background-color: #2196f3;">Web</span>` : `<span class="chip">Local</span>`;
            const statusCheckHtml = (source === 'web') ? `<div class="mt-2"><button id="check-status-btn" class="button button-small">Check Link Status</button><p id="link-status-message" class="text-muted mt-2"></p></div>` : '';
            
            const offsetDisplay = `
                <div class="mt-2">
                    <p class="text-muted mb-1" style="color: #4ADE80;">
                        <strong>Sync Offset:</strong> <span id="current-offset-display">${(offset || 0).toFixed(2)}</span> seconds
                    </p>
                    <div class="nudge-controls">
                        <button id="nudge-backward-btn" class="button button-small">«</button>
                        <input type="number" id="nudge-amount" class="form-control form-control-small" value="0.25" style="width: 60px;">
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
                            <div class="input-group">
                                <input type="number" id="transcript-preroll" class="form-control" placeholder="e.g., 50.5">
                                <button class="button button-small" id="get-transcript-time-btn" title="Activate Sync Mode in Viewer">🎯</button>
                            </div>
                        </div>
                        <div class="form-group">
                            <label for="streaming-preroll">Streaming Pre-Roll (seconds)</label>
                            <div class="input-group">
                                <input type="number" id="streaming-preroll" class="form-control" placeholder="e.g., 30.0">
                                <button class="button button-small" id="get-streaming-time-btn" title="Get Current Player Time">▶️</button>
                            </div>
                        </div>
                        <button id="calculate-offset-btn" class="button button-small button-primary">Calculate & Save Offset</button>
                    </div>
                </details>`;

            mediaSyncPanel.innerHTML = `<p class="text-muted" style="color: #4ADE80;">✅ ${mediaType} is linked.</p><div>${sourceIndicator} <code style="font-size: 0.9em; word-break: break-all;">${path}</code></div><button id="unlink-media-btn" class="button button-danger button-small mt-2">Unlink Media</button>${statusCheckHtml}${offsetDisplay}${prerollCalculator}`;
            
            if (xmlSyncPanel) {
                const scanBtn = xmlSyncPanel.querySelector('#scan-xml-btn'); if (scanBtn) scanBtn.disabled = false;
                const statusEl = xmlSyncPanel.querySelector('#xml-link-status'); if(statusEl && statusEl.dataset.wasDisabled) { statusEl.innerHTML = ''; delete statusEl.dataset.wasDisabled; }
            }
        };

        const renderUnlinkedState = () => {
            mediaSyncPanel.innerHTML = `<p class="text-muted">Link an audio or video file to enable interactive playback.</p><div class="toolbar" style="gap: 0.5rem; justify-content: flex-start;"><button id="scan-audio-btn" class="button">Scan for Local Audio (.mp3)</button><button id="scan-video-btn" class="button">Scan for Local Video (.mp4)</button></div><p id="media-link-status" class="text-muted mt-2" style="transition: opacity 0.3s; opacity: 0;"></p>`;
            if (xmlSyncPanel) {
                const scanBtn = xmlSyncPanel.querySelector('#scan-xml-btn');
                if (scanBtn) scanBtn.disabled = false;
                const statusEl = xmlSyncPanel.querySelector('#xml-link-status');
                if(statusEl) {
                    statusEl.innerHTML = '<p class="text-muted">Scan for podcast metadata in local XML files.</p>';
                    statusEl.dataset.wasDisabled = "false";
                }
            }
        };

        const checkMediaStatus = async () => {
            try {
                const response = await fetch(`/api/document/${docId}/media_status`);
                const result = await response.json();
                if (result.linked) renderLinkedState(result.type, result.path, result.source, result.offset); else renderUnlinkedState();
            } catch (error) {
                mediaSyncPanel.innerHTML = '<p class="text-danger">Could not check media status.</p>';
            }
        };

        mediaSyncPanel.addEventListener('click', async (event) => {
            const target = event.target;
            const statusEl = document.getElementById('media-link-status');

            const handleScan = async (mediaType) => {
                const scanBtn = document.getElementById(`scan-${mediaType}-btn`); if (scanBtn) scanBtn.disabled = true;
                if (statusEl) { statusEl.textContent = `Scanning for ${mediaType}...`; statusEl.style.opacity = '1'; }
                try {
                    const response = await fetch(`/api/document/${docId}/find_${mediaType}`, { method: 'POST', headers: { 'X-CSRFToken': CSRF_TOKEN } });
                    const result = await response.json();
                    if (result.success) {
                        if(statusEl) statusEl.textContent = `✅ ${mediaType.charAt(0).toUpperCase() + mediaType.slice(1)} found! Viewer will reload.`;
                        docViewerIframe.contentWindow.location.reload(true);
                        await checkMediaStatus();
                    } else if(statusEl) {
                        statusEl.textContent = `❌ ${result.message}`;
                    }
                } catch (error) { if(statusEl) statusEl.textContent = `Error during ${mediaType} scan.`; }
                finally { setTimeout(() => { if (statusEl) statusEl.style.opacity = '0'; if (scanBtn) scanBtn.disabled = false; }, 4000); }
            };

            if (target.id === 'scan-audio-btn') await handleScan('audio');
            if (target.id === 'scan-video-btn') await handleScan('video');
            
            if (target.id === 'unlink-media-btn') {
                if (!confirm('Are you sure you want to unlink this media file?')) return;
                target.disabled = true; target.textContent = 'Unlinking...';
                try {
                    const response = await fetch(`/api/document/${docId}/unlink_media`, { method: 'POST', headers: { 'X-CSRFToken': CSRF_TOKEN } });
                    const result = await response.json(); if (!result.success) throw new Error(result.message);
                    docViewerIframe.contentWindow.location.reload(true);
                    await checkMediaStatus();
                } catch (error) { alert(`Error: ${error.message}`); target.disabled = false; target.textContent = 'Unlink Media'; }
            }
            
            async function saveOffset(newOffset, reload = true) {
                 try {
                    const response = await fetch(`/api/document/${docId}/save_audio_offset`, {
                        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ offset: newOffset })
                    });
                    if (!response.ok) throw new Error('Failed to save offset');
                    if (reload) docViewerIframe.contentWindow.location.reload(true);
                    await checkMediaStatus();
                } catch (err) {
                    console.error("Failed to save offset", err); 
                    alert('Could not save the new sync offset.');
                }
            }

            if (target.id === 'clear-offset-btn') {
                if (confirm('Are you sure you want to clear the audio sync offset?')) {
                    await saveOffset(0.0);
                }
            }
            
            if (target.id === 'calculate-offset-btn') {
                const transcriptPreroll = parseFloat(document.getElementById('transcript-preroll').value);
                const streamingPreroll = parseFloat(document.getElementById('streaming-preroll').value);
                if (isNaN(transcriptPreroll) || isNaN(streamingPreroll)) {
                    alert('Please enter valid numbers for both pre-roll fields.');
                    return;
                }
                const newOffset = streamingPreroll - transcriptPreroll;
                await saveOffset(newOffset);
            }

            if (target.id === 'get-transcript-time-btn') {
                if (docViewerIframe.contentWindow) {
                    docViewerIframe.contentWindow.postMessage({ type: 'setSyncMode', active: true }, '*');
                }
            }
            if (target.id === 'get-streaming-time-btn') {
                if (docViewerIframe.contentWindow) {
                    docViewerIframe.contentWindow.postMessage({ type: 'getCurrentAudioTime' }, '*');
                }
            }
            
            if (target.id === 'nudge-backward-btn' || target.id === 'nudge-forward-btn') {
                const currentOffsetDisplay = document.getElementById('current-offset-display');
                const nudgeAmountEl = document.getElementById('nudge-amount');
                if (!currentOffsetDisplay || !nudgeAmountEl) return;

                const currentOffset = parseFloat(currentOffsetDisplay.textContent);
                const nudgeAmount = parseFloat(nudgeAmountEl.value);

                if (isNaN(currentOffset) || isNaN(nudgeAmount)) return;

                const newOffset = target.id === 'nudge-backward-btn' 
                    ? currentOffset - nudgeAmount 
                    : currentOffset + nudgeAmount;

                await saveOffset(newOffset, false);
            }

            if (event.target.id === 'check-status-btn') {
                const btn = event.target;
                const statusMsg = document.getElementById('link-status-message');
                const url = mediaSyncPanel.querySelector('code').textContent;
                btn.disabled = true; btn.textContent = 'Checking...'; statusMsg.textContent = '';
                try {
                    const response = await fetch(`/api/document/${docId}/check_url_status`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ url: url }) });
                    const result = await response.json();
                    if (result.status === 'online') statusMsg.style.color = '#4ADE80';
                    else if (result.status === 'warning') statusMsg.style.color = '#e0843a';
                    else statusMsg.style.color = 'var(--red-danger)';
                    statusMsg.textContent = result.message;
                } catch (error) {
                    statusMsg.style.color = 'var(--red-danger)';
                    statusMsg.textContent = 'An error occurred while checking the link.';
                } finally {
                    btn.disabled = false; btn.textContent = 'Check Link Status';
                }
            }
        });

        if (xmlSyncPanel) {
            const statusContainer = document.getElementById('xml-link-status');
            const renderXmlMatchList = (result) => {
                const { matches, xml_files_scanned } = result;
                const docPath = workbenchContainer.dataset.docPath;
                const targetSrtBasename = docPath.includes('/') 
                    ? docPath.substring(docPath.lastIndexOf('/') + 1).replace(/\.srt$/, '')
                    : docPath.replace(/\.srt$/, '');

                if (matches.length === 0) {
                    statusContainer.innerHTML = xml_files_scanned > 0 
                        ? `<p class="text-muted">Scanned ${xml_files_scanned} XML file(s), but no potential episode entries were found for <code>${targetSrtBasename}.srt</code>.</p>` 
                        : '<p class="text-muted">No .xml files were found in your documents directory to scan.</p>';
                    return;
                }

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

                    const feedbackHtml = `
                        <div class="match-feedback">
                            <strong class="feedback-title">Matching Confidence Report:</strong>
                            <ul>
                                <li class="${enclosureMatch ? 'match-success' : 'match-neutral'}">
                                    <span class="match-icon">${enclosureMatch ? '✅' : 'ℹ️'}</span>
                                    <div><strong>Enclosure Filename Match (High Confidence):</strong>
                                    ${ enclosureMatch
                                        ? `<span>SRT and Enclosure filenames match.</span><code>${enclosureFilename}</code>`
                                        : `<span>SRT filename (<em>${targetSrtBasename}</em>) does not match enclosure.</span><code>${enclosureFilename || 'Not Found'}</code>`
                                    }
                                    </div>
                                </li>
                                <li class="${titleMatch ? 'match-success' : 'match-neutral'}">
                                    <span class="match-icon">${titleMatch ? '✅' : 'ℹ️'}</span>
                                    <div><strong>Title Substring Match (Low Confidence):</strong>
                                    ${ titleMatch
                                        ? `<span>SRT filename (<em>${targetSrtBasename}</em>) found within XML title.</span><code>${preview.title}</code>`
                                        : `<span>SRT filename not in XML title.</span><code>${preview.title}</code>`
                                    }
                                    </div>
                                </li>
                            </ul>
                        </div>`;

                    const linkButtonHtml = enclosure_url ? `<button class="button button-small link-from-web-btn" data-url="${enclosure_url}" title="Link directly from: ${enclosure_url}">Link from Web</button>` : '';
                    listHtml += `
                        <li style="padding: 0.75rem; border: 1px solid var(--border-color); border-radius: 4px; margin-bottom: 0.5rem;">
                            <div style="display: flex; justify-content: space-between; align-items: center;">
                                <div>
                                    <strong>${title}</strong>
                                    <p class="text-muted mb-0" style="font-size: 0.9em;">By ${author} on ${date} <br><code style="font-size: 0.8em;">Found in: ${match.xml_path}</code></p>
                                </div>
                                <div class="toolbar" style="gap: 0.5rem;">
                                    ${linkButtonHtml}
                                    <button class="button button-small import-xml-btn" data-xml-path="${match.xml_path}" data-item-hash="${match.item_hash}">Import Metadata</button>
                                </div>
                            </div>
                            <details class="match-details-dropdown">
                                <summary>Show Confidence Report</summary>
                                ${feedbackHtml}
                            </details>
                        </li>`;
                });
                listHtml += '</ul>';
                statusContainer.innerHTML = listHtml;
            };
            const renderStatusUpdate = (message, isError = false) => { statusContainer.innerHTML = `<p class="${isError ? 'text-danger' : 'text-muted'}">${message}</p>`; };
            
            xmlSyncPanel.addEventListener('click', async (event) => {
                const target = event.target;
                if (target.id === 'scan-xml-btn') {
                    target.disabled = true; target.textContent = 'Scanning...'; renderStatusUpdate('Scanning filesystem...');
                    try {
                        const response = await fetch(`/api/document/${docId}/find_metadata_xml`, { method: 'POST', headers: { 'X-CSRFToken': CSRF_TOKEN } });
                        const result = await response.json(); if (result.success) renderXmlMatchList(result); else renderStatusUpdate(`❌ ${result.message}`, true);
                    } catch (error) { console.error("Scan failed:", error); renderStatusUpdate('A network error occurred during the scan.', true); }
                    finally { target.disabled = false; target.textContent = 'Scan for XML File'; }
                }
                if (target.classList.contains('import-xml-btn')) {
                    const xmlPath = target.dataset.xmlPath, itemHash = target.dataset.itemHash;
                    if (!confirm(`Import this metadata? This will overwrite existing bibliographic data.`)) return;
                    target.disabled = true; target.textContent = 'Importing...';
                    try {
                        const response = await fetch(`/api/document/${docId}/import_from_xml`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ xml_path: xmlPath, item_hash: itemHash }) });
                        const result = await response.json();
                        if (result.success && result.csl_json) {
                            renderStatusUpdate('✅ Success! Metadata imported.', false);
                            if (metadataTab && metadataTab.populateForm) {
                                metadataTab.populateForm(JSON.parse(result.csl_json));
                            }
                            const metadataTabLink = document.querySelector('.tab-link[data-tab="metadata"]'); if (metadataTabLink) metadataTabLink.click();
                        } else { renderStatusUpdate(`❌ ${result.message || 'Import failed.'}`, true); target.disabled = false; target.textContent = 'Import'; }
                    } catch (error) { console.error("Import failed:", error); renderStatusUpdate('A network error occurred.', true); target.disabled = false; target.textContent = 'Import'; }
                }
                if (target.classList.contains('link-from-web-btn')) {
                    const url = target.dataset.url; if (!confirm(`Link this document to audio from the web?\n\nURL: ${url}`)) return;
                    target.disabled = true; target.textContent = 'Linking...';
                    try {
                        const response = await fetch(`/api/document/${docId}/link_audio_from_url`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ url: url }) });
                        const result = await response.json(); if (!result.success) throw new Error(result.message);
                        docViewerIframe.contentWindow.location.reload(true);
                        await checkMediaStatus();
                    } catch (error) { alert(`Error: ${error.message}`); target.disabled = false; target.textContent = 'Link from Web'; }
                }
            });
        }
        
        checkMediaStatus();
    }

    // --- COPY TEXT FUNCTIONALITY ---
    const copyTextBtn = document.getElementById('copy-text-btn');
    const copyTextMenu = document.getElementById('copy-text-menu');
    if (copyTextBtn && copyTextMenu) {
        const copyAllBtn = document.getElementById('copy-all-text-btn');
        const copySinglePageBtn = document.getElementById('copy-single-page-btn');
        const copyRangeBtn = document.getElementById('copy-range-page-btn');
        const pageNumSingleInput = document.getElementById('page-num-single');
        const pageNumStartInput = document.getElementById('page-num-start');
        const pageNumEndInput = document.getElementById('page-num-end');
        
        // NEW: Get the assistant command buttons
        const copyAssistantSingleBtn = document.getElementById('copy-assistant-single-page-btn');
        const copyAssistantRangeBtn = document.getElementById('copy-assistant-range-btn');

        const fetchAndCopyText = async (buttonElement, startPage = null, endPage = null) => {
            const originalText = buttonElement.textContent;
            buttonElement.textContent = 'Copying...'; buttonElement.disabled = true;
            let url = `/api/document/${docId}/text`;
            const params = new URLSearchParams();
            if (startPage !== null) params.append('start_page', startPage);
            if (endPage !== null) params.append('end_page', endPage);
            const queryString = params.toString();
            if (queryString) url += `?${queryString}`;
            try {
                const response = await fetch(url);
                if (!response.ok) throw new Error('Network response was not ok.');
                const data = await response.json();
                if (data.success && typeof data.text === 'string') {
                    if (data.text.trim() === '') {
                        alert('No text could be extracted for the selected page(s).');
                        buttonElement.textContent = 'No Text';
                    } else {
                        await navigator.clipboard.writeText(data.text);
                        buttonElement.textContent = 'Copied!';
                    }
                } else {
                    throw new Error(data.message || 'Failed to get text from server.');
                }
            } catch (error) {
                console.error('Copy failed:', error);
                alert(`Could not copy text: ${error.message}`);
                buttonElement.textContent = 'Error';
            } finally {
                setTimeout(() => {
                    buttonElement.textContent = originalText;
                    buttonElement.disabled = false;
                    copyTextMenu.classList.remove('show');
                }, 2000);
            }
        };
        
        // NEW: Reusable function to copy the assistant command
        const copyAssistantCommand = (buttonElement, pageString) => {
            const originalText = buttonElement.textContent;
            const commandString = `id:${docId} + page:${pageString} + summarize`;

            buttonElement.textContent = 'Copying...';
            buttonElement.disabled = true;

            navigator.clipboard.writeText(commandString).then(() => {
                buttonElement.textContent = 'Copied!';
            }).catch(err => {
                console.error('Failed to copy command:', err);
                alert('Could not copy the command. See console for details.');
                buttonElement.textContent = 'Error';
            }).finally(() => {
                setTimeout(() => {
                    buttonElement.textContent = originalText;
                    buttonElement.disabled = false;
                    copyTextMenu.classList.remove('show');
                }, 2000);
            });
        };

        copyTextBtn.addEventListener('click', (e) => { e.stopPropagation(); copyTextMenu.classList.toggle('show'); });
        document.addEventListener('click', (e) => { if (!copyTextMenu.contains(e.target) && !copyTextBtn.contains(e.target)) copyTextMenu.classList.remove('show'); });
        
        copyAllBtn.addEventListener('click', (e) => { e.preventDefault(); fetchAndCopyText(copyAllBtn); });
        
        copySinglePageBtn.addEventListener('click', (e) => {
            e.preventDefault();
            const pageNum = parseInt(pageNumSingleInput.value, 10);
            if (isNaN(pageNum) || pageNum < 1 || pageNum > docPageCount) { alert(`Please enter a valid page number between 1 and ${docPageCount}.`); return; }
            fetchAndCopyText(copySinglePageBtn, pageNum);
        });
        
        copyRangeBtn.addEventListener('click', (e) => {
            e.preventDefault();
            const startPage = parseInt(pageNumStartInput.value, 10);
            const endPage = parseInt(pageNumEndInput.value, 10);
            if (isNaN(startPage) || isNaN(endPage) || startPage < 1 || endPage > docPageCount || endPage < startPage) { alert(`Please enter a valid page range between 1 and ${docPageCount}.`); return; }
            fetchAndCopyText(copyRangeBtn, startPage, endPage);
        });

        // NEW: Add event listeners for the new buttons
        if (copyAssistantSingleBtn) {
            copyAssistantSingleBtn.addEventListener('click', (e) => {
                e.preventDefault();
                const pageNum = parseInt(pageNumSingleInput.value, 10);
                if (isNaN(pageNum) || pageNum < 1 || pageNum > docPageCount) { alert(`Please enter a valid page number between 1 and ${docPageCount}.`); return; }
                copyAssistantCommand(copyAssistantSingleBtn, `${pageNum}`);
            });
        }

        if (copyAssistantRangeBtn) {
            copyAssistantRangeBtn.addEventListener('click', (e) => {
                e.preventDefault();
                const startPage = parseInt(pageNumStartInput.value, 10);
                const endPage = parseInt(pageNumEndInput.value, 10);
                if (isNaN(startPage) || isNaN(endPage) || startPage < 1 || endPage > docPageCount || endPage < startPage) { alert(`Please enter a valid page range between 1 and ${docPageCount}.`); return; }
                copyAssistantCommand(copyAssistantRangeBtn, `${startPage}-${endPage}`);
            });
        }
    }
});