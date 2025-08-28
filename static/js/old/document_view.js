// --- File: static/js/document_view.js ---

document.addEventListener('DOMContentLoaded', () => {
    // === GLOBAL ELEMENT SELECTORS ===
    const workbenchContainer = document.querySelector('.workbench-container');
    const docId = workbenchContainer.dataset.docId;
    const fileType = workbenchContainer.dataset.fileType;
    const toggleHtmlViewBtn = document.getElementById('toggle-html-view-btn');
    const docViewerIframe = document.getElementById('doc-viewer');
    const docPageCount = parseInt(workbenchContainer.dataset.docPageCount, 10);

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

        // --- START OF THE FIX ---
        // Accordion/collapsing logic for sidebar panels
        sidebar.addEventListener('click', (event) => {
            const panelHeader = event.target.closest('.panel-header');
            if (panelHeader) {
                const panel = panelHeader.closest('.panel');
                if (panel) {
                    panel.classList.toggle('collapsed');
                }
            }
        });
        // --- END OF THE FIX ---
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
                const details = document.createElement('details');
                details.className = 'accordion-item';
                const summary = document.createElement('summary');
                summary.className = 'accordion-header';
                summary.innerHTML = `<span>${label} <span class="chip">${entities.length} unique</span></span>`;
                const body = document.createElement('div');
                body.className = 'accordion-body';
                const list = document.createElement('ul');
                list.className = 'simple-list';
                entities.forEach(entity => {
                    const li = document.createElement('li');
                    const pageLinks = entity.pages.split(',').map(page => `<a href="#page=${page}" class="page-link" data-page="${page}">${page}</a>`).join(', ');
                    li.innerHTML = `<a href="/discover/entity/${encodeURIComponent(entity.label)}/${encodeURIComponent(entity.text)}">${entity.text}</a><span class="text-muted ms-2" style="font-size: 0.9em;">(${entity.appearance_count} on pages: ${pageLinks})</span>`;
                    list.appendChild(li);
                });
                body.appendChild(list);
                details.appendChild(summary);
                details.appendChild(body);
                accordion.appendChild(details);
            }
        });
        discoveryContentContainer.appendChild(accordion);
    }
    
    if (discoveryContentContainer) {
        discoveryContentContainer.addEventListener('click', (e) => {
            if (e.target.classList.contains('page-link')) {
                e.preventDefault();
                const pageNum = e.target.dataset.page;
                if (docViewerIframe && docViewerIframe.contentWindow) {
                    docViewerIframe.contentWindow.postMessage({ type: 'scrollToPage', page: pageNum }, '*');
                }
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

        // MERGED event listener for all messages from the iframe
        window.addEventListener('message', (event) => {
            if (event.source !== docViewerIframe.contentWindow) {
                return;
            }
            // Logic for inserting text into the comment box
            if (event.data.type === 'insertIntoComment' && event.data.text) {
                const currentText = newCommentText.value;
                if (currentText && !currentText.endsWith('\n\n')) {
                    newCommentText.value += '\n\n';
                }
                newCommentText.value += event.data.text + '\n\n';
                newCommentText.focus();
                const curationTabLink = document.querySelector('.tab-link[data-tab="curation"]');
                if (curationTabLink && !curationTabLink.classList.contains('active')) {
                    curationTabLink.click();
                }
            }
            // Logic for updating the current page display
            if (event.data.type === 'pageChanged' && event.data.currentPage) {
                const pageNum = event.data.currentPage;
                if (currentPageDisplay) {
                    const label = (fileType === 'SRT') ? 'Cue' : 'Page';
                    currentPageDisplay.textContent = `Current View: ${label} ${pageNum}`;
                }
                if (pageNumSingleInputForPlaceholder) {
                    pageNumSingleInputForPlaceholder.placeholder = `e.g., ${pageNum}`;
                }
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
            } catch (error) {
                console.error("Failed to load curation data:", error);
            }
        }
        
        function renderComments(comments, currentUser) {
            commentList.innerHTML = '';
            if (comments.length === 0) {
                commentList.innerHTML = '<p class="text-muted">No comments yet.</p>';
                return;
            }
            
            const timestampRegex = /\[(\d{2}:\d{2}:\d{2},\d{3}\s*→\s*\d{2}:\d{2}:\d{2},\d{3})\]\s*([\s\S]*?)(?=\n\[\d{2}:\d{2}:\d{2},\d{3}|\n\n|$)/g;

            comments.forEach(comment => {
                const canDelete = currentUser.role === 'admin' || currentUser.id === comment.user_id;
                const deleteBtnHtml = canDelete ? `<button class="button-delete" data-comment-id="${comment.id}" title="Delete comment">×</button>` : '';
                const commentDate = new Date(comment.created_at).toLocaleString();
                const commentEl = document.createElement('div');
                commentEl.className = 'comment-item';
                
                const escapeHtml = (unsafe) => {
                    return unsafe
                         .replace(/&/g, "&amp;")
                         .replace(/</g, "&lt;")
                         .replace(/>/g, "&gt;")
                         .replace(/"/g, "&quot;")
                         .replace(/'/g, "&#039;");
                };
                
                let processedCommentText = escapeHtml(comment.comment_text);

                processedCommentText = processedCommentText.replace(timestampRegex, (match, timestamp, dialogue) => {
                    const startTime = timestamp.split('→')[0].trim();
                    return `
                        </div> 
                        <div class="comment-quote-timestamp" data-timestamp="${startTime}" title="Click to seek audio">
                            ${timestamp.replace('→', ' → ')}
                        </div>
                        <p class="comment-quote-dialogue">${dialogue.trim()}</p>
                        <div class="comment-body"> 
                    `;
                });

                commentEl.innerHTML = `
                    <div class="comment-header"><strong>${comment.username}</strong>${deleteBtnHtml}</div>
                    <div class="comment-body">${processedCommentText}</div>
                    <small class="text-muted">${commentDate}</small>
                `;
                commentList.appendChild(commentEl);
            });
        }

        function renderTags(tags) {
            tagContainer.innerHTML = '';
            tags.forEach(tag => {
                const tagEl = document.createElement('span');
                tagEl.className = 'tag-item';
                tagEl.textContent = tag;
                const removeBtn = document.createElement('button');
                removeBtn.className = 'tag-remove-btn';
                removeBtn.innerHTML = '×';
                removeBtn.dataset.tag = tag;
                tagEl.appendChild(removeBtn);
                tagContainer.appendChild(tagEl);
            });
        }

        function renderCatalogs(allCatalogs, memberOf, isFavorite) {
            favoritesCatalogId = allCatalogs.find(c => c.name === '⭐ Favorites')?.id;
            favoriteToggle.checked = isFavorite;
            catalogListContainer.innerHTML = '';
            allCatalogs.filter(c => c.id !== favoritesCatalogId).forEach(catalog => {
                const isMember = memberOf.includes(catalog.id);
                const catalogEl = document.createElement('div');
                catalogEl.className = 'form-check';
                catalogEl.innerHTML = `<input type="checkbox" class="form-check-input catalog-checkbox" id="cat-${catalog.id}" data-cat-id="${catalog.id}" ${isMember ? 'checked' : ''}><label class="form-check-label" for="cat-${catalog.id}">${catalog.name}</label>`;
                catalogListContainer.appendChild(catalogEl);
            });
        }
        
        async function saveTags(tags) {
            await fetch(`/api/document/${docId}/tags`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ tags: tags }) });
            await loadCurationData();
        }
        
        async function updateCatalogMembership() {
            const memberOf = Array.from(catalogListContainer.querySelectorAll('.catalog-checkbox:checked')).map(cb => parseInt(cb.dataset.catId));
            if (favoriteToggle.checked && favoritesCatalogId && !memberOf.includes(favoritesCatalogId)) {
                memberOf.push(favoritesCatalogId);
            }
            await fetch(`/api/document/${docId}/catalogs`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ catalog_ids: memberOf }) });
        }

        if (colorPalette) {
            const initialColor = workbenchContainer.dataset.docColor;
            if(initialColor) {
                const selected = colorPalette.querySelector(`[data-color="${initialColor}"]`);
                if(selected) selected.classList.add('selected');
            }
            colorPalette.addEventListener('click', (e) => {
                const colorSpan = e.target.closest('.palette-color');
                if (!colorSpan) return;
                const color = colorSpan.dataset.color;
                fetch(`/api/document/${docId}/color`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN },
                    body: JSON.stringify({ color: color })
                }).then(() => {
                    colorPalette.querySelectorAll('.palette-color').forEach(el => el.classList.remove('selected'));
                    colorSpan.classList.add('selected');
                });
            });
        }

        if (saveNoteBtn) {
            saveNoteBtn.addEventListener('click', () => {
                fetch(`/api/document/${docId}/curation`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN },
                    body: JSON.stringify({ note: noteTextarea.value })
                }).then(() => {
                    saveNoteBtn.textContent = 'Saved!';
                    setTimeout(() => { saveNoteBtn.textContent = 'Save Note'; }, 2000);
                });
            });
        }
        
        if (postCommentBtn) {
            postCommentBtn.addEventListener('click', async () => {
                const text = newCommentText.value.trim();
                if (!text) return;
                const response = await fetch(`/api/document/${docId}/comments`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ comment_text: text }) });
                if (response.ok) { newCommentText.value = ''; await loadCurationData(); }
            });
        }

        if (commentList) {
            commentList.addEventListener('click', async (e) => {
                const quoteTimestamp = e.target.closest('.comment-quote-timestamp');
                if (quoteTimestamp && fileType === 'SRT') {
                    const timestamp = quoteTimestamp.dataset.timestamp;
                    if (timestamp && docViewerIframe.contentWindow) {
                        docViewerIframe.contentWindow.postMessage({
                            type: 'seekToTimestamp',
                            timestamp: timestamp
                        }, '*');
                    }
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
                    e.preventDefault();
                    const newTag = tagInput.value.trim().toLowerCase();
                    if (!newTag) return;
                    const currentTags = Array.from(tagContainer.querySelectorAll('.tag-item')).map(el => el.textContent.slice(0, -1));
                    if (!currentTags.includes(newTag)) {
                        saveTags([...currentTags, newTag]);
                        tagInput.value = '';
                    }
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
                const catalogName = newCatalogNameInput.value.trim();
                if (!catalogName) {
                    alert('Please enter a name for the new catalog.');
                    return;
                }
                try {
                    const response = await fetch('/api/catalogs', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN },
                        body: JSON.stringify({ name: catalogName })
                    });

                    const result = await response.json();
                    if (!response.ok) {
                        throw new Error(result.message || 'Failed to create catalog.');
                    }
                    newCatalogNameInput.value = '';
                    await loadCurationData();
                    setTimeout(async () => {
                        const newCatalogCheckbox = catalogListContainer.querySelector(`.catalog-checkbox[id="cat-${result.catalog.id}"]`);
                        if (newCatalogCheckbox) {
                            newCatalogCheckbox.checked = true;
                            await updateCatalogMembership();
                        }
                    }, 100);
                } catch (error) {
                    alert(`Error: ${error.message}`);
                }
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
            const types = { 
                'report': 'Report', 
                'article-journal': 'Journal Article', 
                'book': 'Book', 
                'webpage': 'Webpage', 
                'broadcast': 'TV/Radio Broadcast', 
                'interview': 'Interview/Podcast', 
                'motion_picture': 'Movie / Short Film',
                'entry-encyclopedia': 'Encyclopedia Entry', 
                'chapter': 'Book Chapter', 
                'manuscript': 'Manuscript', 
                'bill': 'Bill / Legislation' 
            };
            typeSelect.innerHTML = ''; 
            for (const [value, text] of Object.entries(types)) typeSelect.add(new Option(text, value)); 
        }

        function updateFormFields() {
            const selectedType = typeSelect.value;
            document.querySelectorAll('.csl-field-specific').forEach(field => {
                const visibleFor = field.dataset.cslType.split(' ');
                field.style.display = visibleFor.includes(selectedType) ? 'block' : 'none';
            });
            const containerTitleLabel = document.querySelector('label[for="csl-container-title"]');
            const publisherLabel = document.querySelector('label[for="csl-publisher"]');
            if (selectedType === 'article-journal') { containerTitleLabel.textContent = 'Journal Title'; } 
            else if (selectedType === 'broadcast') { containerTitleLabel.textContent = 'Program / Series Title'; publisherLabel.textContent = 'Network / Station'; }
            else if (selectedType === 'interview') { containerTitleLabel.textContent = 'Podcast / Publication Series'; }
            else if (selectedType === 'motion_picture') { 
                publisherLabel.textContent = 'Studio / Distributor'; 
            }
            else { publisherLabel.textContent = 'Publisher / Agency'; }
        }

        function populateForm(cslData) { 
            const data = cslData || {}; 
            typeSelect.value = data.type || 'report'; 
            const docTitleElement = document.querySelector('.document-file-name'); 
            document.getElementById('csl-title').value = data.title || (docTitleElement ? docTitleElement.textContent : ''); 

            document.getElementById('csl-author').value = (data.author || [])
                .map(a => {
                    if (a.literal) {
                        return a.literal;
                    }
                    return `${a.family || ''}, ${a.given || ''}`.trim().replace(/^,|,$/g, '');
                }).join('; ');
            
            const yearSelect = document.getElementById('csl-date-year');
            const monthSelect = document.getElementById('csl-date-month');
            const daySelect = document.getElementById('csl-date-day');
            if (data.issued && data.issued['date-parts'] && data.issued['date-parts'][0]) { 
                const [year, month, day] = data.issued['date-parts'][0]; 
                yearSelect.value = year || ''; 
                monthSelect.value = month || ''; 
                daySelect.value = day || ''; 
            } else { 
                yearSelect.value = ''; monthSelect.value = ''; daySelect.value = ''; 
            } 
            document.getElementById('csl-publisher').value = data.publisher || ''; 
            document.getElementById('csl-container-title').value = data['container-title'] || ''; 
            document.getElementById('csl-url').value = data.URL || ''; 
            updateFormFields(); 
        }
        
        function buildCslFromForm() {
            const csl = { id: `doc-${docId}`, type: typeSelect.value };
            const title = document.getElementById('csl-title').value.trim();
            if (title) csl.title = title;

            const authorString = document.getElementById('csl-author').value.trim();
            if (authorString) {
                csl.author = authorString.split(';').map(name => {
                    return { 'literal': name.trim() };
                }).filter(a => a.literal);
            }

            const dateParts = [document.getElementById('csl-date-year').value, document.getElementById('csl-date-month').value, document.getElementById('csl-date-day').value].filter(Boolean).map(p => parseInt(p, 10));
            if (dateParts.length > 0) csl.issued = { 'date-parts': [dateParts] };
            const publisher = document.getElementById('csl-publisher').value.trim();
            if (['report', 'broadcast', 'interview', 'motion_picture'].includes(csl.type) && publisher) { csl.publisher = publisher; }
            const containerTitle = document.getElementById('csl-container-title').value.trim();
            if (['article-journal', 'broadcast', 'interview'].includes(csl.type) && containerTitle) { csl['container-title'] = containerTitle; }
            const url = document.getElementById('csl-url').value.trim();
            if (['webpage', 'broadcast', 'interview', 'motion_picture'].includes(csl.type) && url) { csl.URL = url; }
            return csl;
        }

        async function loadMetadata() { 
            try { 
                const response = await fetch(`/api/document/${docId}/metadata`); 
                const data = await response.json(); 
                populateForm(data.csl_json ? JSON.parse(data.csl_json) : null); 
            } catch (error) { 
                saveStatus.textContent = "Error loading data."; 
                saveStatus.style.opacity = '1'; 
            } 
        }
        
        saveBtn.addEventListener('click', async () => { 
            const newCslData = buildCslFromForm(); 
            const cslJsonString = JSON.stringify(newCslData, null, 2); 
            saveBtn.disabled = true; 
            saveStatus.textContent = "Saving..."; 
            saveStatus.style.opacity = '1'; 
            try { 
                const response = await fetch(`/api/document/${docId}/metadata`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ csl_json: cslJsonString }) }); 
                const result = await response.json(); 
                if (!response.ok) throw new Error(result.message); 
                saveStatus.textContent = "Saved successfully."; 
                setTimeout(() => { saveStatus.style.opacity = '0'; }, 2000); 
            } catch (error) { 
                saveStatus.textContent = `Error: ${error.message}`; 
            } finally { 
                saveBtn.disabled = false; 
            } 
        });
        
        typeSelect.addEventListener('change', updateFormFields);
        populateDocTypeSelect(); 
        populateYearSelect(); 
        populateMonthSelect(); 
        populateDaySelect(); 
        loadMetadata();
    }
    
    // --- MEDIA LINK LOGIC FOR SRT FILES ---
    const mediaSyncPanel = document.getElementById('media-sync-panel');
    const xmlSyncPanel = document.getElementById('xml-sync-panel');

    if (mediaSyncPanel && fileType === 'SRT') {
        const renderLinkedState = (type, path) => {
            const mediaType = type.charAt(0).toUpperCase() + type.slice(1);
            mediaSyncPanel.innerHTML = `<p class="text-muted" style="color: #4ADE80;">✅ ${mediaType} is linked.</p><code style="font-size: 0.9em; word-break: break-all;">${path}</code><button id="unlink-media-btn" class="button button-danger button-small mt-2">Unlink Media</button>`;
            if (xmlSyncPanel) {
                const scanBtn = xmlSyncPanel.querySelector('#scan-xml-btn');
                if (scanBtn) scanBtn.disabled = false;
                const statusEl = xmlSyncPanel.querySelector('#xml-link-status');
                if(statusEl && statusEl.dataset.wasDisabled) {
                    statusEl.innerHTML = '';
                    delete statusEl.dataset.wasDisabled;
                }
            }
        };

        const renderUnlinkedState = () => {
            mediaSyncPanel.innerHTML = `<p class="text-muted">Link an audio or video file with the same name to enable interactive playback.</p>
            <div class="toolbar" style="gap: 0.5rem; justify-content: flex-start;">
                <button id="scan-audio-btn" class="button">Scan for Audio (.mp3)</button>
                <button id="scan-video-btn" class="button">Scan for Video (.mp4)</button>
            </div>
            <p id="media-link-status" class="text-muted mt-2" style="transition: opacity 0.3s; opacity: 0;"></p>`;
            if (xmlSyncPanel) {
                const scanBtn = xmlSyncPanel.querySelector('#scan-xml-btn');
                if (scanBtn) scanBtn.disabled = true;
                const statusEl = xmlSyncPanel.querySelector('#xml-link-status');
                if(statusEl) {
                    statusEl.innerHTML = '<p class="text-muted"><em>Please sync a media file first to enable XML metadata scanning.</em></p>';
                    statusEl.dataset.wasDisabled = true;
                }
            }
        };

        const checkMediaStatus = async () => {
            try {
                const response = await fetch(`/api/document/${docId}/media_status`);
                const result = await response.json();
                if (result.linked) {
                    renderLinkedState(result.type, result.path);
                } else {
                    renderUnlinkedState();
                }
            } catch (error) {
                mediaSyncPanel.innerHTML = '<p class="text-danger">Could not check media status.</p>';
            }
        };

        mediaSyncPanel.addEventListener('click', async (event) => {
            const target = event.target;
            const statusEl = document.getElementById('media-link-status');

            const handleScan = async (mediaType) => {
                const scanBtn = document.getElementById(`scan-${mediaType}-btn`);
                if (scanBtn) scanBtn.disabled = true;
                if (statusEl) {
                    statusEl.textContent = `Scanning for ${mediaType}...`;
                    statusEl.style.opacity = '1';
                }
                try {
                    const response = await fetch(`/api/document/${docId}/find_${mediaType}`, { method: 'POST', headers: { 'X-CSRFToken': CSRF_TOKEN } });
                    const result = await response.json();
                    if (result.success) {
                        if(statusEl) statusEl.textContent = `✅ ${mediaType.charAt(0).toUpperCase() + mediaType.slice(1)} found! Viewer will reload.`;
                        docViewerIframe.contentWindow.postMessage({ type: 'loadMedia', url: result.media_url, mediaType: mediaType }, '*');
                        await checkMediaStatus();
                    } else {
                        if(statusEl) statusEl.textContent = `❌ ${result.message}`;
                    }
                } catch (error) { 
                    if(statusEl) statusEl.textContent = `Error during ${mediaType} scan.`;
                } finally {
                    setTimeout(() => {
                        if (statusEl) statusEl.style.opacity = '0';
                        if (scanBtn) scanBtn.disabled = false;
                    }, 4000);
                }
            };

            if (target.id === 'scan-audio-btn') { await handleScan('audio'); }
            if (target.id === 'scan-video-btn') { await handleScan('video'); }
            
            if (target.id === 'unlink-media-btn') {
                if (!confirm('Are you sure you want to unlink this media file? The player will be removed.')) return;
                target.disabled = true;
                target.textContent = 'Unlinking...';
                try {
                    const response = await fetch(`/api/document/${docId}/unlink_media`, { method: 'POST', headers: { 'X-CSRFToken': CSRF_TOKEN } });
                    const result = await response.json();
                    if (!result.success) throw new Error(result.message);
                    docViewerIframe.src = docViewerIframe.src; 
                    await checkMediaStatus();
                } catch (error) {
                    alert(`Error: ${error.message}`);
                    target.disabled = false;
                    target.textContent = 'Unlink Media';
                }
            }
        });

        checkMediaStatus();
    }
    
    // --- XML METADATA SYNC LOGIC ---
    if (xmlSyncPanel && fileType === 'SRT') {
        const statusContainer = document.getElementById('xml-link-status');
        
        const renderXmlMatchList = (result) => {
            const matches = result.matches;
            const scannedCount = result.xml_files_scanned;

            if (matches.length === 0) {
                if (scannedCount > 0) {
                    statusContainer.innerHTML = `<p class="text-muted">Scanned ${scannedCount} XML file(s), but no potential episode entries were found.</p>`;
                } else {
                    statusContainer.innerHTML = '<p class="text-muted">No .xml files were found in your documents directory to scan.</p>';
                }
                return;
            }

            let listHtml = `<p>Found ${matches.length} potential match(es) from ${scannedCount} XML file(s). Click to import:</p><ul class="simple-list">`;
            matches.forEach(match => {
                const preview = match.preview;
                const title = preview.title || '<em>No Title</em>';
                const author = preview.author ? (preview.author[0].literal || '') : '<em>No Author</em>';
                const date = preview.issued ? preview.issued['date-parts'][0].join('-') : '<em>No Date</em>';

                listHtml += `
                    <li style="padding: 0.75rem; border: 1px solid var(--border-color); border-radius: 4px; margin-bottom: 0.5rem;">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <div>
                                <strong>${title}</strong>
                                <p class="text-muted mb-0" style="font-size: 0.9em;">
                                    By ${author} on ${date} <br>
                                    <code style="font-size: 0.8em;">Found in: ${match.xml_path}</code>
                                </p>
                            </div>
                            <button class="button button-small import-xml-btn" data-xml-path="${match.xml_path}" data-item-hash="${match.item_hash}">
                                Import
                            </button>
                        </div>
                    </li>
                `;
            });
            listHtml += '</ul>';
            statusContainer.innerHTML = listHtml;
        };

        const renderStatusUpdate = (message, isError = false) => {
            statusContainer.innerHTML = `<p class="${isError ? 'text-danger' : 'text-muted'}">${message}</p>`;
        };

        xmlSyncPanel.addEventListener('click', async (event) => {
            const target = event.target;
            if (target.id === 'scan-xml-btn') {
                target.disabled = true;
                target.textContent = 'Scanning...';
                renderStatusUpdate('Scanning filesystem...');
                try {
                    const response = await fetch(`/api/document/${docId}/find_metadata_xml`, {
                        method: 'POST',
                        headers: { 'X-CSRFToken': CSRF_TOKEN }
                    });
                    const result = await response.json();
                    if (result.success) {
                        renderXmlMatchList(result);
                    } else {
                        renderStatusUpdate(`❌ ${result.message}`, true);
                    }
                } catch (error) {
                    console.error("Scan failed:", error);
                    renderStatusUpdate('A network error occurred during the scan.', true);
                } finally {
                    target.disabled = false;
                    target.textContent = 'Scan for XML File';
                }
            }

            if (target.classList.contains('import-xml-btn')) {
                const xmlPath = target.dataset.xmlPath;
                const itemHash = target.dataset.itemHash;

                if (!confirm(`Are you sure you want to import this episode's metadata?\n\nThis will overwrite any existing bibliographic data.`)) {
                    return;
                }
                target.disabled = true;
                target.textContent = 'Importing...';
                try {
                    const response = await fetch(`/api/document/${docId}/import_from_xml`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN },
                        body: JSON.stringify({ xml_path: xmlPath, item_hash: itemHash })
                    });
                    const result = await response.json();
                    
                    if (result.success && result.csl_json) {
                        renderStatusUpdate('✅ Success! Metadata has been imported and the form updated.', false);
                        
                        populateForm(JSON.parse(result.csl_json));

                        const metadataTabLink = document.querySelector('.tab-link[data-tab="metadata"]');
                        if (metadataTabLink) {
                            metadataTabLink.click();
                        }

                    } else {
                        renderStatusUpdate(`❌ ${result.message || 'Import failed.'}`, true);
                        target.disabled = false;
                        target.textContent = 'Import';
                    }
                } catch (error) {
                    console.error("Import failed:", error);
                    renderStatusUpdate('A network error occurred during import.', true);
                    target.disabled = false;
                    target.textContent = 'Import';
                }
            }
        });
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

        // Helper function to call the API and copy the result
        const fetchAndCopyText = async (buttonElement, startPage = null, endPage = null) => {
            const originalText = buttonElement.textContent;
            buttonElement.textContent = 'Copying...';
            buttonElement.disabled = true;

            let url = `/api/document/${docId}/text`;
            const params = new URLSearchParams();
            if (startPage !== null) params.append('start_page', startPage);
            if (endPage !== null) params.append('end_page', endPage);
            
            const queryString = params.toString();
            if (queryString) {
                url += `?${queryString}`;
            }

            try {
                const response = await fetch(url);
                if (!response.ok) {
                    throw new Error('Network response was not ok.');
                }
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
                // Reset the button after a delay
                setTimeout(() => {
                    buttonElement.textContent = originalText;
                    buttonElement.disabled = false;
                    copyTextMenu.classList.remove('show'); // Hide menu on success/error
                }, 2000);
            }
        };

        // --- Event Listeners ---

        // Toggle dropdown visibility
        copyTextBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            copyTextMenu.classList.toggle('show');
        });

        // Hide dropdown if clicking outside
        document.addEventListener('click', (e) => {
            if (!copyTextMenu.contains(e.target) && !copyTextBtn.contains(e.target)) {
                copyTextMenu.classList.remove('show');
            }
        });

        // Copy All Text
        copyAllBtn.addEventListener('click', (e) => {
            e.preventDefault();
            fetchAndCopyText(copyAllBtn);
        });

        // Copy Single Page
        copySinglePageBtn.addEventListener('click', (e) => {
            e.preventDefault();
            const pageNum = parseInt(pageNumSingleInput.value, 10);
            if (isNaN(pageNum) || pageNum < 1 || pageNum > docPageCount) {
                alert(`Please enter a valid page number between 1 and ${docPageCount}.`);
                return;
            }
            fetchAndCopyText(copySinglePageBtn, pageNum);
        });

        // Copy Page Range
        copyRangeBtn.addEventListener('click', (e) => {
            e.preventDefault();
            const startPage = parseInt(pageNumStartInput.value, 10);
            const endPage = parseInt(pageNumEndInput.value, 10);

            if (isNaN(startPage) || isNaN(endPage) || startPage < 1 || endPage > docPageCount || endPage < startPage) {
                alert(`Please enter a valid page range between 1 and ${docPageCount}.`);
                return;
            }
            fetchAndCopyText(copyRangeBtn, startPage, endPage);
        });
    }
});